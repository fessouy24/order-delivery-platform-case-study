from copy import deepcopy
from decimal import Decimal

import pytest

from apps.operations.integrations import sync_odoo, verify_exclusive_tax_breakdown
from apps.operations.models import IntegrationJob, ShopifyReference
from apps.operations.providers import IntegrationError
from tests.scenarios import stocked_product

pytestmark = pytest.mark.django_db


def taxed_order(setup_order):
    _, location, _, _, make = setup_order
    order = make([(stocked_product(location), 1)])
    line = order.lines.get()
    net = line.unit_price
    vat = (net * Decimal(".14")).quantize(Decimal(".01"))
    order.source = "SHOPIFY"
    order.total = net + vat
    order.save(update_fields=["source", "total"])

    def money(amount):
        return {"shopMoney": {"amount": str(amount)}}

    snapshot = {
        "currencyCode": "EGP",
        "taxesIncluded": False,
        "currentSubtotalPriceSet": money(net),
        "currentTotalTaxSet": money(vat),
        "currentTotalPriceSet": money(order.total),
        "currentTotalDiscountsSet": money(0),
        "totalShippingPriceSet": money(0),
        "lines": [
            {
                "quantity": 1,
                "originalUnitPriceSet": money(net),
                "taxLines": [{"priceSet": money(vat)}],
            }
        ],
    }
    reference = ShopifyReference.objects.create(
        order=order,
        store="test.myshopify.com",
        external_id="test-vat",
        order_number="#VAT",
        commercial_snapshot=snapshot,
    )
    return order, reference


def test_tax_exclusive_order_keeps_net_unit_price(setup_order, monkeypatch):
    from apps.operations.models import ExternalMapping

    order, reference = taxed_order(setup_order)
    for kind, entity in [
        ("customer", order.customer_id),
        ("address", order.address_id),
        ("item", order.lines.get().item_id),
    ]:
        ExternalMapping.objects.create(
            provider="ODOO", entity_type=kind, entity_id=entity, external_id="1"
        )

    class Adapter:
        record = None
        values = None

        def find(self, key):
            return [self.record] if self.record else []

        def create(self, values):
            self.values = values
            self.record = {
                "id": 9,
                "name": "S-TEST",
                "state": "draft",
                "currency_id": [1, "EGP"],
                "amount_total": str(order.total),
            }
            return 9

    adapter = Adapter()
    monkeypatch.delenv("SHOPIFY_QUOTATION_BATCH_ENABLED", raising=False)
    job = sync_odoo(IntegrationJob.objects.get(order=order).pk, adapter=adapter)
    assert job.status == "SYNCED", job.error
    assert adapter.values["order_line"][0][2]["price_unit"] == float(order.lines.get().unit_price)
    assert order.lines.get().unit_price < order.total


@pytest.mark.parametrize("change", ["shipping", "discount", "included", "tax", "line", "missing"])
def test_unreconciled_commercial_breakdown_is_blocked(setup_order, change):
    order, reference = taxed_order(setup_order)
    snapshot = deepcopy(reference.commercial_snapshot)
    if change == "shipping":
        snapshot["totalShippingPriceSet"]["shopMoney"]["amount"] = "5"
    elif change == "discount":
        snapshot["currentTotalDiscountsSet"]["shopMoney"]["amount"] = "1"
    elif change == "included":
        snapshot["taxesIncluded"] = True
    elif change == "tax":
        snapshot["lines"][0]["taxLines"][0]["priceSet"]["shopMoney"]["amount"] = "0"
    elif change == "line":
        snapshot["lines"][0]["quantity"] = 2
    else:
        snapshot.pop("currentTotalTaxSet")
    reference.commercial_snapshot = snapshot
    reference.save()
    with pytest.raises(IntegrationError, match="commercial review"):
        verify_exclusive_tax_breakdown(order)
