import base64
import hashlib
import hmac
import json
import os
import uuid
from datetime import date, timedelta
from decimal import Decimal

from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from rest_framework.decorators import api_view
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from apps.identity.permissions import require_role

from .common import area_for, audit, business_today, work_calendar
from .models import (
    Alert,
    Customer,
    CustomerAddress,
    ExternalMapping,
    IntegrationJob,
    Item,
    Location,
    Order,
    ShopifyReference,
    WebhookInbox,
    WorkPolicy,
)
from .orders import cancel, create_order
from .providers import IntegrationError, Odoo, TransientError, UnknownCreateResult


@require_POST
@csrf_exempt
def shopify_webhook(request):
    configured = os.environ.get("SHOPIFY_CLIENT_SECRET", "")
    store = os.environ.get("SHOPIFY_SHOP_DOMAIN", "").lower()
    if not configured or not store:
        return JsonResponse({"detail": "Integration is not configured."}, status=503)
    expected = base64.b64encode(
        hmac.new(configured.encode(), request.body, hashlib.sha256).digest()
    ).decode()
    if (
        not hmac.compare_digest(
            expected.encode(), request.headers.get("X-Shopify-Hmac-Sha256", "").encode()
        )
        or request.headers.get("X-Shopify-Shop-Domain", "").lower() != store
    ):
        return JsonResponse({"detail": "Invalid webhook authentication."}, status=401)
    event_id = request.headers.get("X-Shopify-Event-Id") or request.headers.get(
        "X-Shopify-Webhook-Id"
    )
    topic = request.headers.get("X-Shopify-Topic", "")
    if (
        not event_id
        or len(event_id) > 160
        or topic not in {"orders/create", "orders/updated", "orders/cancelled"}
    ):
        return JsonResponse({"detail": "Unsupported or missing webhook metadata."}, status=400)
    try:
        payload = json.loads(request.body)
        if not isinstance(payload, dict):
            raise ValueError
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({"detail": "Invalid payload."}, status=400)
    envelope = {key: payload[key] for key in ["id", "admin_graphql_api_id"] if key in payload}
    WebhookInbox.objects.get_or_create(
        store=store, event_id=event_id, defaults={"payload": envelope, "topic": topic}
    )
    # A periodic database sweep dispatches this durable inbox; no broker dependency at acknowledgment.
    return JsonResponse({"accepted": True}, status=202)


def due_date(node):
    policy = WorkPolicy.objects.filter(name="default").first()
    tag = policy.shopify_due_tag if policy else "delivery-today"
    field = os.environ.get("SHOPIFY_DELIVERY_DATE_ATTRIBUTE", "delivery_date")
    attrs = {entry["key"]: entry["value"] for entry in node.get("customAttributes", [])}
    if attrs.get(field):
        try:
            return work_calendar().on_or_after(date.fromisoformat(attrs[field]))
        except ValueError as exc:
            raise IntegrationError("Shopify delivery date attribute is invalid.") from exc
    if tag in node.get("tags", []):
        return work_calendar().on_or_after(business_today())
    return None


