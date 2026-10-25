from datetime import date
from decimal import Decimal as D

import pytest
from rest_framework.exceptions import ValidationError

from apps.operations import inventory, orders, tasks
from apps.operations.models import (
    Alert,
    FreshnessReview,
    IntegrationJob,
    Item,
    OrderLine,
    StockBalance,
    StockReturn,
)
from tests.scenarios import manufactured_product, stocked_product

pytestmark = pytest.mark.django_db


def test_cancelled_dispatch_return_is_inspected_and_idempotent(setup_order, user_factory):
    owner, location, _, _, make = setup_order
    item = stocked_product(location, stock="5")
    order = make([(item, 2)])
    courier = user_factory("Courier")
    order = orders.ready(order.pk, order.version, owner)
    order = orders.assign(order.pk, order.version, owner, courier)
    order = orders.dispatch(order.pk, order.version, owner)
    order = orders.cancel(order.pk, order.version, owner, "Customer refused")
    assert StockBalance.objects.get(item=item).on_hand == 3
    version = order.version
    orders.return_stock(order.pk, version, owner, "RESTOCK", "All units physically inspected")
    orders.return_stock(order.pk, version, owner, "RESTOCK", "All units physically inspected")
    assert StockBalance.objects.get(item=item).on_hand == 5
    assert StockReturn.objects.count() == 1
    assert Alert.objects.get(key=f"return:{order.pk}").resolved_at is not None


def test_no_return_can_restock_unissued_goods(setup_order):
    owner, location, _, _, make = setup_order
    item = stocked_product(location, stock="5")
    order = make([(item, 2)])
    order = orders.cancel(order.pk, order.version, owner, "Cancelled before dispatch")
    with pytest.raises(ValidationError, match="issued stock"):
        orders.return_stock(order.pk, order.version, owner, "RESTOCK", "Not physically issued")
    assert StockBalance.objects.get(item=item).on_hand == 5


def test_reassignment_records_custody_without_second_stock_issue(setup_order, user_factory):
    owner, location, _, _, make = setup_order
    item = stocked_product(location, stock="5")
    order = make([(item, 2)])
    first = user_factory("Courier", "first-courier")
    second = user_factory("Courier", "second-courier")
    order = orders.ready(order.pk, order.version, owner)
    order = orders.assign(order.pk, order.version, owner, first)
    order = orders.dispatch(order.pk, order.version, owner)
    with pytest.raises(ValidationError, match="custody"):
        orders.assign(order.pk, order.version, owner, second)
    order = orders.assign(
        order.pk, order.version, owner, second, "Both couriers confirmed handover"
    )
    assert order.state == "OUT_FOR_DELIVERY"
    assert StockBalance.objects.get(item=item).on_hand == 3
    assert order.assignments.filter(ended_at__isnull=True).get().courier == second
    assert (
        order.events.filter(action="courier.assigned")
        .order_by("sequence")
        .last()
        .after["handover_reason"]
        == "Both couriers confirmed handover"
    )


def test_fresh_postponement_requires_version_and_date_specific_review(setup_order):
    owner, location, _, _, make = setup_order
    product, _ = manufactured_product(location, fresh=True)
    order = make([(product, 1)])
    order = orders.produce(order.lines.get().pk, owner, "FRESH-LOT")
    new_day = date(2026, 10, 6)
    with pytest.raises(ValidationError, match="freshness"):
        orders.postpone(
            order.pk,
            order.version,
            owner,
            {"delivery_date": new_day, "reason": "Customer requested"},
        )
    orders.review_freshness(
        order.pk, order.version, owner, new_day, "Shelf life and storage inspected"
    )
    assert FreshnessReview.objects.get().delivery_date == new_day
    order = orders.postpone(
        order.pk, order.version, owner, {"delivery_date": new_day, "reason": "Customer requested"}
    )
    assert order.delivery_date == new_day
    assert order.allocated


def test_warehouse_material_drilldown_has_product_order_customer(setup_order):
    _, location, customer, _, make = setup_order
    a, raw = manufactured_product(location, per="2")
    b, _ = manufactured_product(location, sku="B", raw=raw, per="3")
    order = make([(a, 2), (b, 1)])
    row = inventory.material_report([order])[0]
    assert D(row["required"]) == 7
    assert row["orders"][0]["customer"] == customer.name
    assert {entry["sku"]: D(entry["required"]) for entry in row["orders"][0]["products"]} == {
        "A": D(4),
        "B": D(3),
    }


