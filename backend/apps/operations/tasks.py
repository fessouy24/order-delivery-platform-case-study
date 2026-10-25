import hashlib
import os
from datetime import timedelta

from celery import shared_task
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .common import business_today, work_calendar
from .integrations import import_shopify, sync_odoo
from .models import (
    Alert,
    DeliveryQuotationBatch,
    IntegrationCursor,
    IntegrationJob,
    Order,
    ProductionBatch,
    ShopifyReference,
    WebhookInbox,
)
from .providers import IntegrationError, Shopify, TransientError


@shared_task
def refresh_production_work():
    from .production_push import send_pending
    from .production_work import sync_work

    sync_work()
    return send_pending()


@shared_task(soft_time_limit=840, time_limit=900)
def poll_market_quotations():
    if (
        os.environ.get("TRIAL_MODE") == "true"
        or os.environ.get("ODOO_MARKET_IMPORT_ENABLED") != "true"
    ):
        return
    from .market import poll_market

    try:
        result = poll_market()
        Alert.objects.filter(key="market-poll").update(resolved_at=timezone.now())
        return result
    except IntegrationError as exc:
        Alert.objects.update_or_create(
            key="market-poll",
            defaults={
                "kind": "MARKET_IMPORT_FAILURE",
                "message": str(exc)[:500],
                "target_role": "Supply Chain",
                "resolved_at": None,
            },
        )


@shared_task
def process_inbox(event_id):
    with transaction.atomic():
        event = WebhookInbox.objects.select_for_update().get(pk=event_id)
        if event.processed_at:
            release_selected_quotation(event)
            return
        event.attempts += 1
        try:
            external_id = event.payload.get("admin_graphql_api_id")
            if not external_id and str(event.payload.get("id", "")).isdigit():
                external_id = f"gid://shopify/Order/{event.payload['id']}"
            if not external_id:
                raise IntegrationError("Webhook contains no valid Shopify order ID.")
            client = Shopify()
            import_shopify(client.order(external_id), event.store)
            event.processed_at, event.error, event.next_attempt_at = timezone.now(), "", None
            Alert.objects.filter(key=f"inbox:{event.pk}").update(resolved_at=timezone.now())
        except (IntegrationError, ValidationError, ValueError, KeyError) as exc:
            event.error = (
                str(exc)[:500]
                if isinstance(exc, IntegrationError)
                else "Shopify fields, stock rules or product recipes need review."
            )
            event.next_attempt_at = (
                timezone.now() + timedelta(seconds=min(3600, 30 * 2 ** min(event.attempts, 7)))
                if isinstance(exc, TransientError)
                else None
            )
            Alert.objects.update_or_create(
                key=f"inbox:{event.pk}",
                defaults={
                    "kind": "SHOPIFY_IMPORT_FAILURE",
                    "message": event.error,
                    "target_role": "Supply Chain",
                    "resolved_at": None,
                },
            )
        event.save()
        if event.processed_at:
            release_selected_quotation(event)


def release_selected_quotation(event):
    # A selected import repaired later keeps its batch eligibility; late new arrivals do not.
    if (
        os.environ.get("SHOPIFY_QUOTATION_BATCH_ENABLED") != "true"
        or not event.quotation_batch_date
    ):
        return
    external_id = event.payload.get("admin_graphql_api_id")
    if not external_id and str(event.payload.get("id", "")).isdigit():
        external_id = f"gid://shopify/Order/{event.payload['id']}"
    order_ids = ShopifyReference.objects.filter(
        store=event.store, external_id=external_id
    ).values_list("order_id", flat=True)
    IntegrationJob.objects.filter(
        order_id__in=order_ids,
        order__source=Order.Source.SHOPIFY,
        order__delivery_date__lte=event.quotation_batch_date,
        website_batch_date__isnull=True,
    ).exclude(order__state__in=[Order.State.CANCELLED, Order.State.DELIVERED]).update(
        website_batch_date=event.quotation_batch_date
    )


@shared_task
def process_odoo(job_id):
    sync_odoo(job_id)


