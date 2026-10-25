import pytest

from apps.operations.market import store_quotation
from apps.operations.models import MarketQuotation, Order


@pytest.fixture(autouse=True)
def approved_market_pairs(monkeypatch):
    monkeypatch.setenv("ODOO_MARKET_APPROVED_PAIRS", "[[801,802],[801,801]]")


def test_customer_approval_does_not_implicitly_approve_other_branches():
    from apps.operations.market import is_approved

    assert is_approved({"partner_id": [801, "market"], "partner_shipping_id": [802, "approved"]})
    assert not is_approved({"partner_id": [801, "market"], "partner_shipping_id": [999, "other"]})


@pytest.mark.django_db
def test_missing_approval_list_imports_nothing(monkeypatch):
    monkeypatch.delenv("ODOO_MARKET_APPROVED_PAIRS")
    assert (
        store_quotation(
            "Training",
            {
                "id": 99,
                "company_id": [1, "Example Business"],
                "partner_id": [801, "market"],
                "partner_shipping_id": [802, "branch"],
            },
        )
        is None
    )


@pytest.mark.django_db
def test_pending_import_has_no_invented_date_or_operational_order(monkeypatch):
    monkeypatch.setenv("ODOO_WEBSITE_PARTNER_ID", "803")
    quote = {
        "id": 70,
        "name": "S00070",
        "company_id": [1, "Example Business"],
        "partner_id": [801, "Example Market"],
        "partner_shipping_id": [802, "branch"],
        "state": "draft",
        "origin": False,
        "lines": [{"name": "Example Product", "product_id": [901, "Example Product"]}],
    }
    first = store_quotation("Training", quote)
    second = store_quotation("Training", quote)
    assert first.pk == second.pk
    assert MarketQuotation.objects.count() == 1
    assert second.status == "PENDING" and second.order_id is None
    assert Order.objects.count() == 0
    assert second.snapshot["lines"][0]["name"] == "Example Product"


@pytest.mark.django_db
def test_website_cash_customer_and_origin_are_excluded(monkeypatch):
    monkeypatch.setenv("ODOO_WEBSITE_PARTNER_ID", "803")
    quote = {
        "id": 71,
        "name": "S00071",
        "company_id": [1, "Example Business"],
        "partner_id": [803, "website"],
        "state": "draft",
        "origin": False,
    }
    assert store_quotation("Training", quote) is None
    quote.update(partner_id=[801, "market"], origin="OPSDEMO:abc")
    assert store_quotation("Training", quote) is None


@pytest.mark.django_db
def test_cancelled_pending_quotation_stays_out_of_delivery_work():
    quote = {
        "id": 72,
        "name": "S00072",
        "company_id": [1, "Example Business"],
        "partner_id": [801, "market"],
        "partner_shipping_id": [802, "branch"],
        "state": "cancel",
        "origin": False,
    }
    row = store_quotation("Training", quote)
    assert row.status == "CANCELLED" and row.order_id is None


@pytest.mark.django_db
@pytest.mark.parametrize("correct_branch", [False, True])
def test_schedule_requires_reservation_and_reuses_odoo_quote(
    api, setup_order, user_factory, monkeypatch, correct_branch
):
    from apps.operations import market
    from apps.operations.models import ExternalMapping, IntegrationJob, Item

    _, location, customer, address, _ = setup_order
    staff = user_factory("Supply Chain")
    courier = user_factory("Courier")
    item = Item.objects.create(sku="DEMO-901", name="Example Product", price=100)
    for kind, internal, external in [
        ("item", item.pk, 901),
        ("item_uom", item.pk, 1),
        ("customer", customer.pk, 801),
        ("address", address.pk, 802),
    ]:
        ExternalMapping.objects.create(
            provider="ODOO", entity_type=kind, entity_id=internal, external_id=str(external)
        )
    quote = {
        "id": 73,
        "name": "S00073",
        "company_id": [1, "Example Business"],
        "partner_id": [801, "market"],
        "partner_shipping_id": [802, "branch"],
        "state": "draft",
        "origin": False,
        "write_date": "2026-10-14 12:00:00",
        "currency_id": [74, "EGP"],
        "amount_total": 114,
        "lines": [
            {
                "id": 10,
                "name": item.name,
                "product_id": [901, item.name],
                "product_uom": [1, "Units"],
                "product_uom_qty": 1,
                "price_subtotal": 100,
                "display_type": False,
            }
        ],
    }
    row = store_quotation("Training", quote)

    class Fake:
        database = "Training"

        def call(self, *args, **kwargs):
            return [quote]

    monkeypatch.setattr(market, "Odoo", Fake)
    api.force_authenticate(staff)
    endpoint = f"/api/v1/market/{row.pk}/schedule/"
    assert (
        api.post(
            endpoint, {"courier": str(courier.pk), "location": str(location.pk)}, format="json"
        ).status_code
        == 400
    )
    assert not Order.objects.exists()
    body = {"courier": str(courier.pk), "location": str(location.pk), "delivery_date": "2026-10-05"}
    if correct_branch:
        from apps.operations.models import Customer, CustomerAddress

        corrected = CustomerAddress.objects.create(
            customer=customer, branch_code="DS1", full_address="Demo District"
        )
        other = CustomerAddress.objects.create(
            customer=Customer.objects.create(name="Other market"),
            branch_code="DS1",
            full_address="Wrong customer",
        )
        assert (
            api.post(
                endpoint,
                {
                    **body,
                    "delivery_address": str(other.pk),
                    "delivery_address_reason": "Confirmed branch",
                },
                format="json",
            ).status_code
            == 404
        )
        assert (
            api.post(
                endpoint, {**body, "delivery_address": str(corrected.pk)}, format="json"
            ).status_code
            == 400
        )
        assert not Order.objects.exists()
        body.update(
            delivery_address=str(corrected.pk),
            delivery_address_reason="User confirmed DS1 Demo District despite the Odoo shipping branch.",
        )
    response = api.post(endpoint, body, format="json")
    assert response.status_code == 201, response.data
    order = Order.objects.get()
    assert str(order.delivery_date) == "2026-10-05" and order.courier == courier
    assert order.total == 114 and order.expected_collection == 0
    assert IntegrationJob.objects.get(order=order).status == "SYNCED"
    assert api.get("/api/v1/orders/?q=S00073").data["count"] == 1
    assert api.get(f"/api/v1/orders/{order.pk}/").data["odoo_number"] == "S00073"
    if correct_branch:
        assert order.address == corrected
        assert (
            ExternalMapping.objects.get(entity_type="address", external_id="802").entity_id
            == address.pk
        )
        assert (
            order.events.get(action="market.reservation_scheduled").after["delivery_branch"]
            == "DS1"
        )
    assert api.post(endpoint, body, format="json").status_code == 200
    assert Order.objects.count() == 1
    updated = {**quote, "state": "cancel", "write_date": "2026-10-14 13:00:00"}
    store_quotation("Training", updated)
    order.refresh_from_db()
    assert order.review_required


@pytest.mark.django_db
def test_courier_cannot_read_pending_market_inbox(api, user_factory):
    api.force_authenticate(user_factory("Courier"))
    assert api.get("/api/v1/market/pending/").status_code == 403