def test_batch_assignment_rolls_back_if_one_order_is_not_ready(setup_order, api, user_factory):
    owner, location, _, _, make = setup_order
    item = stocked_product(location)
    first, second = make([(item, 1)]), make([(item, 1)])
    first = orders.ready(first.pk, first.version, owner)
    courier = user_factory("Courier")
    api.force_authenticate(user_factory("Supply Chain"))
    response = api.post(
        "/api/v1/orders/assign-batch/",
        {
            "courier": str(courier.pk),
            "orders": [
                {"id": str(first.pk), "version": first.version},
                {"id": str(second.pk), "version": second.version},
            ],
        },
        format="json",
    )
    assert response.status_code == 400
    first.refresh_from_db()
    assert first.courier_id is None
    assert first.version == 2


def test_role_cannot_invoke_batch_assignment(setup_order, api, user_factory):
    api.force_authenticate(user_factory("Courier"))
    assert api.post("/api/v1/orders/assign-batch/", {}, format="json").status_code == 403


def test_alert_generation_deduplicates_and_resolves(setup_order, monkeypatch):
    owner, location, _, _, make = setup_order
    monkeypatch.setattr(tasks, "business_today", lambda: date(2026, 10, 4))
    order = make([(stocked_product(location), 1)])
    tasks.generate_alerts()
    count = Alert.objects.count()
    tasks.generate_alerts()
    assert Alert.objects.count() == count
    orders.cancel(order.pk, order.version, owner, "No longer required")
    tasks.generate_alerts()
    assert not Alert.objects.filter(key__startswith="daily:", resolved_at__isnull=True).exists()


def test_management_finance_reporting_never_adds_different_currencies(
    setup_order, user_factory, api
):
    _, location, _, _, make = setup_order
    item = stocked_product(location)
    make([(item, 2)], currency="EGP")
    make([(item, 3)], currency="USD")
    api.force_authenticate(user_factory("Management"))
    rows = api.get("/api/v1/dashboard/").json()["finance"]["by_currency"]
    assert {row["currency"]: D(row["expected_today"]) for row in rows} == {
        "EGP": D(20),
        "USD": D(30),
    }


def test_partial_payment_requires_explicit_remaining_collection(setup_order):
    _, location, _, _, make = setup_order
    item = stocked_product(location)
    with pytest.raises(ValidationError, match="explicit"):
        make([(item, 2)], payment_status="PARTIAL")
    order = make([(item, 2)], payment_status="PARTIAL", expected_collection=D(5))
    assert order.expected_collection == 5


def test_unpaid_non_cash_order_still_requires_payment(setup_order):
    _, location, _, _, make = setup_order
    order = make([(stocked_product(location), 1)], payment_method="BANK_TRANSFER")
    assert order.expected_collection == 10


def test_admin_can_manage_accounts_without_password_leaking(api, user_factory, settings):
    settings.REQUIRE_STAFF_MFA = (
        False  # This case tests role administration; MFA has separate mandatory coverage.
    )
    admin = user_factory("Admin")
    api.force_authenticate(admin)
    response = api.post(
        "/api/v1/users/",
        {"username": "new-staff", "password": "initial-long-secret-7859", "roles": ["Warehouse"]},
        format="json",
    )
    assert response.status_code == 201
    assert response.json()["roles"] == ["Warehouse"]
    assert "password" not in response.json()
    response = api.patch(f"/api/v1/users/{admin.pk}/", {"roles": ["Sales"]}, format="json")
    assert response.status_code == 400


def test_missing_verified_mfa_context_fails_closed(api, user_factory, settings):
    settings.REQUIRE_STAFF_MFA = True
    api.force_authenticate(user_factory("Admin"))
    assert api.get("/api/v1/users/").status_code == 403


def test_raw_material_cannot_be_manufactured_through_catalog_api(api, user_factory):
    api.force_authenticate(user_factory("Warehouse"))
    response = api.post(
        "/api/v1/items/",
        {"sku": "RAW", "name": "Raw", "kind": "RAW_MATERIAL", "unit": "kg", "manufactured": True},
        format="json",
    )
    assert response.status_code == 400
    assert not Item.objects.filter(sku="RAW").exists()


