import uuid
from datetime import date
from decimal import Decimal as D

import pytest

from apps.operations import orders
from apps.operations.models import (
    Customer,
    CustomerAddress,
    Item,
    Location,
    Recipe,
    RecipeLine,
    StockBalance,
)


@pytest.fixture(autouse=True)
def operating_day(monkeypatch):
    for module in [
        "apps.operations.orders",
        "apps.operations.views",
        "apps.operations.serializers",
    ]:
        monkeypatch.setattr(module + ".business_today", lambda: date(2026, 10, 4))


@pytest.fixture
def setup_order(user_factory):
    owner = user_factory("Sales")
    location = Location.objects.create(name="Main warehouse")
    customer = Customer.objects.create(
        name="Demo Retailer", company=True, phone="+12025550100", sales_owner=owner
    )
    address = CustomerAddress.objects.create(
        customer=customer, branch_code="DS 30", full_address="Delivery branch address"
    )

    def make(items, key=None, **extra):
        data = {
            "command_key": key or str(uuid.uuid4()),
            "customer": customer,
            "address": address,
            "location": location,
            "delivery_date": date(2026, 10, 4),
            "lines": [{"item": item, "quantity": D(str(qty))} for item, qty in items],
        }
        data.update(extra)
        return orders.create_order(data, owner)

    return owner, location, customer, address, make


def stocked_product(location, sku="A", stock="100"):
    item = Item.objects.create(sku=sku, name=f"Product {sku}", price=D("10"))
    StockBalance.objects.create(item=item, location=location, on_hand=D(stock))
    return item


def manufactured_product(location, sku="A", raw=None, per="2", fresh=False):
    if raw is None:
        raw = Item.objects.create(sku="RAW-X", name="Material X", kind=Item.Kind.RAW)
        StockBalance.objects.create(item=raw, location=location, on_hand=D("100"))
    item = Item.objects.create(
        sku=sku, name=f"Product {sku}", manufactured=True, fresh=fresh, price=D("10")
    )
    recipe = Recipe.objects.create(product=item)
    RecipeLine.objects.create(recipe=recipe, material=raw, quantity=D(per))
    return item, raw