@transaction.atomic
def import_shopify(node, store):
    updated = parse_datetime(node["updatedAt"])
    if updated is None or timezone.is_naive(updated):
        raise IntegrationError("Shopify updated timestamp is missing or invalid.")
    reference = ShopifyReference.objects.filter(store=store, external_id=node["id"]).first()
    if reference is None:
        if node.get("cancelledAt") or due_date(node) is None:
            return None
        reference, _ = ShopifyReference.objects.get_or_create(
            store=store, external_id=node["id"], defaults={"order_number": node["name"]}
        )
    # Lock the unique upstream identity before the order and warehouse. A new reference
    # is linked inside this same transaction, so no unlinked claim is committed.
    reference = ShopifyReference.objects.select_for_update().get(pk=reference.pk)
    created = parse_datetime(node.get("createdAt") or "")
    if created is not None and timezone.is_aware(created) and reference.order_created_at != created:
        reference.order_created_at = created
        reference.save(update_fields=["order_created_at"])
    existing = reference if reference.order_id else None
    if existing:
        order = Order.objects.select_for_update().get(pk=existing.order_id)
        if existing.updated_at and updated <= existing.updated_at:
            return order
        if node.get("cancelledAt") and order.state not in {
            Order.State.CANCELLED,
            Order.State.DELIVERED,
        }:
            cancel(order.pk, order.version, None, "Shopify cancelled the upstream order")
        elif order.state not in {Order.State.CANCELLED, Order.State.DELIVERED}:
            live_lines, internal_lines = {}, {}
            for entry in node["lineItems"]["nodes"]:
                sku = (entry.get("variant") or {}).get("sku") or entry["sku"]
                price = Decimal(entry["originalUnitPriceSet"]["shopMoney"]["amount"])
                key = (sku, price)
                live_lines[key] = live_lines.get(key, 0) + int(
                    entry.get("currentQuantity", entry["quantity"])
                )
            live_lines = {key: quantity for key, quantity in live_lines.items() if quantity}
            for line in order.lines.all():
                key = (line.sku, line.unit_price)
                internal_lines[key] = internal_lines.get(key, 0) + line.quantity
            upstream_total = Decimal(node["currentTotalPriceSet"]["shopMoney"]["amount"])
            if not upstream_total.is_finite() or upstream_total < 0:
                raise IntegrationError("Shopify commercial total is invalid.")
            commercial_change = live_lines != internal_lines or upstream_total != order.total
            if commercial_change:
                Alert.objects.update_or_create(
                    key=f"shopify-change:{order.pk}",
                    defaults={
                        "kind": "SHOPIFY_ORDER_CHANGE",
                        "order": order,
                        "message": "Shopify products, quantities, prices or total changed. The order is held for commercial/stock reconciliation.",
                        "target_role": "Supply Chain",
                        "resolved_at": None,
                    },
                )
                order.review_required, order.warehouse_ready = True, False
            else:
                Alert.objects.filter(key=f"shopify-change:{order.pk}").update(
                    resolved_at=timezone.now()
                )
                order.review_required = Alert.objects.filter(
                    key=f"payment-change:{order.pk}", resolved_at__isnull=True
                ).exists()
            paid = node.get("displayFinancialStatus", "UNPAID")
            outstanding = Decimal(node["totalOutstandingSet"]["shopMoney"]["amount"])
            if not outstanding.is_finite() or outstanding < 0 or outstanding > upstream_total:
                raise IntegrationError("Invalid Shopify outstanding amount.")
            before = {
                "payment_status": order.payment_status,
                "expected_collection": order.expected_collection,
            }
            if order.stock_issued and outstanding != order.expected_collection:
                Alert.objects.update_or_create(
                    key=f"payment-change:{order.pk}",
                    defaults={
                        "kind": "PAYMENT_REVIEW",
                        "order": order,
                        "message": "Payment changed after dispatch. Finance must approve the courier's remaining collection.",
                        "target_role": "Finance",
                        "resolved_at": None,
                    },
                )
                order.review_required = True
            elif not commercial_change:
                order.payment_status, order.expected_collection = paid, outstanding
                audit(
                    None,
                    "shopify.payment_updated",
                    order,
                    before,
                    {"payment_status": paid, "expected_collection": outstanding},
                )
            order.version += 1
            order.save(
                update_fields=[
                    "payment_status",
                    "expected_collection",
                    "version",
                    "review_required",
                    "warehouse_ready",
                ]
            )
            if order.review_required:
                audit(
                    None,
                    "shopify.review_required",
                    order,
                    after={"commercial_change": commercial_change},
                )
        elif Decimal(
            node["currentTotalPriceSet"]["shopMoney"]["amount"]
        ) != order.total or node.get("cancelledAt"):
            Alert.objects.update_or_create(
                key=f"closed-payment:{order.pk}",
                defaults={
                    "kind": "CLOSED_PAYMENT_REVIEW",
                    "order": order,
                    "message": "Shopify changed a closed order. Finance must reconcile it; recorded delivery and collection were preserved.",
                    "target_role": "Finance",
                    "resolved_at": None,
                },
            )
        existing.commercial_snapshot = quotation_commercial_snapshot(node)
        existing.updated_at = updated
        existing.tags = node.get("tags", [])
        existing.fulfillment_status = node.get("displayFulfillmentStatus", "")
        existing.payment_gateways = node.get("paymentGatewayNames", [])
        existing.save(
            update_fields=[
                "updated_at",
                "tags",
                "fulfillment_status",
                "payment_gateways",
                "commercial_snapshot",
            ]
        )
        return order
    if node.get("cancelledAt"):
        return None
    due = due_date(node)
    if due is None:
        return None
    location_id = os.environ.get("SHOPIFY_LOCATION_ID", "")
    try:
        location = Location.objects.select_for_update().get(pk=uuid.UUID(location_id), active=True)
    except (Location.DoesNotExist, ValueError) as exc:
        raise IntegrationError("Configure Shopify's internal warehouse location.") from exc
    shipping = node.get("shippingAddress")
    if not shipping:
        raise IntegrationError("Shopify shipping address is missing or redacted.")
    if node["lineItems"]["pageInfo"]["hasNextPage"]:
        raise IntegrationError("Extended line pagination is required for this order.")
    prepared = []
    for entry in node["lineItems"]["nodes"]:
        quantity = Decimal(entry.get("currentQuantity", entry["quantity"]))
        if quantity == 0:
            continue
        sku = (entry.get("variant") or {}).get("sku") or entry.get("sku")
        item = Item.objects.filter(sku=sku, active=True, kind=Item.Kind.FINISHED).first()
        if not item:
            raise IntegrationError("Shopify order contains an unmapped or inactive product SKU.")
        prepared.append(
            {
                "item": item,
                "quantity": quantity,
                "unit_price": Decimal(entry["originalUnitPriceSet"]["shopMoney"]["amount"]),
            }
        )
    if not prepared or any(entry["quantity"] <= 0 for entry in prepared):
        raise IntegrationError("Shopify order has invalid line quantities.")
    external_customer = (node.get("customer") or {}).get("id") or node["id"]
    identity = f"{store}|{external_customer}"
    mapping = ExternalMapping.objects.filter(
        provider="SHOPIFY", entity_type="customer", external_id=identity
    ).first()
    if mapping:
        customer = Customer.objects.get(pk=mapping.entity_id)
    else:
        customer = Customer.objects.create(
            name=shipping.get("name")
            or (node.get("customer") or {}).get("displayName")
            or "Customer",
            phone=shipping.get("phone") or node.get("phone") or "",
        )
        ExternalMapping.objects.create(
            provider="SHOPIFY", entity_type="customer", external_id=identity, entity_id=customer.pk
        )
    original = shipping.get("city") or shipping.get("province") or ""
    address = CustomerAddress.objects.create(
        customer=customer,
        branch_code=f"SHOP-{node['id'].split('/')[-1]}",
        full_address=", ".join(
            str(shipping.get(key) or "")
            for key in ["address1", "address2", "city", "province", "country", "zip"]
            if shipping.get(key)
        ),
        original_area=original,
        area=area_for(original),
        phone=shipping.get("phone") or node.get("phone") or customer.phone,
        latitude=shipping.get("latitude"),
        longitude=shipping.get("longitude"),
    )
    # Commercial total includes shipping, taxes and discounts; preserve Shopify's actual totals.
    data = {
        "command_key": f"shopify:{store}:{node['id']}",
        "customer": customer,
        "address": address,
        "location": location,
        "delivery_date": due,
        "lines": prepared,
        "notes": node.get("note") or "",
        "payment_method": "ONLINE" if node.get("displayFinancialStatus") == "PAID" else "COD",
        "payment_status": node.get("displayFinancialStatus") or "UNPAID",
        "currency": node["currencyCode"],
        "expected_collection": Decimal(0),
    }
    order = create_order(data, None, Order.Source.SHOPIFY)
    order.total = Decimal(node["currentTotalPriceSet"]["shopMoney"]["amount"])
    order.expected_collection = Decimal(node["totalOutstandingSet"]["shopMoney"]["amount"])
    if order.total < 0 or order.expected_collection < 0 or order.expected_collection > order.total:
        raise IntegrationError("Invalid Shopify commercial totals.")
    order.save(update_fields=["total", "expected_collection"])
    reference.commercial_snapshot = quotation_commercial_snapshot(node)
    reference.order = order
    reference.updated_at = updated
    reference.tags = node.get("tags", [])
    reference.fulfillment_status = node.get("displayFulfillmentStatus", "")
    reference.payment_gateways = node.get("paymentGatewayNames", [])
    reference.save(
        update_fields=[
            "order",
            "updated_at",
            "tags",
            "fulfillment_status",
            "payment_gateways",
            "commercial_snapshot",
        ]
    )
    audit(None, "shopify.imported", order, after={"shopify_id": node["id"]})
    return order


