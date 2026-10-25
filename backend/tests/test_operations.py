from datetime import date, time
from decimal import Decimal as D

import pytest
from rest_framework.exceptions import ValidationError

from apps.operations import inventory, orders
from apps.operations.common import normalize
from apps.operations.models import (
    Area,
    AreaAlias,
    Customer,
    CustomerAddress,
    Item,
    Order,
    Postponement,
    ProductionBatch,
    StockBalance,
    StockMovement,
)
from tests.scenarios import manufactured_product, stocked_product

pytestmark = pytest.mark.django_db


def test_ds_is_owned_by_customer_and_cross_company_order_rolls_back(setup_order):
    owner, loc, customer, address, make = setup_order
    wrong = Customer.objects.create(name="Other company")
    other_address = CustomerAddress.objects.create(
        customer=wrong, branch_code="DS 30", full_address="Other address"
    )
    item = stocked_product(loc)
    with pytest.raises(ValidationError, match="does not belong"):
        make([(item, 1)], address=other_address)
    assert not Order.objects.exists()
    assert customer.addresses.get().pk == address.pk


def test_duplicate_b2b_order_is_prevented_and_conflicting_key_rejected(setup_order):
    _, loc, _, _, make = setup_order
    item = stocked_product(loc)
    first = make([(item, 2)], key="same-request")
    second = make([(item, 2)], key="same-request")
    assert first.pk == second.pk
    assert Order.objects.count() == 1
    assert first.reservations.filter(active=True).count() == 1
    with pytest.raises(ValidationError, match="different order"):
        make([(item, 3)], key="same-request")


def test_shared_raw_material_shortage_is_one_across_persisted_orders(setup_order):
    _, loc, _, _, make = setup_order
    raw = Item.objects.create(sku="X", name="Material X", kind=Item.Kind.RAW)
    StockBalance.objects.create(item=raw, location=loc, on_hand=D(4))
    a, _ = manufactured_product(loc, "A", raw, "2")
    b, _ = manufactured_product(loc, "B", raw, "3")
    first, second = make([(a, 1)]), make([(b, 1)])
    report = inventory.material_report([first, second])
    assert report[0]["required"] == "5.000000"
    assert D(report[0]["shortage"]) == 1
    assert first.allocated and not second.allocated


def test_reservations_and_cancellation_release(setup_order):
    owner, loc, _, _, make = setup_order
    item = stocked_product(loc)
    first, second = make([(item, 70)]), make([(item, 40)])
    assert not second.allocated
    stock = StockBalance.objects.get(item=item, location=loc)
    assert stock.reserved == 70
    orders.cancel(first.pk, first.version, owner, "Customer changed plans")
    stock.refresh_from_db()
    assert stock.on_hand == 100 and stock.reserved == 0
    second = inventory.allocate(second.pk, owner)
    assert second.allocated
    stock.refresh_from_db()
    assert stock.reserved == 40


def test_postponement_preserves_reservation_and_history(setup_order):
    owner, loc, _, _, make = setup_order
    order = make([(stocked_product(loc), 1)])
    changed = orders.postpone(
        order.pk,
        order.version,
        owner,
        {"delivery_date": date(2026, 10, 5), "earliest_time": time(17), "reason": "After 5 PM"},
    )
    assert changed.delivery_date == date(2026, 10, 5)
    assert changed.earliest_time == time(17)
    assert changed.reservations.filter(active=True).count() == 1
    assert Postponement.objects.get(order=order).original_date == date(2026, 10, 4)
    assert changed.events.filter(action="order.postponed").exists()


def test_manufacturing_consumes_raw_once_then_dispatch_consumes_output(setup_order, user_factory):
    owner, loc, _, _, make = setup_order
    item, raw = manufactured_product(loc)
    order = make([(item, 1)])
    line = order.lines.get()
    order = orders.produce(line.pk, owner, "LOT-001")
    orders.produce(line.pk, owner, "LOT-001")
    assert StockBalance.objects.get(item=raw, location=loc).on_hand == 98
    assert StockBalance.objects.get(item=item, location=loc).on_hand == 1
    order = orders.ready(order.pk, order.version, owner)
    courier = user_factory("Courier")
    order = orders.assign(order.pk, order.version, owner, courier)
    order = orders.dispatch(order.pk, order.version, owner)
    assert StockBalance.objects.get(item=item, location=loc).on_hand == 0
    assert StockBalance.objects.get(item=raw, location=loc).on_hand == 98
    order = orders.deliver(order.pk, order.version, courier, D(0))
    assert order.delivered_at is not None
    assert StockMovement.objects.filter(delta__lt=0).count() == 2


