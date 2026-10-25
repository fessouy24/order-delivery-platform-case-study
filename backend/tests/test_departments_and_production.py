from datetime import date
from decimal import Decimal as D

import pytest
from rest_framework.exceptions import ValidationError

from apps.operations import inventory, orders
from apps.operations.models import (
    Order,
    ProductionDevice,
    ProductionNotice,
    ProductionReceipt,
    ProductionWork,
    StockBalance,
    StockLot,
)
from apps.operations.production_work import record_made, sync_work
from tests.scenarios import manufactured_product, stocked_product

pytestmark = pytest.mark.django_db


def made_data(work, qty="1", key="made-test"):
    return {
        "version": work.version,
        "quantity": D(qty),
        "command_key": key,
        "reference": "LOT-TEST",
        "expires_on": date(2026, 12, 31),
    }


def test_batches_are_consumed_in_expiry_order_and_retry_is_safe(setup_order, user_factory):
    owner, loc, _, _, make = setup_order
    item = stocked_product(loc, stock="0")
    for reference, day, qty in [
        ("late", date(2026, 12, 1), "5"),
        ("early", date(2026, 11, 1), "3"),
    ]:
        inventory.receive(item, loc, D(qty), "Receipt", reference, owner, reference, day)
    inventory.receive(item, loc, D(3), "Receipt", "early", owner, "early", date(2026, 11, 1))
    order = make([(item, 4)])
    order = orders.ready(order.pk, order.version, owner)
    order = orders.assign(order.pk, order.version, owner, user_factory("Courier"))
    orders.dispatch(order.pk, order.version, owner)
    assert StockLot.objects.get(reference="early").remaining == 0
    assert StockLot.objects.get(reference="late").remaining == 4
    assert StockBalance.objects.get(item=item).on_hand == 4


def test_expired_batches_block_stock_and_untracked_dates_do_not_add_stock(setup_order, monkeypatch):
    owner, loc, _, _, make = setup_order
    item = stocked_product(loc, stock="10")
    inventory.register_lot(item, loc, D(8), "expired", date(2026, 10, 1), "register-test", owner)
    order = make([(item, 3)])
    assert not order.allocated
    assert StockBalance.objects.get(item=item).on_hand == 10
    with pytest.raises(ValidationError):
        inventory.register_lot(item, loc, D(3), "extra", date(2026, 12, 1), "register-extra", owner)


def test_partial_recipe_production_tracks_finished_and_raw_reservations(setup_order):
    owner, loc, _, _, make = setup_order
    item, raw = manufactured_product(loc, per="2")
    order = make([(item, 5)])
    line = order.lines.get()
    sync_work()
    orders.produce(line.pk, owner, "partial", D(2), date(2026, 12, 1))
    line.refresh_from_db()
    assert line.produced_quantity == 2 and not line.production_complete
    sync_work()
    assert ProductionWork.objects.get(line=line).made == 2
    assert inventory.requirements(order) == {item.pk: D(2), raw.pk: D(6)}
    orders.produce(line.pk, owner, "remaining", D(3), date(2026, 12, 2))
    line.refresh_from_db()
    assert line.production_complete and line.produced_quantity == 5
    assert StockBalance.objects.get(item=raw).on_hand == 90


def test_replenished_shortage_rejects_old_production_confirmation(setup_order):
    owner, loc, _, _, make = setup_order
    item = stocked_product(loc, stock="0")
    make([(item, 2)])
    sync_work()
    work = ProductionWork.objects.get(item=item)
    inventory.receive(item, loc, D(2), "Warehouse receipt", "received-elsewhere", owner)
    with pytest.raises(ValidationError, match="shortage changed"):
        record_made(work.pk, made_data(work), owner)
    assert not ProductionReceipt.objects.exists()
    assert StockBalance.objects.get(item=item).on_hand == 2


def test_limited_box_has_one_work_request_per_order_even_with_available_stock(setup_order):
    owner, loc, _, _, make = setup_order
    item = stocked_product(loc, sku="6224001458545")
    item.produce_on_order = True
    item.save()
    a = make([(item, 2)])
    make([(item, 1)])
    sync_work()
    sync_work()
    assert ProductionWork.objects.filter(kind="LIMITED", active=True).count() == 2
    with pytest.raises(ValidationError, match="made-to-order"):
        orders.ready(a.pk, a.version, owner)
    work = ProductionWork.objects.get(line__order=a)
    record_made(work.pk, made_data(work, "2"), owner)
    a.refresh_from_db()
    assert orders.ready(a.pk, a.version, owner).warehouse_ready


def test_other_products_only_appear_for_shortages_and_made_retries_do_not_duplicate_stock(
    setup_order,
):
    owner, loc, _, _, make = setup_order
    stocked = stocked_product(loc, sku="enough")
    short = stocked_product(loc, sku="short", stock="0")
    make([(stocked, 2)])
    order = make([(short, 2)])
    sync_work()
    assert not ProductionWork.objects.filter(item=stocked).exists()
    work = ProductionWork.objects.get(item=short)
    payload = made_data(work)
    record_made(work.pk, payload, owner)
    record_made(work.pk, payload, owner)
    assert StockBalance.objects.get(item=short).on_hand == 1
    work.refresh_from_db()
    sync_work()
    work.refresh_from_db()
    assert work.required - work.made == 1
    record_made(work.pk, made_data(work, key="last-unit"), owner)
    sync_work()
    order.refresh_from_db()
    assert order.allocated
    assert not ProductionWork.objects.filter(item=short, active=True).exists()


