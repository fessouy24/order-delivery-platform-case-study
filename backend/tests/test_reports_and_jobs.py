import csv
import io
from datetime import datetime
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.operations import orders, tasks
from apps.operations.models import Alert, Area, IntegrationCursor, WebhookInbox
from apps.operations.providers import IntegrationError
from tests.scenarios import stocked_product

pytestmark = pytest.mark.django_db


def test_warehouse_document_escapes_customer_text(setup_order, api, user_factory):
    _, location, customer, _, make = setup_order
    customer.name = '<script>alert("x")</script>'
    customer.save()
    make([(stocked_product(location), 1)])
    api.force_authenticate(user_factory("Warehouse"))
    response = api.get("/api/v1/documents/preparation/?date=2026-10-04")
    assert response.status_code == 200
    assert b"<script>" not in response.content
    assert b"&lt;script&gt;" in response.content
    assert response["Cache-Control"] == "private, no-store"


def test_courier_cannot_print_another_couriers_dispatch_sheet(setup_order, api, user_factory):
    owner, location, _, _, make = setup_order
    order = make([(stocked_product(location), 1)])
    courier = user_factory("Courier", "assigned-courier")
    order = orders.ready(order.pk, order.version, owner)
    orders.assign(order.pk, order.version, owner, courier)
    api.force_authenticate(user_factory("Courier", "different-courier"))
    assert api.get(f"/api/v1/documents/{order.pk}/dispatch/").status_code == 404


def test_management_export_has_financial_columns_without_formula_injection(
    setup_order, api, user_factory
):
    _, location, customer, _, make = setup_order
    customer.name = '=HYPERLINK("malicious")'
    customer.save()
    make([(stocked_product(location), 1)])
    api.force_authenticate(user_factory("Management"))
    response = api.get("/api/v1/reports/orders/?start=2026-10-04&end=2026-10-04")
    assert response.status_code == 200
    rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
    assert "Total" in rows[0]
    assert rows[1][2].startswith("'=")


def test_finance_export_preserves_zero_collection(setup_order, api, user_factory):
    _, location, _, _, make = setup_order
    make([(stocked_product(location), 1)], payment_status="PAID")
    api.force_authenticate(user_factory("Finance"))
    response = api.get("/api/v1/reports/orders/?start=2026-10-04&end=2026-10-04")
    rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
    assert rows[1][rows[0].index("Expected collection")] == "0.00"


def test_routing_correction_keeps_original_area_and_history(setup_order, api, user_factory):
    _, location, _, address, make = setup_order
    address.original_area = "Customer original spelling"
    address.save()
    order = make([(stocked_product(location), 1)])
    area = Area.objects.create(name="New Cairo")
    api.force_authenticate(user_factory("Supply Chain"))
    response = api.post(
        f"/api/v1/orders/{order.pk}/routing/",
        {"version": order.version, "area": str(area.pk), "reason": "Confirmed location"},
        format="json",
    )
    assert response.status_code == 200
    order.refresh_from_db()
    assert order.area == area
    assert order.original_area == "Customer original spelling"
    assert order.events.filter(action="order.routing_corrected").exists()


def test_invalid_shopify_recipe_is_visible_and_retriable(monkeypatch, api, user_factory):
    event = WebhookInbox.objects.create(
        store="test.myshopify.com", event_id="one", topic="orders/create", payload={"id": 1}
    )

    class FakeShopify:
        def order(self, external_id):
            raise IntegrationError("Product mapping needs review.")

    monkeypatch.setattr(tasks, "Shopify", FakeShopify)
    tasks.process_inbox(str(event.pk))
    event.refresh_from_db()
    assert event.error == "Product mapping needs review."
    assert event.attempts == 1
    assert event.processed_at is None
    assert Alert.objects.get(key=f"inbox:{event.pk}").resolved_at is None
    api.force_authenticate(user_factory("Supply Chain"))
    assert (
        api.post(f"/api/v1/integrations/shopify/{event.pk}/retry/", {}, format="json").status_code
        == 200
    )
    event.refresh_from_db()
    assert event.error == ""
    assert event.next_attempt_at is not None


def test_poll_commits_durable_work_before_cursor_advances(monkeypatch):
    class FakeShopify:
        store = "test.myshopify.com"

        def recent_ids(self, since):
            yield "gid://shopify/Order/1"
            yield "gid://shopify/Order/2"

    monkeypatch.setenv("SHOPIFY_SHOP_DOMAIN", "test.myshopify.com")
    monkeypatch.setenv("SHOPIFY_API_VERSION", "2026-07")
    monkeypatch.setattr(tasks, "Shopify", FakeShopify)
    tasks.poll_shopify()
    assert WebhookInbox.objects.count() == 2
    assert IntegrationCursor.objects.get(name="shopify").synced_through is not None
    tasks.poll_shopify()
    assert WebhookInbox.objects.count() == 2


def test_invalid_report_period_is_rejected(api, user_factory):
    api.force_authenticate(user_factory("Management"))
    assert api.get("/api/v1/reports/orders/?start=2020-01-01&end=2026-01-01").status_code == 400


def test_poll_invalid_configuration_alerts_without_advancing_cursor(monkeypatch):
    monkeypatch.setenv("SHOPIFY_SHOP_DOMAIN", "invalid-host")
    monkeypatch.setenv("SHOPIFY_API_VERSION", "2026-07")
    tasks.poll_shopify()
    assert IntegrationCursor.objects.get(name="shopify").synced_through is None
    assert Alert.objects.filter(key="shopify-poll", resolved_at__isnull=True).exists()


def test_dashboard_groups_orders_in_the_same_area(setup_order, api, user_factory):
    _, location, _, address, make = setup_order
    address.area = Area.objects.create(name="Cairo")
    address.save()
    item = stocked_product(location)
    make([(item, 1)])
    make([(item, 2)])
    api.force_authenticate(user_factory("Management"))
    response = api.get("/api/v1/dashboard/")
    assert response.data["by_area"] == [{"area__name": "Cairo", "count": 2}]


def test_management_dashboard_sums_same_currency_orders(setup_order, api, user_factory):
    _, location, _, _, make = setup_order
    item = stocked_product(location)
    for quantity in (1, 2):
        order = make([(item, quantity)])
        order.state = "DELIVERED"
        order.delivered_at = timezone.make_aware(datetime(2026, 10, 4, 12))
        order.collected = order.expected_collection
        order.save()
    api.force_authenticate(user_factory("Management"))
    response = api.get("/api/v1/dashboard/")
    rows = response.data["finance"]["by_currency"]
    assert len(rows) == 1
    assert rows[0]["expected_today"] == Decimal("30")
    assert rows[0]["collected_today"] == Decimal("30")