@shared_task
def sweep_integrations():
    if os.environ.get("TRIAL_MODE") == "true":
        return
    now = timezone.now()
    events = WebhookInbox.objects.filter(processed_at__isnull=True).filter(
        Q(error="") | Q(next_attempt_at__lte=now)
    )
    for event_id in events.values_list("pk", flat=True)[:100]:
        process_inbox.delay(str(event_id))
    jobs = IntegrationJob.objects.filter(
        order__delivery_date__lte=business_today(), order__review_required=False
    ).exclude(order__state=Order.State.CANCELLED)
    jobs = jobs.filter(
        Q(status="PENDING")
        | Q(next_attempt_at__lte=now, status__in=["ERROR", "UNKNOWN"])
        | Q(status__in=["CLAIMED", "CREATING"], claimed_at__lt=now - timedelta(minutes=5))
    )
    if os.environ.get("SHOPIFY_QUOTATION_BATCH_ENABLED") == "true":
        jobs = jobs.filter(
            ~Q(order__source=Order.Source.SHOPIFY) | Q(website_batch_date__isnull=False)
        )
    for job_id in jobs.values_list("pk", flat=True)[:100]:
        process_odoo.delay(str(job_id))


@shared_task(soft_time_limit=110, time_limit=120)
def process_shopify_fulfillment(job_id):
    from .shopify_fulfillment import process

    return process(job_id)


@shared_task
def sweep_shopify_fulfillments():
    if (
        os.environ.get("TRIAL_MODE") == "true"
        or os.environ.get("SHOPIFY_FULFILLMENT_ENABLED") != "true"
    ):
        return
    from .models import ShopifyFulfillmentJob

    now = timezone.now()
    jobs = ShopifyFulfillmentJob.objects.filter(
        Q(status="PENDING")
        | Q(status="ERROR", next_attempt_at__lte=now)
        | Q(status__in=["CLAIMED", "SUBMITTING"], claimed_at__lt=now - timedelta(minutes=5))
    )
    for pk in jobs.values_list("pk", flat=True)[:100]:
        process_shopify_fulfillment.delay(str(pk))


@shared_task(soft_time_limit=840, time_limit=900)
def poll_shopify():
    if not os.environ.get("SHOPIFY_SHOP_DOMAIN") or not os.environ.get("SHOPIFY_API_VERSION"):
        return False
    cursor, _ = IntegrationCursor.objects.get_or_create(name="shopify")
    started = timezone.now()
    since = (cursor.synced_through or started - timedelta(days=7)) - timedelta(minutes=5)
    try:
        client = Shopify()
        for external_id in client.recent_ids(since):
            # Persist work before advancing the cursor; a malformed order cannot block unrelated imports.
            key = "poll:" + hashlib.sha256(f"{client.store}|{external_id}".encode()).hexdigest()
            with transaction.atomic():
                event, created = WebhookInbox.objects.select_for_update().get_or_create(
                    store=client.store,
                    event_id=key,
                    defaults={
                        "topic": "orders/updated",
                        "payload": {"admin_graphql_api_id": external_id},
                    },
                )
                if not created and event.processed_at:
                    event.processed_at, event.error, event.next_attempt_at = None, "", None
                    event.save(update_fields=["processed_at", "error", "next_attempt_at"])
    except IntegrationError as exc:
        Alert.objects.update_or_create(
            key="shopify-poll",
            defaults={
                "kind": "SHOPIFY_SYNC_FAILURE",
                "message": str(exc)[:500],
                "target_role": "Supply Chain",
                "resolved_at": None,
            },
        )
        return False
    IntegrationCursor.objects.filter(pk=cursor.pk).update(synced_through=started)
    Alert.objects.filter(key="shopify-poll").update(resolved_at=timezone.now())
    return True


