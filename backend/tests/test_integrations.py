import base64
import hashlib
import hmac
import json
from datetime import date
from decimal import Decimal as D

import pytest
from rest_framework.exceptions import ValidationError

from apps.operations import inventory, orders
from apps.operations.integrations import import_shopify, sync_odoo
from apps.operations.models import (
    ExternalMapping,
    IntegrationJob,
    Order,
    ShopifyReference,
    WebhookInbox,
)
from apps.operations.providers import IntegrationError, UnknownCreateResult
from tests.scenarios import stocked_product

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def integration_day(monkeypatch):
    monkeypatch.setattr("apps.operations.integrations.business_today", lambda: date(2026, 10, 4))


def shopify_node(sku):
    return {
        "id": "gid://shopify/Order/1",
        "name": "#1001",
        "updatedAt": "2026-10-04T10:00:00Z",
        "createdAt": "2026-10-04T09:00:00Z",
        "cancelledAt": None,
        "note": "Deliver after calling",
        "tags": ["delivery-today"],
        "customAttributes": [],
        "currencyCode": "EGP",
        "displayFinancialStatus": "PAID",
        "currentTotalPriceSet": {"shopMoney": {"amount": "10", "currencyCode": "EGP"}},
        "totalOutstandingSet": {"shopMoney": {"amount": "0", "currencyCode": "EGP"}},
        "customer": {"id": "gid://shopify/Customer/1", "displayName": "Customer"},
        "shippingAddress": {
            "name": "Customer",
            "phone": "+12025550100",
            "address1": "1 Example Street",
            "city": "Cairo",
        },
        "lineItems": {
            "pageInfo": {"hasNextPage": False},
            "nodes": [
                {
                    "sku": sku,
                    "name": "Product",
                    "quantity": 1,
                    "variant": {"id": "gid://shopify/ProductVariant/1", "sku": sku},
                    "originalUnitPriceSet": {"shopMoney": {"amount": "10", "currencyCode": "EGP"}},
                }
            ],
        },
    }


def test_shopify_import_preserves_mapping_and_deduplicates(setup_order, monkeypatch):
    _, location, _, _, _ = setup_order
    item = stocked_product(location)
    monkeypatch.setenv("SHOPIFY_LOCATION_ID", str(location.pk))
    node = shopify_node(item.sku)
    first = import_shopify(node, "test.myshopify.com")
    again = import_shopify(node, "test.myshopify.com")
    assert first.pk == again.pk
    assert (
        Order.objects.count()
        == ShopifyReference.objects.count()
        == IntegrationJob.objects.count()
        == 1
    )
    assert first.expected_collection == 0
    assert first.source == "SHOPIFY"
    assert first.full_address == "1 Example Street, Cairo"


def test_shopify_unmapped_sku_rolls_back_without_false_order(setup_order, monkeypatch):
    _, location, _, _, _ = setup_order
    monkeypatch.setenv("SHOPIFY_LOCATION_ID", str(location.pk))
    with pytest.raises(IntegrationError, match="unmapped"):
        import_shopify(shopify_node("unknown"), "test.myshopify.com")
    assert not Order.objects.exists()


def test_shopify_hmac_and_durable_event_deduplication(client, monkeypatch):
    monkeypatch.setenv("SHOPIFY_CLIENT_SECRET", "test-webhook-secret")
    monkeypatch.setenv("SHOPIFY_SHOP_DOMAIN", "test.myshopify.com")
    raw = json.dumps({"id": 1}).encode()
    signature = base64.b64encode(
        hmac.new(b"test-webhook-secret", raw, hashlib.sha256).digest()
    ).decode()
    headers = {
        "HTTP_X_SHOPIFY_HMAC_SHA256": signature,
        "HTTP_X_SHOPIFY_SHOP_DOMAIN": "test.myshopify.com",
        "HTTP_X_SHOPIFY_TOPIC": "orders/create",
        "HTTP_X_SHOPIFY_EVENT_ID": "same-event",
    }
    assert (
        client.post(
            "/api/v1/integrations/shopify/webhook/", raw, content_type="application/json", **headers
        ).status_code
        == 202
    )
    assert (
        client.post(
            "/api/v1/integrations/shopify/webhook/", raw, content_type="application/json", **headers
        ).status_code
        == 202
    )
    assert WebhookInbox.objects.count() == 1
    headers["HTTP_X_SHOPIFY_HMAC_SHA256"] = "bad"
    assert (
        client.post(
            "/api/v1/integrations/shopify/webhook/", raw, content_type="application/json", **headers
        ).status_code
        == 401
    )


