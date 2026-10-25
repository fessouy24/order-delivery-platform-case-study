from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.operations import orders
from apps.operations.models import CourierPosition, Order
from tests.scenarios import stocked_product

pytestmark = pytest.mark.django_db


def dispatch(setup_order, user_factory):
    owner, location, _, _, make = setup_order
    order = make([(stocked_product(location), 1)])
    courier = user_factory("Courier")
    order = orders.ready(order.pk, order.version, owner)
    order = orders.assign(order.pk, order.version, owner, courier)
    return orders.dispatch(order.pk, order.version, owner), courier, owner


def sample(**extra):
    return {
        "latitude": 30.04,
        "longitude": 31.23,
        "accuracy": 12,
        "sampled_at": timezone.now().isoformat(),
        **extra,
    }


def test_own_position_only_and_role_boundaries(setup_order, user_factory, api):
    order, courier, _ = dispatch(setup_order, user_factory)
    other = user_factory("Courier", username="other")
    api.force_authenticate(other)
    assert (
        api.post(
            "/api/v1/courier/location/", sample(courier=str(courier.pk)), format="json"
        ).json()["active"]
        is False
    )
    assert not CourierPosition.objects.exists()
    api.force_authenticate(courier)
    assert api.post("/api/v1/courier/location/", sample(), format="json").status_code == 200
    assert CourierPosition.objects.get().courier == courier
    for role in ["Courier", "Warehouse", "Finance", "Production", "Sales"]:
        api.force_authenticate(user_factory(role, username="read-" + role))
        assert api.get("/api/v1/courier/locations/").status_code == 403
    for role in ["Admin", "Management", "Supply Chain"]:
        api.force_authenticate(user_factory(role, username="read-" + role))
        assert api.get("/api/v1/courier/locations/").json()[0]["position"]["latitude"] == 30.04


@pytest.mark.parametrize("outcome", ["deliver", "cancel", "postpone"])
def test_last_outcome_stops_sharing(setup_order, user_factory, api, outcome):
    order, courier, owner = dispatch(setup_order, user_factory)
    api.force_authenticate(courier)
    api.post("/api/v1/courier/location/", sample(), format="json")
    if outcome == "deliver":
        orders.deliver(order.pk, order.version, owner, Decimal(0))
    elif outcome == "cancel":
        orders.cancel(order.pk, order.version, owner, "Customer requested")
    else:
        orders.postpone(
            order.pk,
            order.version,
            owner,
            {
                "delivery_date": order.delivery_date,
                "earliest_time": None,
                "latest_time": None,
                "reason": "Later visit",
            },
        )
    assert api.post("/api/v1/courier/location/", sample(), format="json").json()["active"] is False
    assert not CourierPosition.objects.exists()


def test_no_tracking_before_dispatch_stale_and_invalid_samples(setup_order, user_factory, api):
    order, courier, _ = dispatch(setup_order, user_factory)
    api.force_authenticate(courier)
    for data in [
        sample(latitude=91),
        sample(longitude=-181),
        sample(accuracy=-1),
        sample(sampled_at=(timezone.now() - timedelta(minutes=3)).isoformat()),
    ]:
        assert api.post("/api/v1/courier/location/", data, format="json").status_code == 400
    api.post("/api/v1/courier/location/", sample(), format="json")
    CourierPosition.objects.update(sampled_at=timezone.now() - timedelta(minutes=6))
    api.force_authenticate(user_factory("Management"))
    assert api.get("/api/v1/courier/locations/").json()[0]["position"] is None
    Order.objects.filter(pk=order.pk).update(state=Order.State.ASSIGNED)
    assert api.get("/api/v1/courier/locations/").json() == []
    api.force_authenticate(courier)
    assert api.get("/api/v1/courier/location/").json()["active"] is False


def test_other_active_order_keeps_route_on(setup_order, user_factory, api):
    first, courier, owner = dispatch(setup_order, user_factory)
    *_, make = setup_order
    second = make([(first.lines.get().item, 1)])
    second = orders.ready(second.pk, second.version, owner)
    second = orders.assign(second.pk, second.version, owner, courier)
    second = orders.dispatch(second.pk, second.version, owner)
    orders.deliver(first.pk, first.version, owner, Decimal(0))
    api.force_authenticate(courier)
    assert api.post("/api/v1/courier/location/", sample(), format="json").json()["active"] is True
    assert CourierPosition.objects.count() == 1