def test_fresh_quantities_aggregate_and_readiness_is_blocked(setup_order, monkeypatch):
    owner, loc, _, _, make = setup_order
    monkeypatch.setattr("apps.operations.orders.business_today", lambda: date(2026, 10, 1))
    item, _ = manufactured_product(loc, fresh=True)
    created = [make([(item, qty)], delivery_date=date(2026, 10, 1)) for qty in [2, 4, 1]]
    assert all(order.delivery_date == date(2026, 10, 3) for order in created)
    assert ProductionBatch.objects.count() == 1
    assert sum(ProductionBatch.objects.get().allocations.values_list("quantity", flat=True)) == 7
    with pytest.raises(ValidationError, match="manufacturing complete"):
        orders.ready(created[0].pk, created[0].version, owner)


def test_courier_scope_and_finance_redaction(setup_order, user_factory, api):
    owner, loc, _, _, make = setup_order
    order = make([(stocked_product(loc), 1)])
    courier = user_factory("Courier", "courier-one")
    other = user_factory("Courier", "courier-two")
    order = orders.ready(order.pk, order.version, owner)
    order = orders.assign(order.pk, order.version, owner, courier)
    api.force_authenticate(other)
    assert api.get(f"/api/v1/orders/{order.pk}/").status_code == 404
    api.force_authenticate(courier)
    detail = api.get(f"/api/v1/orders/{order.pk}/").json()
    assert "total" not in detail and "unit_price" not in detail["lines"][0]
    assert detail["expected_collection"] == "0.00"  # Market customers do not pay couriers.
    assert (
        api.post(f"/api/v1/orders/{order.pk}/ready/", {"version": order.version}).status_code == 403
    )
    assert api.get("/api/v1/users/").status_code == 403


def test_sales_cannot_access_other_customers(setup_order, user_factory, api):
    _, loc, customer, address, _ = setup_order
    stranger = user_factory("Sales", "other-sales")
    item = stocked_product(loc)
    api.force_authenticate(stranger)
    assert api.get(f"/api/v1/customers/{customer.pk}/").status_code == 404
    response = api.post(
        "/api/v1/orders/",
        {
            "command_key": "stranger-create",
            "customer": str(customer.pk),
            "address": str(address.pk),
            "location": str(loc.pk),
            "delivery_date": "2026-10-04",
            "lines": [{"item": str(item.pk), "quantity": "1"}],
        },
        format="json",
    )
    assert response.status_code == 403


def test_stale_version_is_rejected(setup_order):
    owner, loc, _, _, make = setup_order
    order = make([(stocked_product(loc), 1)])
    order = orders.ready(order.pk, order.version, owner)
    with pytest.raises(ValidationError, match="changed"):
        orders.cancel(order.pk, 1, owner, "Stale edit")


def test_area_normalization_preserves_original(setup_order):
    _, _, _, address, _ = setup_order
    area = Area.objects.create(name="New Cairo")
    AreaAlias.objects.create(area=area, original_text="التجمع", normalized_text=normalize("التجمع"))
    assert normalize("  أَلْتجمع  ") == normalize("التجمع")
    address.original_area = "  أَلْتجمع  "
    address.save()
    assert CustomerAddress.objects.get(pk=address.pk).original_area == "  أَلْتجمع  "


def test_stock_movement_idempotency_and_reserved_adjustment(setup_order):
    owner, loc, _, _, make = setup_order
    item = stocked_product(loc, stock="0")
    inventory.receive(item, loc, D(10), "Receipt", "receipt-one", owner)
    inventory.receive(item, loc, D(10), "Receipt", "receipt-one", owner)
    assert StockBalance.objects.get(item=item, location=loc).on_hand == 10
    make([(item, 8)])
    with pytest.raises(ValidationError, match="reserved"):
        inventory.receive(item, loc, D(-3), "Count correction", "adjust-one", owner)