def test_warehouse_only_gets_its_workspace_and_combined_loads_without_prices(
    setup_order, user_factory, api
):
    owner, loc, _, _, make = setup_order
    item = stocked_product(loc)
    courier = user_factory("Courier")
    for qty in [2, 3]:
        order = make([(item, qty)])
        order = orders.ready(order.pk, order.version, owner)
        orders.assign(order.pk, order.version, owner, courier)
    api.force_authenticate(user_factory("Warehouse"))
    assert api.get("/api/v1/dashboard/").status_code == 403
    assert api.get("/api/v1/orders/").status_code == 403
    response = api.get("/api/v1/departments/warehouse/?date=2026-10-04")
    assert response.status_code == 200
    load = response.data["loads"][0]
    assert D(load["products"][0]["quantity"]) == 5
    assert "total" not in load["orders"][0]
    assert "unit_price" not in load["orders"][0]["lines"][0]


def test_finance_workspace_cod_totals_separate_currencies_and_exclude_prepaid(
    setup_order, user_factory, api
):
    owner, loc, _, _, make = setup_order
    item = stocked_product(loc)
    courier = user_factory("Courier")
    for extra in [{}, {"currency": "USD"}, {"payment_method": "ONLINE", "payment_status": "PAID"}]:
        order = make([(item, 2)], **extra)
        order.source = Order.Source.SHOPIFY
        order.save(update_fields=["source"])
        order = orders.ready(order.pk, order.version, owner)
        order = orders.assign(order.pk, order.version, owner, courier)
        orders.dispatch(order.pk, order.version, owner)
    api.force_authenticate(user_factory("Finance"))
    assert api.get("/api/v1/orders/").status_code == 403
    assert api.get("/api/v1/inventory/").status_code == 403
    response = api.get("/api/v1/departments/finance/?date=2026-10-04")
    assert response.status_code == 200
    assert response.data["couriers"][0]["expected_cod"] == {"EGP": "20.00", "USD": "20.00"}
    assert len(response.data["couriers"][0]["orders"]) == 3


def test_production_role_isolated_and_quantities_bounded(setup_order, user_factory, api):
    _, loc, _, _, make = setup_order
    item = stocked_product(loc, stock="0")
    make([(item, 1)])
    production = user_factory("Production")
    api.force_authenticate(production)
    for endpoint in [
        "/api/v1/orders/",
        "/api/v1/departments/finance/",
        "/api/v1/departments/warehouse/",
        "/api/v1/dashboard/",
        "/api/v1/users/",
    ]:
        assert api.get(endpoint).status_code == 403
    response = api.get("/api/v1/production/work/")
    assert response.status_code == 200
    work = ProductionWork.objects.get(item=item)
    assert (
        api.post(
            f"/api/v1/production/work/{work.pk}/made/", made_data(work, "2"), format="json"
        ).status_code
        == 400
    )
    assert (
        api.post(
            f"/api/v1/production/work/{work.pk}/made/", made_data(work), format="json"
        ).status_code
        == 200
    )
    api.force_authenticate(user_factory("Warehouse"))
    assert api.get("/api/v1/production/work/").status_code == 403


def test_push_disabled_never_contacts_google(settings):
    from apps.operations.production_push import send_pending

    settings.PRODUCTION_PUSH_ENABLED = False
    assert send_pending() == {"configured": False, "sent": 0}


def test_production_push_outbox_deduplicates_and_respects_disabled_users(
    setup_order, user_factory, settings, monkeypatch
):
    import sys
    import types

    from apps.operations.production_push import send_pending

    owner, loc, _, _, _ = setup_order
    item = stocked_product(loc)
    user = user_factory("Production")
    ProductionDevice.objects.create(user=user, token="dummy-device-token")
    work = ProductionWork.objects.create(
        key="push-work", kind="LIMITED", item=item, location=loc, required=1
    )
    ProductionNotice.objects.create(work=work, message="New production request")
    posts = []

    class Session:
        def __init__(self, *args):
            pass

        def post(self, url, **kwargs):
            posts.append((url, kwargs))
            return types.SimpleNamespace(status_code=200, text="{}")

    for name in [
        "google",
        "google.oauth2",
        "google.auth",
        "google.auth.transport",
        "google.auth.transport.requests",
    ]:
        module = types.ModuleType(name)
        module.__path__ = []
        monkeypatch.setitem(sys.modules, name, module)
    sys.modules["google.oauth2"].service_account = types.SimpleNamespace(
        Credentials=types.SimpleNamespace(
            from_service_account_file=lambda *args, **kwargs: object()
        )
    )
    sys.modules["google.auth.transport.requests"].AuthorizedSession = Session
    settings.PRODUCTION_PUSH_ENABLED = True
    settings.FCM_CREDENTIALS_FILE = "test-only"
    settings.FCM_PROJECT_ID = "test-company"
    assert send_pending()["sent"] == 1
    assert send_pending()["sent"] == 0
    assert len(posts) == 1
    assert "notification" not in posts[0][1]["json"]["message"]
    user.is_active = False
    user.save()
    ProductionNotice.objects.create(work=work, message="Another request")
    assert send_pending()["sent"] == 0
