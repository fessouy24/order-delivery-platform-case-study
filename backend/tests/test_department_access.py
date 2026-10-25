"""Department boundaries must hold on the server, including direct API requests."""

from decimal import Decimal
from uuid import uuid4

import pytest
from django.contrib.auth.models import Group
from django.test import Client

from apps.operations.models import Item, Location, StockBalance, StockMovement
from tests.scenarios import stocked_product

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    "role", ["Management", "Supply Chain", "Warehouse", "Courier", "Finance", "Sales"]
)
def test_department_write_boundaries(api, user_factory, role):
    api.force_authenticate(user_factory(role))
    permitted = {
        "items": {"Warehouse"},
        "recipes": {"Warehouse"},
        "locations": {"Warehouse"},
        "inventory/movement": {"Warehouse"},
        "areas": {"Supply Chain"},
        "aliases": {"Supply Chain"},
        "customers": {"Supply Chain", "Sales", "Management"},
        "addresses": {"Supply Chain", "Sales", "Management"},
        "occasional": {"Supply Chain", "Management"},
        "users": set(),
        "policies": set(),
        "mappings": set(),
    }
    for endpoint, roles in permitted.items():
        # Empty payloads must fail authorization before any serializer/model mutation.
        url = "area-aliases" if endpoint == "aliases" else endpoint
        if role not in roles:
            assert api.post(f"/api/v1/{url}/", {}, format="json").status_code == 403, (
                role,
                endpoint,
            )
    assert not Item.objects.exists()
    assert not Location.objects.exists()


@pytest.mark.parametrize("role", ["Management", "Warehouse", "Courier", "Finance", "Sales"])
def test_only_coordinator_can_schedule_market_or_occasional(api, user_factory, role):
    api.force_authenticate(user_factory(role))
    for endpoint in ["market", "occasional"]:
        if role == "Management" and endpoint == "occasional":
            continue
        assert (
            api.post(f"/api/v1/{endpoint}/{uuid4()}/schedule/", {}, format="json").status_code
            == 403
        )


def test_warehouse_creates_product_and_opening_count_atomically(api, user_factory):
    api.force_authenticate(user_factory("Warehouse"))
    warehouse = api.post("/api/v1/locations/", {"name": "Second warehouse"}, format="json")
    assert warehouse.status_code == 201
    payload = {
        "sku": "NEW-PRODUCT",
        "name": "New product",
        "opening_stock": {
            "location": warehouse.data["id"],
            "quantity": "25.5",
            "reason": "Counted on receipt",
            "command_key": "opening-1",
        },
    }
    response = api.post("/api/v1/items/", payload, format="json")
    assert response.status_code == 201, response.data
    assert StockBalance.objects.get(item_id=response.data["id"]).on_hand == Decimal("25.5")
    assert StockMovement.objects.count() == 1
    assert "opening_stock" not in response.data
    # A retry cannot create a second item or double its stock.
    assert api.post("/api/v1/items/", payload, format="json").status_code == 400
    assert StockMovement.objects.count() == 1
    # A conflicting stock command rolls back product creation as well.
    payload["sku"] = "ROLLBACK-PRODUCT"
    assert api.post("/api/v1/items/", payload, format="json").status_code == 400
    assert not Item.objects.filter(sku="ROLLBACK-PRODUCT").exists()
    payload["opening_stock"]["command_key"] = "negative"
    payload["opening_stock"]["quantity"] = "-1"
    assert api.post("/api/v1/items/", payload, format="json").status_code == 400
    assert not Item.objects.filter(sku="ROLLBACK-PRODUCT").exists()


def test_courier_order_scope_and_hidden_money(api, user_factory, setup_order):
    _, location, _, _, make = setup_order
    first = make([(stocked_product(location), 1)])
    second = make([(first.lines.first().item, 1)])
    courier = user_factory("Courier")
    other = user_factory("Courier", "other-courier")
    first.courier = courier
    first.save()
    second.courier = other
    second.save()
    api.force_authenticate(courier)
    assert api.get(f"/api/v1/orders/{second.pk}/").status_code == 404
    assert api.get(f"/api/v1/orders/{second.pk}/history/").status_code == 404
    detail = api.get(f"/api/v1/orders/{first.pk}/").data
    assert "expected_collection" in detail
    assert "total" not in detail and "unit_price" not in detail["lines"][0]
    for endpoint in ["users", "items", "inventory", "warehouse", "reports/orders"]:
        assert api.get(f"/api/v1/{endpoint}/").status_code == 403
    api.force_authenticate(user_factory("Warehouse"))
    assert api.get(f"/api/v1/orders/{first.pk}/").status_code == 403
    detail = api.get("/api/v1/departments/warehouse/?date=2026-10-04").data["loads"][0]["orders"][0]
    assert not {"total", "collected", "expected_collection", "payment_status"} & detail.keys()
    assert "unit_price" not in detail["lines"][0]


def test_gm_can_read_finances_but_cannot_approve_or_dispatch(api, user_factory, setup_order):
    _, location, _, _, make = setup_order
    order = make([(stocked_product(location), 1)])
    api.force_authenticate(user_factory("Management"))
    assert "total" in api.get(f"/api/v1/orders/{order.pk}/").data
    assert "finance" in api.get("/api/v1/dashboard/").data
    for action in ["collection-review", "assign", "dispatch", "ready", "deliver"]:
        assert (
            api.post(f"/api/v1/orders/{order.pk}/{action}/", {}, format="json").status_code == 403
        )


def test_disabling_account_and_removing_role_restrict_existing_session(user_factory):
    user = user_factory("Warehouse")
    client = Client()
    client.force_login(user)
    assert client.get("/api/v1/inventory/").status_code == 200
    user.groups.clear()
    assert client.get("/api/v1/inventory/").status_code == 403
    user.groups.add(Group.objects.get(name="Warehouse"))
    user.is_active = False
    user.save()
    assert client.get("/api/v1/inventory/").status_code == 403


def test_finance_can_filter_by_courier_without_managing_accounts(api, user_factory):
    courier = user_factory("Courier")
    user_factory("Warehouse")
    api.force_authenticate(user_factory("Finance"))
    response = api.get("/api/v1/couriers/")
    assert response.status_code == 200
    assert [str(row["id"]) for row in response.data] == [str(courier.pk)]
    assert set(response.data[0]) == {"id", "username", "first_name", "last_name"}
    assert api.get("/api/v1/users/").status_code == 403
