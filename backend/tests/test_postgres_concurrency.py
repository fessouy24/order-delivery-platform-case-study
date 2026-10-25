from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from django.db import close_old_connections, connection

from apps.operations.integrations import import_shopify
from apps.operations.models import Order, ShopifyReference, StockBalance
from tests.scenarios import stocked_product
from tests.test_integrations import shopify_node


@pytest.mark.django_db(transaction=True)
def test_parallel_orders_cannot_over_reserve(setup_order):
    if connection.vendor != "postgresql":
        pytest.skip("Row-lock acceptance requires PostgreSQL; SQLite cannot prove this behavior.")
    _, location, _, _, make = setup_order
    item = stocked_product(location, stock="100")

    def create(key):
        close_old_connections()
        try:
            return make([(item, 80)], key=key).pk
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(create, ["concurrent-one", "concurrent-two"]))
    assert Order.objects.filter(allocated=True).count() == 1
    balance = StockBalance.objects.get(item=item, location=location)
    assert balance.reserved == Decimal(80)
    assert balance.on_hand == Decimal(100)


@pytest.mark.django_db(transaction=True)
def test_parallel_shopify_imports_link_one_order_and_reservation(setup_order, monkeypatch):
    if connection.vendor != "postgresql":
        pytest.skip("Concurrent unique-reference claims require PostgreSQL acceptance.")
    _, location, _, _, _ = setup_order
    item = stocked_product(location)
    monkeypatch.setenv("SHOPIFY_LOCATION_ID", str(location.pk))
    node = shopify_node(item.sku)

    def create(_):
        close_old_connections()
        try:
            return import_shopify(node, "test.myshopify.com").pk
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(create, [1, 2]))
    assert ids[0] == ids[1]
    assert Order.objects.count() == ShopifyReference.objects.count() == 1
    assert StockBalance.objects.get(item=item, location=location).reserved == 1