@shared_task(soft_time_limit=840, time_limit=900)
def daily_quotation_batch():
    """Capture the day's selection once, process bounded chunks, and resume after restarts."""
    if (
        os.environ.get("TRIAL_MODE") == "true"
        or os.environ.get("SHOPIFY_QUOTATION_BATCH_ENABLED") != "true"
    ):
        return
    now = timezone.now()
    local = timezone.localtime(now)
    day = local.date()
    if local.hour < 9 or not work_calendar().is_workday(day):
        return
    batch, _ = DeliveryQuotationBatch.objects.get_or_create(day=day)
    with transaction.atomic():
        batch = DeliveryQuotationBatch.objects.select_for_update().get(pk=batch.pk)
        if batch.completed_at or (
            batch.claimed_at and batch.claimed_at > now - timedelta(minutes=15)
        ):
            return
        batch.claimed_at = now
        batch.save(update_fields=["claimed_at"])
    try:
        if batch.snapshot is None:
            if not poll_shopify():
                return
            store = os.environ.get("SHOPIFY_SHOP_DOMAIN", "").lower()
            # This is a fixed selection: later arrivals wait for the next day's batch.
            snapshot = {
                "events": [
                    str(pk)
                    for pk in WebhookInbox.objects.filter(store=store, processed_at__isnull=True)
                    .order_by("created_at", "pk")
                    .values_list("pk", flat=True)
                ],
                "jobs": [
                    str(pk)
                    for pk in IntegrationJob.objects.filter(
                        order__source=Order.Source.SHOPIFY, order__delivery_date__lte=day
                    )
                    .exclude(order__state__in=[Order.State.CANCELLED, Order.State.DELIVERED])
                    .values_list("pk", flat=True)
                ],
            }
            batch.snapshot = snapshot
            batch.save(update_fields=["snapshot"])
        IntegrationJob.objects.filter(
            pk__in=batch.snapshot["jobs"], website_batch_date__isnull=True
        ).update(website_batch_date=day)
        WebhookInbox.objects.filter(pk__in=batch.snapshot["events"]).update(
            quotation_batch_date=day
        )
        for index in range(
            batch.next_event, min(batch.next_event + 20, len(batch.snapshot["events"]))
        ):
            event_id = batch.snapshot["events"][index]
            process_inbox(event_id)
            batch.next_event = index + 1
            batch.save(update_fields=["next_event"])
        if batch.next_event == len(batch.snapshot["events"]):
            batch.completed_at = timezone.now()
            batch.save(update_fields=["completed_at"])
    finally:
        DeliveryQuotationBatch.objects.filter(pk=batch.pk).update(claimed_at=None)


@shared_task
def generate_alerts():
    day = business_today()
    live_keys = set()
    active = Order.objects.exclude(state__in=[Order.State.CANCELLED, Order.State.DELIVERED])
    for order in active:
        conditions = []
        if not order.customer_phone or not order.full_address.strip():
            conditions.append(
                ("MISSING_CONTACT", "Customer phone or address is missing.", "Supply Chain")
            )
        if order.area_id is None:
            conditions.append(
                (
                    "AREA_REVIEW",
                    "The original area needs a normalized route mapping.",
                    "Supply Chain",
                )
            )
        if order.delivery_date == day and not order.courier_id:
            conditions.append(
                ("UNASSIGNED", "Today's order has no assigned courier.", "Supply Chain")
            )
        if order.delivery_date <= day and not order.warehouse_ready:
            conditions.append(
                ("WAREHOUSE_NOT_READY", "Warehouse readiness is overdue or due today.", "Warehouse")
            )
        if order.delivery_date < day:
            conditions.append(("OVERDUE", "Delivery is overdue.", "Supply Chain"))
        for kind, message, role in conditions:
            key = f"daily:{kind}:{order.pk}"
            live_keys.add(key)
            Alert.objects.update_or_create(
                key=key,
                defaults={
                    "kind": kind,
                    "message": message,
                    "target_role": role,
                    "order": order,
                    "resolved_at": None,
                },
            )
    for batch in ProductionBatch.objects.select_related("item"):
        pending = batch.allocations.filter(
            completed_at__isnull=True,
            order_line__active=True,
            order_line__order__review_required=False,
        ).exclude(order_line__order__state__in=[Order.State.CANCELLED, Order.State.DELIVERED])
        if pending.filter(order_line__fresh=True).exists() and day >= work_calendar().previous(
            batch.due_date
        ):
            key = f"fresh:{batch.pk}"
            live_keys.add(key)
            amount = sum(pending.filter(order_line__fresh=True).values_list("quantity", flat=True))
            Alert.objects.update_or_create(
                key=key,
                defaults={
                    "kind": "FRESH_PRODUCTION_DUE",
                    "target_role": "Warehouse",
                    "message": f"Prepare {amount} units of {batch.item.name} for {batch.due_date}.",
                    "resolved_at": None,
                },
            )
    Alert.objects.filter(Q(key__startswith="daily:") | Q(key__startswith="fresh:")).exclude(
        key__in=live_keys
    ).filter(resolved_at__isnull=True).update(resolved_at=timezone.now())


@shared_task(soft_time_limit=110, time_limit=120)
def process_finance_completion(pk):
    from .finance_completion import process

    return process(pk)


@shared_task
def sweep_finance_completions():
    if (
        os.environ.get("TRIAL_MODE") == "true"
        or os.environ.get("ODOO_COMPLETION_ENABLED") != "true"
    ):
        return
    from .models import FinanceCompletion

    now = timezone.now()
    jobs = FinanceCompletion.objects.filter(
        Q(odoo_status="PENDING")
        | Q(odoo_status="RUNNING", claimed_at__lt=now - timedelta(minutes=5))
    )
    for pk in jobs.values_list("pk", flat=True)[:100]:
        process_finance_completion.delay(str(pk))
