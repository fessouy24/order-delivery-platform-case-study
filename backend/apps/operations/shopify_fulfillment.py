"""Durable website-only fulfillment outbox. No remote mutations inside delivery transactions."""

import os
import uuid
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from apps.identity.permissions import require_role

from . import fulfillment_transport as transport
from .courier_cash import website_cod
from .models import Alert, CourierCashEntry, Order, ShopifyFulfillmentJob
from .providers import IntegrationError, Shopify, TransientError, UnknownCreateResult


def eligible(order, gate):
    if (
        order.source != Order.Source.SHOPIFY
        or order.state != Order.State.DELIVERED
        or order.review_required
    ):
        return False
    if gate == "COD_SETTLED":
        return (
            website_cod(order)
            and CourierCashEntry.objects.filter(order=order, settlement__isnull=False).exists()
        )
    return (
        gate == "ONLINE_PAID"
        and order.payment_method == "ONLINE"
        and order.payment_status == "PAID"
        and order.expected_collection == 0
    )


def queue_fulfillment(order, gate):
    if not eligible(order, gate) or not hasattr(order, "shopify"):
        return None
    job, _ = ShopifyFulfillmentJob.objects.get_or_create(order=order, defaults={"gate": gate})
    return job


def alert(job, message):
    Alert.objects.update_or_create(
        key=f"shopify-fulfillment:{job.order_id}",
        defaults={
            "kind": "SHOPIFY_FULFILLMENT",
            "order_id": job.order_id,
            "target_role": "Supply Chain",
            "message": message[:500],
            "resolved_at": None,
        },
    )


def verify_contents(order, current):
    local = defaultdict(Decimal)
    remote = defaultdict(Decimal)
    for line in order.lines.all():
        local[line.sku] += line.quantity
    for sku, quantity in current["order_lines"]:
        if quantity:
            remote[sku] += Decimal(quantity)
    if (
        dict(local) != dict(remote)
        or Decimal(current["money"]["amount"]) != order.total
        or current["money"]["currencyCode"] != order.currency
    ):
        raise IntegrationError(
            "Shopify products, quantities or commercial total differ from the delivered order. Review before fulfillment."
        )


