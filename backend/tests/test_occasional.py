import pytest

from apps.operations.models import IntegrationJob, OccasionalOrder, Order
from tests.scenarios import stocked_product


@pytest.mark.django_db
@pytest.mark.parametrize("creator_role", ["Supply Chain", "Management", "Admin"])
def test_phone_order_pending_then_reserved(api, setup_order, user_factory, creator_role):
    _, location, customer, address, _ = setup_order
    item = stocked_product(location)
    operator = user_factory(creator_role)
    courier = user_factory("Courier")
    api.force_authenticate(operator)
    data = {
        "command_key": "phone-one",
        "channel": "PHONE",
        "reference": "Called by Ahmed",
        "customer": str(customer.pk),
        "address": str(address.pk),
        "location": str(location.pk),
        "lines": [{"item": str(item.pk), "quantity": "2", "unit_price": "12.50"}],
        "payment_method": "COD",
        "payment_status": "PARTIAL",
        "expected_collection": "5.00",
    }
    first = api.post("/api/v1/occasional/", data, format="json")
    assert first.status_code == 201, first.data
    assert first.data["status"] == "PENDING"
    assert Order.objects.count() == 0 and IntegrationJob.objects.count() == 0
    row = OccasionalOrder.objects.get()
    assert "delivery_date" not in row.payload
    assert api.post("/api/v1/occasional/", data, format="json").status_code == 200
    changed = {**data, "reference": "Different caller"}
    assert api.post("/api/v1/occasional/", changed, format="json").status_code == 400
    endpoint = f"/api/v1/occasional/{row.pk}/schedule/"
    assert api.post(endpoint, {}, format="json").status_code == 400
    reserved = {"delivery_date": "2026-10-05", "courier": str(courier.pk)}
    result = api.post(endpoint, reserved, format="json")
    assert result.status_code == 201, result.data
    order = Order.objects.get()
    assert order.total == 25 and order.expected_collection == 5
    assert order.courier == courier and str(order.delivery_date) == "2026-10-05"
    assert "PHONE / Called by Ahmed" in order.notes
    assert api.post(endpoint, reserved, format="json").status_code == 200
    assert Order.objects.count() == 1


@pytest.mark.django_db
def test_courier_cannot_create_occasional_requests(api, user_factory):
    api.force_authenticate(user_factory("Courier"))
    assert api.post("/api/v1/occasional/", {}, format="json").status_code == 403


@pytest.mark.django_db
def test_operations_can_add_one_time_customer_and_address(api, user_factory):
    api.force_authenticate(user_factory("Supply Chain"))
    response = api.post(
        "/api/v1/customers/", {"name": "Phone customer", "company": False}, format="json"
    )
    assert response.status_code == 201, response.data
    response = api.post(
        "/api/v1/addresses/",
        {
            "customer": response.data["id"],
            "branch_code": "Home",
            "full_address": "Delivery address",
        },
        format="json",
    )
    assert response.status_code == 201, response.data
