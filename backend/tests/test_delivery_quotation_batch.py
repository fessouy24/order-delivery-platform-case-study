from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from apps.operations import tasks
from apps.operations.integrations import sync_odoo
from apps.operations.models import DeliveryQuotationBatch, Order, ShopifyReference, WebhookInbox
from tests.scenarios import stocked_product

pytestmark = pytest.mark.django_db


def configure(monkeypatch, hour=9):
    monkeypatch.setenv("TRIAL_MODE", "false")
    monkeypatch.setenv("SHOPIFY_QUOTATION_BATCH_ENABLED", "true")
    monkeypatch.setenv("SHOPIFY_SHOP_DOMAIN", "sample.myshopify.com")
    now = datetime(2026, 10, 4, hour, 0, tzinfo=ZoneInfo("Africa/Cairo"))
    monkeypatch.setattr(tasks.timezone, "now", lambda: now)
    monkeypatch.setattr(tasks, "poll_shopify", lambda: True)
    return now


def website(make, location):
    order = make([(stocked_product(location), 1)])
    order.source = Order.Source.SHOPIFY
    order.save(update_fields=["source"])
    return order


def test_batch_waits_until_nine_and_releases_only_due_orders_once(setup_order, monkeypatch):
    _, location, _, _, make = setup_order
    order = website(make, location)
    configure(monkeypatch, 8)
    tasks.daily_quotation_batch()
    assert not DeliveryQuotationBatch.objects.exists()
    configure(monkeypatch, 9)
    tasks.daily_quotation_batch()
    job = order.odoo
    job.refresh_from_db()
    assert job.website_batch_date.isoformat() == "2026-10-04"
    assert DeliveryQuotationBatch.objects.get().completed_at
    late = make([(order.lines.first().item, 1)])
    late.source = Order.Source.SHOPIFY
    late.save(update_fields=["source"])
    tasks.daily_quotation_batch()
    late.odoo.refresh_from_db()
    assert late.odoo.website_batch_date is None


def test_unreleased_website_job_cannot_create_quotation(setup_order, monkeypatch):
    _, location, _, _, make = setup_order
    order = website(make, location)
    configure(monkeypatch, 8)

    class NeverCall:
        def find(self, *args):
            raise AssertionError("Unreleased website job must not contact Odoo")

    job = sync_odoo(order.odoo.pk, NeverCall())
    assert job.status == "PENDING"
    assert job.attempts == 0


def test_failed_poll_and_stale_claim_can_resume_without_marking_complete(monkeypatch):
    now = configure(monkeypatch)
    batch = DeliveryQuotationBatch.objects.create(
        day=now.date(), claimed_at=now - timedelta(minutes=16)
    )
    monkeypatch.setattr(tasks, "poll_shopify", lambda: False)
    tasks.daily_quotation_batch()
    batch.refresh_from_db()
    assert batch.snapshot is None and batch.claimed_at is None and batch.completed_at is None
    monkeypatch.setattr(tasks, "poll_shopify", lambda: True)
    tasks.daily_quotation_batch()
    batch.refresh_from_db()
    assert batch.completed_at is not None


def test_trial_never_starts_daily_batch(monkeypatch):
    configure(monkeypatch)
    monkeypatch.setenv("TRIAL_MODE", "true")
    tasks.daily_quotation_batch()
    assert not DeliveryQuotationBatch.objects.exists()


def test_selected_event_repair_preserves_release_but_late_event_does_not(setup_order, monkeypatch):
    _, location, _, _, make = setup_order
    order = website(make, location)
    now = configure(monkeypatch)
    ShopifyReference.objects.create(
        order=order,
        store="sample.myshopify.com",
        external_id="gid://shopify/Order/1",
        order_number="#1",
    )
    event = WebhookInbox.objects.create(
        store="sample.myshopify.com",
        event_id="one",
        topic="orders/updated",
        payload={"admin_graphql_api_id": "gid://shopify/Order/1"},
        processed_at=now,
    )
    tasks.process_inbox(str(event.pk))
    order.odoo.refresh_from_db()
    assert order.odoo.website_batch_date is None
    event.quotation_batch_date = now.date()
    event.save(update_fields=["quotation_batch_date"])
    tasks.process_inbox(str(event.pk))
    order.odoo.refresh_from_db()
    assert order.odoo.website_batch_date == now.date()