def mapped(entity_type, entity_id):
    mapping = ExternalMapping.objects.filter(
        provider="ODOO", entity_type=entity_type, entity_id=entity_id
    ).first()
    if mapping is None or not mapping.external_id.isascii() or not mapping.external_id.isdecimal():
        raise IntegrationError(f"Configure the Odoo {entity_type} mapping.")
    return int(mapping.external_id)


def quotation_commercial_snapshot(node):
    keys = (
        "currencyCode",
        "taxesIncluded",
        "currentSubtotalPriceSet",
        "currentTotalTaxSet",
        "currentTotalDiscountsSet",
        "totalShippingPriceSet",
        "currentTotalPriceSet",
    )
    result = {key: node[key] for key in keys if key in node}
    result["lines"] = [
        {
            key: line[key]
            for key in ("quantity", "currentQuantity", "originalUnitPriceSet", "taxLines")
            if key in line
        }
        for line in node.get("lineItems", {}).get("nodes", [])
        if line.get("currentQuantity", line.get("quantity", 0))
    ]
    return result


def verify_exclusive_tax_breakdown(order):
    """Accept separate VAT only with a complete, reconciled Shopify source breakdown.

    Shipping, discounts, included taxes, missing fields and inconsistent source lines
    remain review holds. Odoo's own final total must still match after creation.
    """
    message = "Shopify shipping, discount or tax mapping needs commercial review before quotation creation."
    source = ShopifyReference.objects.get(order=order).commercial_snapshot
    try:

        def money(key):
            value = Decimal(source[key]["shopMoney"]["amount"])
            if not value.is_finite() or value < 0:
                raise ValueError
            return value

        if source["taxesIncluded"] is not False or source["currencyCode"] != order.currency:
            raise ValueError
        subtotal, tax = money("currentSubtotalPriceSet"), money("currentTotalTaxSet")
        if money("totalShippingPriceSet") or money("currentTotalDiscountsSet"):
            raise ValueError
        if (subtotal + tax).quantize(Decimal(".01")) != order.total or money(
            "currentTotalPriceSet"
        ) != order.total:
            raise ValueError
        expected = sorted((line.quantity, line.unit_price) for line in order.lines.all())
        actual = []
        taxes = Decimal(0)
        for line in source["lines"]:
            qty = Decimal(str(line.get("currentQuantity", line["quantity"])))
            unit = Decimal(line["originalUnitPriceSet"]["shopMoney"]["amount"])
            if not qty.is_finite() or qty <= 0 or not unit.is_finite() or unit < 0:
                raise ValueError
            actual.append((qty, unit))
            for row in line["taxLines"]:
                amount = Decimal(row["priceSet"]["shopMoney"]["amount"])
                if not amount.is_finite() or amount < 0:
                    raise ValueError
                taxes += amount
        if (
            sorted(actual) != expected
            or sum((q * p for q, p in actual), Decimal(0)).quantize(Decimal(".01")) != subtotal
        ):
            raise ValueError
        if taxes.quantize(Decimal(".01")) != tax:
            raise ValueError
    except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
        raise IntegrationError(message) from exc