def test_quantity_amendment_releases_and_recalculates_reservations(setup_order):
    owner, location, _, _, make = setup_order
    item = stocked_product(location, stock="10")
    order = make([(item, 8)])
    old_line = order.lines.get()
    order = orders.amend(
        order.pk,
        order.version,
        owner,
        {"reason": "Customer reduced quantity", "lines": [{"item": item, "quantity": D(3)}]},
    )
    balance = StockBalance.objects.get(item=item)
    assert balance.reserved == 3
    assert order.total == 30
    assert order.lines.get().quantity == 3
    assert not OrderLine.all_objects.get(pk=old_line.pk).active
    assert OrderLine.all_objects.filter(order=order).count() == 2
    assert order.events.get(action="order.amended").before["lines"][0]["quantity"] == "8.000000"


def test_amendment_does_not_duplicate_production_demand(setup_order, api, user_factory):
    owner, location, _, _, make = setup_order
    product, raw = manufactured_product(location)
    order = make([(product, 5)])
    order = orders.amend(
        order.pk,
        order.version,
        owner,
        {"reason": "Reduced production", "lines": [{"item": product, "quantity": D(2)}]},
    )
    api.force_authenticate(user_factory("Warehouse"))
    api.force_authenticate(user_factory("Production"))
    production = api.get("/api/v1/production/").json()
    assert len(production) == 1
    assert D(production[0]["quantity"]) == 2
    assert len(production[0]["allocations"]) == 1
    assert StockBalance.objects.get(item=raw).reserved == 4


def test_amendment_is_blocked_after_remote_creation_starts(setup_order):
    owner, location, _, _, make = setup_order
    item = stocked_product(location)
    order = make([(item, 8)])
    IntegrationJob.objects.filter(order=order).update(status="CREATING")
    with pytest.raises(ValidationError, match="Odoo request"):
        orders.amend(
            order.pk,
            order.version,
            owner,
            {"reason": "Unsafe change", "lines": [{"item": item, "quantity": D(3)}]},
        )
    assert StockBalance.objects.get(item=item).reserved == 8


def test_catalog_edit_cannot_change_existing_order_freshness(setup_order):
    owner, location, _, _, make = setup_order
    product, _ = manufactured_product(location, fresh=True)
    order = make([(product, 1)])
    order = orders.produce(order.lines.get().pk, owner, "FRESH-LOT")
    product.fresh = False
    product.save()
    with pytest.raises(ValidationError, match="freshness"):
        orders.postpone(
            order.pk,
            order.version,
            owner,
            {"delivery_date": date(2026, 10, 6), "reason": "Customer requested"},
        )


def test_raw_material_recipe_units_cannot_be_silently_changed(setup_order, api, user_factory):
    _, location, _, _, _ = setup_order
    _, raw = manufactured_product(location)
    api.force_authenticate(user_factory("Warehouse"))
    response = api.patch(f"/api/v1/items/{raw.pk}/", {"unit": "kg"}, format="json")
    assert response.status_code == 400


def test_fractional_quantity_money_and_collection_use_same_rounding(setup_order):
    _, location, _, _, make = setup_order
    product = stocked_product(location)
    product.price = D("0.05")
    product.save()
    order = make([(product, "0.1")])
    assert order.total == order.expected_collection


def test_cancellation_resolves_shortage_and_unstarted_job(setup_order):
    owner, location, _, _, make = setup_order
    order = make([(stocked_product(location, stock="0"), 1)])
    assert Alert.objects.filter(order=order, resolved_at__isnull=True).exists()
    orders.cancel(order.pk, order.version, owner, "No longer required")
    assert not Alert.objects.filter(order=order, resolved_at__isnull=True).exists()
    assert IntegrationJob.objects.get(order=order).status == "CANCELLED"


def test_nested_amendment_history_hides_prices_from_coordinator(setup_order, api, user_factory):
    owner, location, _, _, make = setup_order
    product = stocked_product(location)
    order = make([(product, 5)])
    orders.amend(
        order.pk,
        order.version,
        owner,
        {"reason": "Reduced quantity", "lines": [{"item": product, "quantity": D(2)}]},
    )
    api.force_authenticate(user_factory("Supply Chain"))
    response = api.get(f"/api/v1/orders/{order.pk}/history/")
    assert response.status_code == 200
    assert b"unit_price" not in response.content
    assert b'"total"' not in response.content


def test_history_sequence_preserves_actions_with_identical_timestamps(setup_order, monkeypatch):
    from django.utils import timezone

    fixed_time = timezone.now()
    monkeypatch.setattr(timezone, "now", lambda: fixed_time)
    owner, location, _, _, make = setup_order
    order = make([(stocked_product(location), 1)])
    orders.ready(order.pk, order.version, owner)
    events = list(order.events.order_by("sequence"))
    assert len({event.created_at for event in events}) == 1
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    assert events[-1].action == "warehouse.ready"