class FakeOdoo:
    def __init__(self, timeout=False):
        self.calls = 0
        self.created = []
        self.timeout = timeout

    def find(self, key):
        if not self.created:
            return []
        total = sum(
            command[2]["price_unit"] * command[2]["product_uom_qty"]
            for command in self.created[0]["order_line"]
        )
        return [
            {
                "id": 42,
                "name": "S00042",
                "amount_total": total,
                "currency_id": [1, "EGP"],
                "state": "draft",
            }
        ]

    def create(self, values):
        self.calls += 1
        self.created.append(values)
        if self.timeout:
            raise UnknownCreateResult("The provider committed but its response was lost.")
        return 42


def map_order(order):
    for entity_type, entity_id, external_id in [
        ("customer", order.customer_id, "1"),
        ("address", order.address_id, "2"),
    ]:
        ExternalMapping.objects.create(
            provider="ODOO", entity_type=entity_type, entity_id=entity_id, external_id=external_id
        )
    for line in order.lines.all():
        ExternalMapping.objects.create(
            provider="ODOO", entity_type="item", entity_id=line.item_id, external_id="3"
        )


def test_odoo_draft_and_duplicate_prevention(setup_order):
    _, location, _, _, make = setup_order
    order = make([(stocked_product(location), 1)])
    map_order(order)
    provider = FakeOdoo()
    first = sync_odoo(order.odoo.pk, provider)
    second = sync_odoo(order.odoo.pk, provider)
    assert first.status == second.status == "SYNCED"
    assert first.external_number == "S00042"
    assert provider.calls == 1
    assert "state" not in provider.created[0]
    assert provider.created[0]["partner_shipping_id"] == 2


def test_website_quotation_uses_shared_cash_customer_and_visible_order_reference(
    setup_order, monkeypatch
):
    from apps.operations.models import Order, ShopifyReference

    _, location, _, _, make = setup_order
    order = make([(stocked_product(location), 1)])
    order.source = Order.Source.SHOPIFY
    order.save(update_fields=["source"])
    ShopifyReference.objects.create(
        order=order,
        store="sample.myshopify.com",
        external_id="gid://shopify/Order/9001",
        order_number="#9001",
    )
    ExternalMapping.objects.create(
        provider="ODOO", entity_type="item", entity_id=order.lines.first().item_id, external_id="3"
    )
    monkeypatch.setenv("ODOO_WEBSITE_PARTNER_ID", "803")
    adapter = FakeOdoo()
    assert sync_odoo(order.odoo.pk, adapter).status == "SYNCED"
    values = adapter.created[0]
    assert (
        values["partner_id"] == values["partner_invoice_id"] == values["partner_shipping_id"] == 803
    )
    assert values["client_order_ref"] == "#9001"
    assert values["origin"] == f"OPSDEMO:{order.pk}"
    assert sync_odoo(order.odoo.pk, adapter).status == "SYNCED"
    assert adapter.calls == 1


def test_odoo_timeout_after_commit_reconciles_without_second_create(setup_order):
    _, location, _, _, make = setup_order
    order = make([(stocked_product(location), 1)])
    map_order(order)
    provider = FakeOdoo(timeout=True)
    assert sync_odoo(order.odoo.pk, provider).status == "UNKNOWN"
    assert sync_odoo(order.odoo.pk, provider).status == "SYNCED"
    assert provider.calls == 1


def test_odoo_unknown_without_found_record_never_recreates(setup_order):
    _, location, _, _, make = setup_order
    order = make([(stocked_product(location), 1)])
    map_order(order)
    IntegrationJob.objects.filter(order=order).update(status="UNKNOWN")
    provider = FakeOdoo()
    assert sync_odoo(order.odoo.pk, provider).status == "UNKNOWN"
    assert provider.calls == 0
    assert Order.objects.filter(pk=order.pk).exists()