def verify_quotation(record, order):
    currency = record.get("currency_id")
    if not isinstance(currency, list) or len(currency) != 2 or currency[1] != order.currency:
        raise IntegrationError("Odoo quotation currency needs pricelist/commercial mapping review.")
    try:
        amount = Decimal(str(record["amount_total"]))
    except (KeyError, ValueError, ArithmeticError) as exc:
        raise IntegrationError("Odoo quotation total could not be verified.") from exc
    if not amount.is_finite() or amount.quantize(Decimal("0.01")) != order.total:
        raise IntegrationError(
            "Odoo quotation total differs. Review taxes, discounts and commercial mappings."
        )


def sync_odoo(job_id, adapter=None):
    from .models import MarketQuotation

    if MarketQuotation.objects.filter(order__odoo__pk=job_id).exists():
        return IntegrationJob.objects.get(pk=job_id)
    # Commit a claim before external effects so a worker crash cannot erase evidence of an attempt.
    with transaction.atomic():
        order_id = IntegrationJob.objects.values_list("order_id", flat=True).get(pk=job_id)
        Order.objects.select_for_update().get(pk=order_id)
        job = IntegrationJob.objects.select_for_update().select_related("order").get(pk=job_id)
        if (
            job.status == "SYNCED"
            or job.order.delivery_date > business_today()
            or job.order.state == Order.State.CANCELLED
            or job.order.review_required
            or (
                os.environ.get("SHOPIFY_QUOTATION_BATCH_ENABLED") == "true"
                and job.order.source == Order.Source.SHOPIFY
                and job.website_batch_date is None
            )
        ):
            return job
        if (
            job.status in {"CLAIMED", "CREATING"}
            and job.claimed_at
            and job.claimed_at > timezone.now() - timedelta(minutes=5)
        ):
            return job
        previous_status = "UNKNOWN" if job.status in {"CLAIMED", "CREATING"} else job.status
        job.status, job.claimed_at = "CLAIMED", timezone.now()
        job.attempts += 1
        job.save()
    remote_create_attempted = False
    try:
        adapter = adapter or Odoo()
        key = f"OPSDEMO:{job.order_id}"
        found = adapter.find(key)
        if len(found) > 1:
            raise IntegrationError("Multiple matching Odoo quotations need reconciliation.")
        if found:
            job.external_id = str(found[0]["id"])
            job.external_number = found[0]["name"]
            verify_quotation(found[0], job.order)
        else:
            if previous_status == "UNKNOWN":
                raise UnknownCreateResult(
                    "No quotation found yet. Review the unknown result before allowing recreation."
                )
            website_partner = (
                os.environ.get("ODOO_WEBSITE_PARTNER_ID", "").strip()
                if job.order.source == Order.Source.SHOPIFY
                else ""
            )
            if website_partner and (not website_partner.isdigit() or int(website_partner) <= 0):
                raise IntegrationError("Configure a valid website cash customer partner record ID.")
            values = {
                "partner_id": int(website_partner)
                if website_partner
                else mapped("customer", job.order.customer_id),
                "partner_shipping_id": int(website_partner)
                if website_partner
                else mapped("address", job.order.address_id),
                "client_order_ref": key,
                "order_line": [
                    [
                        0,
                        0,
                        {
                            "product_id": mapped("item", line.item_id),
                            "name": line.name,
                            "product_uom_qty": float(line.quantity),
                            "price_unit": float(line.unit_price),
                        },
                    ]
                    for line in job.order.lines.all()
                ],
            }
            if job.order.source == Order.Source.SHOPIFY:
                if website_partner:
                    source = ShopifyReference.objects.filter(order=job.order).first()
                    if not source or not source.order_number:
                        raise IntegrationError(
                            "Website order number is missing; review before quotation creation."
                        )
                    values.update(
                        partner_id=int(website_partner),
                        partner_invoice_id=int(website_partner),
                        partner_shipping_id=int(website_partner),
                        client_order_ref=source.order_number,
                        origin=key,
                    )
                line_total = sum(
                    (line.quantity * line.unit_price for line in job.order.lines.all()), Decimal(0)
                )
                if line_total.quantize(Decimal("0.01")) != job.order.total:
                    verify_exclusive_tax_breakdown(job.order)
            IntegrationJob.objects.filter(pk=job.pk).update(status="CREATING")
            remote_create_attempted = True
            job.external_id = str(adapter.create(values))
            confirmed = adapter.find(key)
            if len(confirmed) != 1 or str(confirmed[0]["id"]) != job.external_id:
                raise UnknownCreateResult(
                    "Quotation created but its reference could not be verified yet."
                )
            job.external_number = confirmed[0]["name"]
            verify_quotation(confirmed[0], job.order)
            if confirmed[0].get("state") != "draft":
                raise IntegrationError(
                    "The created Odoo quotation is not draft; review server customization."
                )
        job.status, job.error, job.next_attempt_at = "SYNCED", "", None
        job.last_success_at = timezone.now()
        audit(
            None,
            "odoo.quotation_linked",
            job.order,
            after={"odoo_id": job.external_id, "number": job.external_number},
        )
        Alert.objects.filter(key=f"odoo:{job.order_id}").update(resolved_at=timezone.now())
    except UnknownCreateResult as exc:
        job.status, job.error = "UNKNOWN", str(exc)
        job.next_attempt_at = timezone.now() + timedelta(minutes=10)
    except (IntegrationError, ValidationError) as exc:
        job.status = "UNKNOWN" if remote_create_attempted or job.external_id else "ERROR"
        job.error = str(exc)[:500]
        job.next_attempt_at = (
            timezone.now() + timedelta(seconds=min(3600, 30 * 2 ** min(job.attempts, 7)))
            if isinstance(exc, TransientError)
            else None
        )
    job.save()
    if job.status != "SYNCED":
        Alert.objects.update_or_create(
            key=f"odoo:{job.order_id}",
            defaults={
                "kind": "ODOO_SYNC_FAILURE",
                "order": job.order,
                "target_role": "Supply Chain",
                "message": job.error,
                "resolved_at": None,
            },
        )
    return job