def process(job_id):
    if (
        os.environ.get("TRIAL_MODE") == "true"
        or os.environ.get("SHOPIFY_FULFILLMENT_ENABLED") != "true"
    ):
        return
    with transaction.atomic():
        job = (
            ShopifyFulfillmentJob.objects.select_for_update(of=("self",))
            .select_related("order", "order__shopify")
            .get(pk=job_id)
        )
        if job.status == "SUCCEEDED":
            return
        if job.claimed_at and job.claimed_at > timezone.now() - timedelta(minutes=5):
            return
        if not eligible(job.order, job.gate):
            job.status = "REVIEW"
            job.error = "Delivery, payment or cash handover needs review."
            job.save()
            alert(job, job.error)
            return
        job.claim_token = uuid.uuid4()
        job.claimed_at = timezone.now()
        job.attempts += 1
        job.status = "CLAIMED"
        job.save()
    try:
        client = Shopify()
        reference = job.order.shopify
        if client.store != reference.store:
            raise IntegrationError("Shopify order belongs to a different store.")
        transport.permissions(client)
        current = transport.snapshot(client, reference.external_id)
        verify_contents(job.order, current)
        if job.gate == "ONLINE_PAID" and current["financial_status"] != "PAID":
            raise IntegrationError("Shopify no longer reports the online order as fully paid.")
        if not job.plan:
            job.plan = current
            job.save(update_fields=["plan"])
        lines = transport.remaining_plan(job.plan, current)
        if job.pending_group:
            pending_ids = {
                line["id"]
                for group in job.pending_group
                for line in group["fulfillmentOrderLineItems"]
            }
            if pending_ids & {line["id"] for line in lines}:
                raise UnknownCreateResult(
                    "An earlier fulfillment may still be processing. Check Shopify before retrying; cash handover remains saved."
                )
            job.pending_group = []
            job.save(update_fields=["pending_group"])
        for group in transport.groups(lines):
            # Recheck current payment/order state immediately before every mutation.
            job.order.refresh_from_db()
            if not eligible(job.order, job.gate):
                raise IntegrationError("Local order changed before Shopify fulfillment.")
            fresh = transport.snapshot(client, reference.external_id)
            verify_contents(job.order, fresh)
            transport.remaining_plan(job.plan, fresh)
            if job.gate == "ONLINE_PAID" and fresh["financial_status"] != "PAID":
                raise IntegrationError("Online payment needs review before fulfillment.")
            available = {line["id"] for line in fresh["lines"]}
            ids = {line["id"] for fo in group for line in fo["fulfillmentOrderLineItems"]}
            if not ids & available:
                continue
            if not ids <= available:
                raise IntegrationError(
                    "Shopify fulfillment group changed; review before continuing."
                )
            owned = ShopifyFulfillmentJob.objects.filter(
                pk=job.pk, claim_token=job.claim_token
            ).update(pending_group=group, status="SUBMITTING", claimed_at=timezone.now())
            if not owned:
                raise UnknownCreateResult(
                    "Another worker owns fulfillment reconciliation. Refresh status."
                )
            job.pending_group = group
            job.status = "SUBMITTING"
            try:
                remote_id = transport.create(client, group)
            except UnknownCreateResult:
                raise
            except IntegrationError:
                job.pending_group = []
                ShopifyFulfillmentJob.objects.filter(pk=job.pk, claim_token=job.claim_token).update(
                    pending_group=[]
                )
                raise
            job.remote_ids.append(remote_id)
            job.pending_group = []
            job.status = "CLAIMED"
            ShopifyFulfillmentJob.objects.filter(pk=job.pk, claim_token=job.claim_token).update(
                remote_ids=job.remote_ids, pending_group=[], status="CLAIMED"
            )
        final = transport.snapshot(client, reference.external_id)
        if not final["fulfilled"]:
            raise TransientError("Shopify has not confirmed the entire order fulfilled yet.")
        job.status = "SUCCEEDED"
        job.completed_at = timezone.now()
        job.error = ""
        job.next_attempt_at = None
        Alert.objects.filter(key=f"shopify-fulfillment:{job.order_id}").update(
            resolved_at=timezone.now()
        )
    except UnknownCreateResult as exc:
        job.status = "UNKNOWN"
        job.error = str(exc)
        job.next_attempt_at = None
        alert(job, job.error)
    except IntegrationError as exc:
        job.status = "ERROR"
        job.error = str(exc)[:500]
        job.next_attempt_at = (
            timezone.now() + timedelta(seconds=min(3600, 30 * 2 ** min(job.attempts, 7)))
            if isinstance(exc, TransientError)
            else None
        )
        alert(job, job.error)
    except Exception:
        # An unexpected response or worker interruption must never leave an
        # unclaimable job, expose provider secrets, or resubmit an uncertain write.
        job.status = "UNKNOWN" if job.pending_group else "ERROR"
        job.error = "Unexpected fulfillment failure. Review Shopify before retrying; cash handover remains saved."
        job.next_attempt_at = None
        alert(job, job.error)
    finally:
        ShopifyFulfillmentJob.objects.filter(pk=job.pk, claim_token=job.claim_token).update(
            status=job.status,
            claimed_at=None,
            error=job.error,
            next_attempt_at=job.next_attempt_at,
            completed_at=job.completed_at,
        )


@api_view(["POST"])
def retry(request, order_id):
    require_role(request.user, "Supply Chain", "Finance")
    job = ShopifyFulfillmentJob.objects.filter(order_id=order_id).first()
    if not job:
        raise ValidationError("No fulfillment job exists for this order.")
    if job.claimed_at:
        raise ValidationError("Fulfillment is already being checked. Refresh before retrying.")
    if job.status != "SUCCEEDED":
        job.status = "PENDING"
        job.next_attempt_at = None
        job.save(update_fields=["status", "next_attempt_at"])
    return Response({"status": job.status})