def test_odoo_mapping_failure_is_visible_and_internal_order_survives(setup_order):
    _, location, _, _, make = setup_order
    order = make([(stocked_product(location), 1)])
    job = sync_odoo(order.odoo.pk, FakeOdoo())
    assert job.status == "ERROR"
    assert "mapping" in job.error
    assert Order.objects.filter(pk=order.pk).exists()
    assert order.alert_set.filter(kind="ODOO_SYNC_FAILURE").exists()


def test_odoo_currency_mismatch_never_reports_success_or_recreates(setup_order):
    _, location, _, _, make = setup_order
    order = make([(stocked_product(location), 1)])
    map_order(order)

    class WrongCurrency(FakeOdoo):
        def find(self, key):
            rows = super().find(key)
            if rows:
                rows[0]["currency_id"] = [2, "USD"]
            return rows

    adapter = WrongCurrency()
    assert sync_odoo(order.odoo.pk, adapter).status == "UNKNOWN"
    assert sync_odoo(order.odoo.pk, adapter).status == "UNKNOWN"
    assert adapter.calls == 1


def test_upstream_quantity_change_holds_order_and_prevents_quotation(setup_order, monkeypatch):
    owner, location, _, _, _ = setup_order
    item = stocked_product(location)
    monkeypatch.setenv("SHOPIFY_LOCATION_ID", str(location.pk))
    node = shopify_node(item.sku)
    order = import_shopify(node, "test.myshopify.com")
    node["updatedAt"] = "2026-10-04T11:00:00Z"
    node["lineItems"]["nodes"][0]["quantity"] = 2
    node["currentTotalPriceSet"]["shopMoney"]["amount"] = "20"
    order = import_shopify(node, "test.myshopify.com")
    assert order.review_required
    assert inventory.material_report([order]) == []
    with pytest.raises(ValidationError, match="review"):
        orders.ready(order.pk, order.version, owner)
    adapter = FakeOdoo()
    assert sync_odoo(order.odoo.pk, adapter).status == "PENDING"
    assert adapter.calls == 0


def test_payment_after_dispatch_requires_finance_approval(
    setup_order, monkeypatch, user_factory, api
):
    owner, location, _, _, _ = setup_order
    item = stocked_product(location)
    monkeypatch.setenv("SHOPIFY_LOCATION_ID", str(location.pk))
    node = shopify_node(item.sku)
    node["displayFinancialStatus"] = "PENDING"
    node["totalOutstandingSet"]["shopMoney"]["amount"] = "10"
    order = import_shopify(node, "test.myshopify.com")
    courier = user_factory("Courier")
    order = orders.ready(order.pk, order.version, owner)
    order = orders.assign(order.pk, order.version, owner, courier)
    order = orders.dispatch(order.pk, order.version, owner)
    node["updatedAt"] = "2026-10-04T11:00:00Z"
    node["displayFinancialStatus"] = "PAID"
    node["totalOutstandingSet"]["shopMoney"]["amount"] = "0"
    order = import_shopify(node, "test.myshopify.com")
    assert order.review_required and order.expected_collection == 10
    with pytest.raises(ValidationError, match="review"):
        orders.deliver(order.pk, order.version, courier, D(10))
    command = {
        "version": order.version,
        "expected_collection": "0",
        "payment_status": "PAID",
        "reason": "Verified provider receipt",
    }
    api.force_authenticate(user_factory("Supply Chain"))
    assert (
        api.post(
            f"/api/v1/orders/{order.pk}/collection-review/", command, format="json"
        ).status_code
        == 403
    )
    api.force_authenticate(user_factory("Finance"))
    assert (
        api.post(
            f"/api/v1/orders/{order.pk}/collection-review/", command, format="json"
        ).status_code
        == 200
    )
    order.refresh_from_db()
    assert not order.review_required and order.expected_collection == 0
    assert orders.deliver(order.pk, order.version, courier, D(0)).state == "DELIVERED"
    assert Order.objects.filter(pk=order.pk).exists()
