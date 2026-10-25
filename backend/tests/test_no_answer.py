import pytest
from rest_framework.exceptions import ValidationError

from apps.operations import orders
from apps.operations.models import (
    Alert,
    CourierCashEntry,
    Order,
    ShopifyFulfillmentJob,
    ShopifyReference,
    StockMovement,
)
from tests.scenarios import stocked_product

pytestmark = pytest.mark.django_db


def dispatched(setup_order, user_factory, website=True):
    owner, location, _, _, make = setup_order
    order = make([(stocked_product(location), 1)])
    if website:
        order.source = "SHOPIFY"
        order.save(update_fields=["source"])
        ShopifyReference.objects.create(
            order=order, store="test.myshopify.com", external_id=str(order.pk), order_number="#9002"
        )
    courier = user_factory("Courier")
    order = orders.ready(order.pk, order.version, owner)
    order = orders.assign(order.pk, order.version, owner, courier)
    return orders.dispatch(order.pk, order.version, owner), courier, owner


def test_first_attempt_upcoming_admin_notice_and_duplicate_tap_guard(
    setup_order, user_factory, api
):
    order, courier, _ = dispatched(setup_order, user_factory)
    api.force_authenticate(courier)
    version = order.version
    original = order.delivery_date
    result = api.post(f"/api/v1/orders/{order.pk}/no-answer/", {"version": version}, format="json")
    assert result.status_code == 200 and result.json()["no_answer_count"] == 1
    order.refresh_from_db()
    assert order.delivery_date > original and order.state == Order.State.ASSIGNED
    assert order.courier == courier and order.stock_issued
    assert (
        api.post(
            f"/api/v1/orders/{order.pk}/no-answer/", {"version": version}, format="json"
        ).status_code
        == 404
    )
    assert (
        api.post(
            f"/api/v1/orders/{order.pk}/no-answer/", {"version": order.version}, format="json"
        ).status_code
        == 404
    )
    assert not ShopifyFulfillmentJob.objects.exists() and not CourierCashEntry.objects.exists()
    notice = Alert.objects.get(kind="CUSTOMER_NO_ANSWER")
    assert notice.target_role == "Admin"
    api.force_authenticate(user_factory("Admin"))
    assert str(order.pk) in str(api.get("/api/v1/orders/?window=upcoming").json())
    assert "CUSTOMER_NO_ANSWER" in str(api.get("/api/v1/alerts/").json())


def test_second_dispatched_attempt_cancels_without_restock_or_fulfillment(
    setup_order, user_factory, monkeypatch
):
    order, courier, _ = dispatched(setup_order, user_factory)
    movement_count = StockMovement.objects.count()
    order = orders.no_answer(order.pk, order.version, courier)
    monkeypatch.setattr(orders, "business_today", lambda: order.delivery_date)
    order = orders.dispatch(order.pk, order.version, courier)
    order = orders.no_answer(order.pk, order.version, courier)
    assert order.state == Order.State.CANCELLED and order.no_answer_count == 2
    assert StockMovement.objects.count() == movement_count
    assert Alert.objects.filter(key=f"return:{order.pk}", resolved_at__isnull=True).exists()
    assert Alert.objects.filter(
        key=f"no-answer:{order.pk}:2", target_role="Admin", resolved_at__isnull=True
    ).exists()
    assert not ShopifyFulfillmentJob.objects.exists() and not CourierCashEntry.objects.exists()


def test_market_other_courier_and_department_cannot_use_no_answer(setup_order, user_factory, api):
    order, courier, _ = dispatched(setup_order, user_factory, website=False)
    api.force_authenticate(courier)
    url = f"/api/v1/orders/{order.pk}/no-answer/"
    assert api.post(url, {"version": order.version}, format="json").status_code == 400
    api.force_authenticate(user_factory("Courier", "other-courier"))
    assert api.post(url, {"version": order.version}, format="json").status_code == 404
    api.force_authenticate(user_factory("Warehouse"))
    assert api.post(url, {"version": order.version}, format="json").status_code == 403
    order.refresh_from_db()
    assert order.no_answer_count == 0


def test_produced_fresh_goods_need_review_before_retry(setup_order, user_factory, monkeypatch):
    order, courier, owner = dispatched(setup_order, user_factory)
    order.lines.update(fresh=True, production_complete=True)
    order = orders.no_answer(order.pk, order.version, courier)
    assert not order.warehouse_ready
    assert Alert.objects.filter(
        kind="FRESHNESS_REVIEW", order=order, target_role="Warehouse"
    ).exists()
    monkeypatch.setattr(orders, "business_today", lambda: order.delivery_date)
    order = orders.ready(order.pk, order.version, owner)
    with pytest.raises(ValidationError, match="fresh items"):
        orders.dispatch(order.pk, order.version, owner)
    orders.review_freshness(
        order.pk, order.version, owner, order.delivery_date, "Inspected actual goods"
    )
    assert orders.dispatch(order.pk, order.version, owner).state == Order.State.DISPATCHED


@pytest.mark.parametrize("outcome", ["postpone", "no_answer"])
def test_deferred_order_hidden_until_dispatch_and_can_change_courier(
    setup_order, user_factory, api, monkeypatch, outcome
):
    order, first, owner = dispatched(setup_order, user_factory)
    movement_count = StockMovement.objects.count()
    due = orders.work_calendar().add_workdays(order.delivery_date, 1)
    if outcome == "postpone":
        order = orders.postpone(
            order.pk,
            order.version,
            first,
            {"delivery_date": due, "reason": "Customer requested another day"},
        )
    else:
        order = orders.no_answer(order.pk, order.version, first)
    api.force_authenticate(first)
    assert str(order.pk) not in str(api.get("/api/v1/orders/").json())
    assert api.get(f"/api/v1/orders/{order.pk}/").status_code == 404
    # Physical custody stays recorded and departmental access is preserved.
    assert order.courier_id == first.pk and order.stock_issued
    api.force_authenticate(user_factory("Supply Chain"))
    assert api.get(f"/api/v1/orders/{order.pk}/").status_code == 200
    second = user_factory("Courier", "replacement-courier")
    with pytest.raises(ValidationError, match="custody"):
        orders.assign(order.pk, order.version, owner, second)
    order = orders.assign(
        order.pk, order.version, owner, second, "Physical stock handed to replacement courier"
    )
    api.force_authenticate(second)
    assert api.get(f"/api/v1/orders/{order.pk}/").status_code == 404
    monkeypatch.setattr(orders, "business_today", lambda: order.delivery_date)
    order = orders.dispatch(order.pk, order.version, owner)
    assert api.get(f"/api/v1/orders/{order.pk}/").status_code == 200
    api.force_authenticate(first)
    assert api.get(f"/api/v1/orders/{order.pk}/").status_code == 404
    assert StockMovement.objects.count() == movement_count