@api_view(["GET"])
def integration_status(request):
    require_role(request.user, "Supply Chain")
    return Response(
        {
            "shopify_configured": bool(
                os.environ.get("SHOPIFY_SHOP_DOMAIN") and os.environ.get("SHOPIFY_API_VERSION")
            ),
            "odoo_configured": bool(
                os.environ.get("ODOO_BASE_URL") and os.environ.get("ODOO_VERSION")
            ),
            "jobs": list(
                IntegrationJob.objects.select_related("order")
                .order_by("created_at")
                .values(
                    "id",
                    "order_id",
                    "order__number",
                    "status",
                    "attempts",
                    "error",
                    "external_id",
                    "external_number",
                    "last_success_at",
                )[:200]
            ),
            "inbox_errors": list(
                WebhookInbox.objects.exclude(error="").values("id", "topic", "error", "created_at")[
                    :100
                ]
            ),
        }
    )


@api_view(["POST"])
@transaction.atomic
def retry_shopify(request, event_id):
    require_role(request.user, "Supply Chain")
    from django.shortcuts import get_object_or_404

    event = get_object_or_404(WebhookInbox.objects.select_for_update(), pk=event_id)
    if not event.processed_at:
        event.error, event.next_attempt_at = "", timezone.now()
        event.save(update_fields=["error", "next_attempt_at"])
        audit(request.user, "shopify.retry_requested", event)
    return Response({"queued": event.processed_at is None})


@api_view(["POST"])
@transaction.atomic
def retry_odoo(request, order_id):
    require_role(request.user, "Supply Chain")
    from django.shortcuts import get_object_or_404

    job = get_object_or_404(IntegrationJob.objects.select_for_update(), order_id=order_id)
    if job.order.state == Order.State.CANCELLED:
        raise ValidationError(
            "Cancelled orders cannot start another quotation attempt. Review any existing remote document separately."
        )
    if job.status in {"CLAIMED", "CREATING"}:
        raise ValidationError(
            "The worker is processing or reconciling this request. Retry after its current attempt completes."
        )
    if job.status == "UNKNOWN":
        # Still reconciliation-only; this action cannot turn an unknown create into a new create.
        job.next_attempt_at = timezone.now()
    elif job.status != "SYNCED":
        job.status, job.next_attempt_at = "PENDING", timezone.now()
    job.save(update_fields=["status", "next_attempt_at"])
    audit(request.user, "odoo.retry_requested", job.order, after={"status": job.status})
    return Response({"queued": True, "status": job.status})
