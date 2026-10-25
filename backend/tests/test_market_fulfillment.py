from decimal import Decimal as D
from urllib.parse import parse_qs, urlparse

import pytest
from rest_framework.exceptions import ValidationError

from apps.operations import orders
from apps.operations.communication import WEBSITE_DELIVERY_MESSAGE
from apps.operations.models import Alert, Order, StockBalance
from tests.scenarios import stocked_product

pytestmark = pytest.mark.django_db


def entries(order, amount):
    return [{"line": order.lines.get().pk, "quantity": D(amount)}]


def test_partial_stock_dispatch_and_receipt_keep_original_order(setup_order, user_factory):
    owner, loc, _, _, make = setup_order
    item = stocked_product(loc, stock="4")
    order = make([(item, 10)])
    assert not order.allocated
    order = orders.market_quantities(
        order.pk, order.version, owner, entries(order, "4"), "Only four available"
    )
    assert order.allocated
    order = orders.ready(order.pk, order.version, owner)
    order = orders.assign(order.pk, order.version, owner, user_factory("Courier"))
    order = orders.dispatch(order.pk, order.version, owner)
    assert StockBalance.objects.get(item=item).on_hand == 0
    order = orders.market_quantities(
        order.pk, order.version, owner, entries(order, "3"), "One refused", received=True
    )
    line = order.lines.get()
    assert (line.quantity, line.sent_quantity, line.received_quantity) == (D(10), D(4), D(3))
    assert order.total == 100
    assert Alert.objects.filter(key=f"market-receipt:{order.pk}", resolved_at=None).exists()
    assert StockBalance.objects.get(item=item).on_hand == 0
    with pytest.raises(ValidationError):
        orders.market_quantities(
            order.pk, order.version, owner, entries(order, "5"), "Too many", received=True
        )
    with pytest.raises(ValidationError):
        orders.market_quantities(
            order.pk, order.version, owner, entries(order, "3"), "After dispatch"
        )


def test_market_quantity_permissions_and_stale_requests(setup_order, user_factory, api):
    _, loc, _, _, make = setup_order
    order = make([(stocked_product(loc), 5)])
    data = {
        "version": order.version,
        "lines": [{"line": str(order.lines.get().pk), "quantity": "3"}],
        "reason": "Partial dispatch",
    }
    for role in ["Courier", "Finance", "Supply Chain", "Management", "Sales"]:
        api.force_authenticate(user_factory(role, "quantity-" + role.replace(" ", "-")))
        assert (
            api.post(f"/api/v1/orders/{order.pk}/market-sent/", data, format="json").status_code
            == 403
        )
    api.force_authenticate(user_factory("Warehouse"))
    response = api.post(f"/api/v1/orders/{order.pk}/market-sent/", data, format="json")
    assert response.status_code == 200, response.data
    assert (
        api.post(f"/api/v1/orders/{order.pk}/market-sent/", data, format="json").status_code == 400
    )
    data["version"] = response.data["version"]
    assert (
        api.post(f"/api/v1/orders/{order.pk}/market-received/", data, format="json").status_code
        == 403
    )
    api.force_authenticate(user_factory("Finance", "finance-receipt"))
    assert (
        api.post(f"/api/v1/orders/{order.pk}/market-received/", data, format="json").status_code
        == 400
    )


def test_website_whatsapp_exact_message_and_egyptian_phone(setup_order, user_factory, api):
    _, loc, _, _, make = setup_order
    order = make([(stocked_product(loc), 1)])
    order.source = Order.Source.SHOPIFY
    order.customer_phone = "01000000000"
    order.save()
    api.force_authenticate(user_factory("Supply Chain"))
    response = api.post(f"/api/v1/orders/{order.pk}/whatsapp/", {}, format="json")
    assert response.status_code == 200, response.data
    assert urlparse(response.data["url"]).path == "/201000000000"
    assert parse_qs(urlparse(response.data["url"]).query)["text"] == [WEBSITE_DELIVERY_MESSAGE]


def test_omitted_duplicate_and_excess_lines_are_rejected(setup_order):
    owner, loc, _, _, make = setup_order
    order = make([(stocked_product(loc), 5)])
    for values in [
        [],
        entries(order, "0"),
        entries(order, "6"),
        entries(order, "-1"),
        entries(order, "2") * 2,
    ]:
        with pytest.raises(ValidationError):
            orders.market_quantities(order.pk, order.version, owner, values, "Invalid quantities")
    order.refresh_from_db()
    assert order.lines.get().sent_quantity is None


def test_cancelled_partial_dispatch_returns_only_sent_stock(setup_order, user_factory):
    owner, loc, _, _, make = setup_order
    item = stocked_product(loc, stock="20")
    order = make([(item, 10)])
    order = orders.market_quantities(
        order.pk, order.version, owner, entries(order, "4"), "Partial load"
    )
    order = orders.ready(order.pk, order.version, owner)
    order = orders.assign(order.pk, order.version, owner, user_factory("Courier"))
    order = orders.dispatch(order.pk, order.version, owner)
    order = orders.cancel(order.pk, order.version, owner, "Refused")
    orders.return_stock(order.pk, order.version, owner, "RESTOCK", "All four physically returned")
    assert StockBalance.objects.get(item=item).on_hand == 20
