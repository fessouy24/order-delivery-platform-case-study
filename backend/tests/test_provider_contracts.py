import pytest

from apps.operations.providers import (
    IntegrationError,
    NoProviderRedirects,
    Odoo,
    Shopify,
    TransientError,
    UnknownCreateResult,
)


def configure_odoo(monkeypatch, version):
    for name, value in {
        "ODOO_BASE_URL": "https://odoo.example.invalid",
        "ODOO_API_KEY": "contract-test-only",
        "ODOO_DATABASE": "test-db",
        "ODOO_LOGIN": "integration",
        "ODOO_VERSION": version,
    }.items():
        monkeypatch.setenv(name, value)


def test_odoo19_create_uses_json2_vals_list_and_returns_record_id(monkeypatch):
    configure_odoo(monkeypatch, "19")
    calls = []

    def fake_request(url, payload, headers):
        calls.append((url, payload, headers))
        return [42]

    monkeypatch.setattr("apps.operations.providers.request_json", fake_request)
    assert Odoo().create({"partner_id": 1}) == 42
    assert calls[0][0].endswith("/json/2/sale.order/create")
    assert calls[0][1] == {"vals_list": [{"partner_id": 1}]}
    assert calls[0][2]["X-Odoo-Database"] == "test-db"


@pytest.mark.parametrize("version", ["17", "18"])
def test_odoo_xmlrpc_create_uses_values_and_model(monkeypatch, version):
    configure_odoo(monkeypatch, version)
    calls = []

    class FakeProxy:
        def authenticate(self, *args):
            return 7

        def execute_kw(self, *args):
            calls.append(args)
            return 42

    monkeypatch.setattr(
        "apps.operations.providers.xmlrpc.client.ServerProxy", lambda *args, **kwargs: FakeProxy()
    )
    assert Odoo().create({"partner_id": 1}) == 42
    assert calls[0][3:6] == ("sale.order", "create", [{"partner_id": 1}])


def test_odoo17_write_targets_only_explicit_record_ids(monkeypatch):
    configure_odoo(monkeypatch, "17")
    calls = []

    class FakeProxy:
        def authenticate(self, *args):
            return 7

        def execute_kw(self, *args):
            calls.append(args)
            return True

    monkeypatch.setattr(
        "apps.operations.providers.xmlrpc.client.ServerProxy", lambda *a, **k: FakeProxy()
    )
    assert Odoo().call("sale.order", "write", ids=[902], vals={"client_order_ref": "#9001"}) is True
    assert calls[0][3:6] == ("sale.order", "write", [[902], {"client_order_ref": "#9001"}])


def test_quotation_lookup_preserves_marker_after_human_reference_change(monkeypatch):
    configure_odoo(monkeypatch, "19")
    calls = []
    monkeypatch.setattr(
        "apps.operations.providers.request_json",
        lambda url, payload, headers: calls.append(payload) or [],
    )
    assert Odoo().find("unique-marker") == []
    assert calls[0]["domain"] == [
        "|",
        ["client_order_ref", "=", "unique-marker"],
        ["origin", "=", "unique-marker"],
    ]


def test_shopify_short_lived_token_renews_before_expiry(monkeypatch):
    monkeypatch.setenv("SHOPIFY_SHOP_DOMAIN", "test.myshopify.com")
    monkeypatch.setenv("SHOPIFY_API_VERSION", "2026-07")
    monkeypatch.setenv("SHOPIFY_CLIENT_ID", "test-id")
    monkeypatch.setenv("SHOPIFY_CLIENT_SECRET", "test-only")
    monkeypatch.delenv("SHOPIFY_ACCESS_TOKEN", raising=False)
    requests = []

    def request(*args, **kwargs):
        requests.append(args)
        return {"access_token": "short-lived-test-only", "expires_in": 86400}

    monkeypatch.setattr("apps.operations.providers.request_json", request)
    adapter = Shopify()
    adapter.token()
    adapter.token()
    assert len(requests) == 1
    adapter.expires_at = 0
    adapter.token()
    assert len(requests) == 2


def test_authenticated_provider_request_cannot_follow_redirect_to_another_host():
    with pytest.raises(IntegrationError, match="canonical"):
        NoProviderRedirects().redirect_request(
            None, None, 302, "Moved", {}, "https://untrusted.invalid/"
        )


@pytest.mark.parametrize(
    "result",
    [
        None,
        {},
        {"access_token": "", "expires_in": 86400},
        {"access_token": "test", "expires_in": True},
        {"access_token": "test", "expires_in": 30},
    ],
)
def test_invalid_shopify_token_is_retryable(monkeypatch, result):
    monkeypatch.setenv("SHOPIFY_SHOP_DOMAIN", "test.myshopify.com")
    monkeypatch.setenv("SHOPIFY_API_VERSION", "2026-07")
    monkeypatch.setenv("SHOPIFY_CLIENT_ID", "test-id")
    monkeypatch.setenv("SHOPIFY_CLIENT_SECRET", "test-only")
    monkeypatch.delenv("SHOPIFY_ACCESS_TOKEN", raising=False)
    monkeypatch.setattr("apps.operations.providers.request_json", lambda *a, **k: result)
    with pytest.raises(TransientError):
        Shopify().token()


@pytest.mark.parametrize("result", [None, [], [1, 2], True, 0, -1, "42", [False]])
def test_invalid_odoo_create_id_requires_reconciliation(monkeypatch, result):
    configure_odoo(monkeypatch, "19")
    monkeypatch.setattr("apps.operations.providers.request_json", lambda *a, **k: result)
    with pytest.raises(UnknownCreateResult):
        Odoo().create({"partner_id": 1})
