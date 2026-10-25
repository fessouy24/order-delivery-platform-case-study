"""Odoo quotation inbox: no operational dates until staff schedule a reservation."""

import json
import os
import uuid
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from apps.identity.permissions import require_role

from .common import audit
from .models import (
    Alert,
    Customer,
    CustomerAddress,
    ExternalMapping,
    IntegrationJob,
    Item,
    MarketQuotation,
)
from .orders import create_order
from .providers import IntegrationError, Odoo
from .serializers import NewOrder

FIELDS = [
    "id",
    "name",
    "state",
    "company_id",
    "partner_id",
    "partner_shipping_id",
    "client_order_ref",
    "origin",
    "amount_total",
    "currency_id",
    "order_line",
    "write_date",
]


def approved_pairs():
    try:
        values = json.loads(os.environ.get("ODOO_MARKET_APPROVED_PAIRS", "[]"))
        if not isinstance(values, list) or any(
            not isinstance(pair, list)
            or len(pair) != 2
            or any(type(value) is not int or value <= 0 for value in pair)
            for pair in values
        ):
            raise ValueError
        return {tuple(pair) for pair in values}
    except (ValueError, TypeError):
        raise IntegrationError(
            "Configure approved market customer/branch record ID pairs."
        ) from None


def is_approved(quote):
    customer = quote.get("partner_id") or [None]
    branch = quote.get("partner_shipping_id") or [None]
    return (customer[0], branch[0]) in approved_pairs()


@transaction.atomic
def store_quotation(database, quote):
    tracked = MarketQuotation.objects.filter(
        database=database, company_id=quote["company_id"][0], quotation_id=quote["id"]
    ).exists()
    if not is_approved(quote) and not tracked:
        return None
    # Website quotations, including ones created manually, do not belong in the market inbox.
    website = os.environ.get("ODOO_WEBSITE_PARTNER_ID", "")
    if (
        str(quote["partner_id"][0]) == website
        or (quote.get("origin") or "").startswith("OPSDEMO:")
        or (quote.get("origin") or "").startswith("OPSDEMO-TRAINING:")
    ):
        return None
    if (
        IntegrationJob.objects.filter(external_id=str(quote["id"])).exists()
        and not MarketQuotation.objects.filter(
            database=database, company_id=quote["company_id"][0], quotation_id=quote["id"]
        ).exists()
    ):
        return None
    row, _ = MarketQuotation.objects.get_or_create(
        database=database,
        company_id=quote["company_id"][0],
        quotation_id=quote["id"],
        defaults={"number": quote["name"]},
    )
    row = MarketQuotation.objects.select_for_update().get(pk=row.pk)

    def comparable(value):
        return {key: data for key, data in value.items() if key not in {"state", "write_date"}}

    own_confirmation = (
        row.order_id
        and hasattr(row.order, "finance_completion")
        and quote.get("state") in {"sale", "done"}
        and comparable(row.snapshot) == comparable(quote)
    )
    if row.order_id and row.snapshot != quote and not own_confirmation:
        row.order.review_required = True
        row.order.save(update_fields=["review_required"])
        Alert.objects.update_or_create(
            key=f"market:{row.pk}",
            defaults={
                "kind": "MARKET_CHANGED",
                "message": f"Odoo quotation {row.number} changed after scheduling; review the reservation.",
                "target_role": "Supply Chain",
                "resolved_at": None,
            },
        )
    row.snapshot = quote
    row.status = (
        "REVIEW"
        if row.order_id and row.order.review_required
        else (
            "SCHEDULED"
            if row.order_id
            else ("CANCELLED" if quote["state"] == "cancel" else "PENDING")
        )
    )
    row.save()
    return row


def poll_market(adapter=None):
    # An explicit company boundary is mandatory; brand names never filter products.
    company = os.environ.get("ODOO_MARKET_COMPANY_ID", "")
    if not company.isdigit() or int(company) <= 0 or not os.environ.get("ODOO_WEBSITE_PARTNER_ID"):
        raise IntegrationError(
            "Configure the market company and website cash customer IDs before polling."
        )
    client = adapter or Odoo()
    last_id = 0
    count = 0
    # Rescan the selected creation window, including edits/cancellations, on every poll.
    since = os.environ.get("ODOO_MARKET_IMPORT_FROM", "")
    if not approved_pairs():
        raise IntegrationError("Approve market customer/branch pairs before enabling imports.")
    if not since:
        raise IntegrationError(
            "Configure the first market import timestamp to avoid importing historical orders."
        )
    while True:
        quotes = client.call(
            "sale.order",
            "search_read",
            domain=[
                ["company_id", "=", int(company)],
                ["create_date", ">=", since],
                ["id", ">", last_id],
            ],
            fields=FIELDS,
            order="id asc",
            limit=50,
        )
        if not quotes:
            return count
        for quote in quotes:
            quote["lines"] = client.call(
                "sale.order.line",
                "search_read",
                domain=[["order_id", "=", quote["id"]]],
                fields=[
                    "id",
                    "product_id",
                    "name",
                    "product_uom_qty",
                    "product_uom",
                    "price_unit",
                    "discount",
                    "price_subtotal",
                    "price_total",
                    "display_type",
                ],
                order="id asc",
            )
            if store_quotation(client.database, quote):
                count += 1
        last_id = quotes[-1]["id"]


@api_view(["GET"])
def inbox(request):
    require_role(request.user, "Management", "Supply Chain")
    result = []
    for row in MarketQuotation.objects.exclude(status="CANCELLED").order_by("-created_at")[:500]:
        customers = list(
            ExternalMapping.objects.filter(
                provider="ODOO",
                entity_type="customer",
                external_id=str(row.snapshot["partner_id"][0]),
            ).values_list("entity_id", flat=True)[:2]
        )
        result.append(
            {
                "id": str(row.pk),
                "number": row.number,
                "status": row.status,
                "snapshot": row.snapshot,
                "delivery_customer_id": str(customers[0]) if len(customers) == 1 else None,
                "order": str(row.order_id) if row.order_id else None,
            }
        )
    return Response(result)


def mapping(kind, external, model):
    matches = list(
        ExternalMapping.objects.filter(
            provider="ODOO", entity_type=kind, external_id=str(external)
        ).values_list("entity_id", flat=True)[:2]
    )
    if len(matches) != 1:
        raise ValidationError(f"Verify the Odoo {kind} mapping for record {external}.")
    return get_object_or_404(model, pk=matches[0])


@api_view(["POST"])
@transaction.atomic
def schedule(request, quotation_id):
    require_role(request.user, "Supply Chain")
    row = get_object_or_404(MarketQuotation.objects.select_for_update(), pk=quotation_id)
    if row.order_id:
        return Response({"order": str(row.order_id)})
    quote = row.snapshot
    if not is_approved(quote):
        raise ValidationError("This customer/branch pair is not approved for market scheduling.")
    # Re-read before accepting a reservation; the background snapshot may be stale.
    try:
        client = Odoo()
    except IntegrationError as exc:
        raise ValidationError(str(exc)) from None
    if client.database != row.database:
        raise ValidationError("Quotation belongs to a different Odoo database.")
    try:
        latest = client.call(
            "sale.order",
            "search_read",
            domain=[["id", "=", row.quotation_id]],
            fields=["write_date", "state", "company_id"],
            limit=1,
        )
    except IntegrationError as exc:
        raise ValidationError(str(exc)) from None
    if (
        not latest
        or latest[0]["write_date"] != quote["write_date"]
        or latest[0]["state"] != quote["state"]
        or latest[0]["company_id"][0] != row.company_id
    ):
        raise ValidationError("Quotation changed; refresh its import before scheduling.")
    try:
        courier_id = uuid.UUID(str(request.data.get("courier")))
    except ValueError:
        raise ValidationError("Choose a courier for the reservation.") from None
    courier = get_object_or_404(
        get_user_model().objects.filter(is_active=True, groups__name="Courier").distinct(),
        pk=courier_id,
    )
    if quote.get("state") not in ["draft", "sent", "sale"]:
        raise ValidationError("This quotation cannot be scheduled.")
    lines = []
    for line in quote["lines"]:
        if line.get("display_type"):
            continue
        if not line.get("product_id") or not line.get("product_uom"):
            raise ValidationError(
                "Quotation contains a line without a product or unit; review it first."
            )
        item = mapping("item", line["product_id"][0], Item)
        # No silent carton or unit conversion.
        if not ExternalMapping.objects.filter(
            provider="ODOO",
            entity_type="item_uom",
            entity_id=item.pk,
            external_id=str(line["product_uom"][0]),
        ).exists():
            raise ValidationError(f"Approve the Odoo unit mapping for {item.name}.")
        qty = Decimal(str(line["product_uom_qty"]))
        if qty <= 0:
            raise ValidationError("Quotation contains a nonpositive quantity; review it first.")
        lines.append(
            {
                "item": str(item.pk),
                "quantity": str(qty),
                "unit_price": str(
                    (Decimal(str(line["price_subtotal"])) / qty).quantize(Decimal(".01"))
                ),
            }
        )
    customer = mapping("customer", quote["partner_id"][0], Customer)
    address = mapping("address", quote["partner_shipping_id"][0], CustomerAddress)
    original_address = address
    correction_reason = str(request.data.get("delivery_address_reason") or "").strip()
    if request.data.get("delivery_address"):
        from rest_framework import serializers

        address_id = serializers.UUIDField().run_validation(request.data["delivery_address"])
        address = get_object_or_404(CustomerAddress, pk=address_id, customer=customer)
        if address.pk != original_address.pk and not (3 <= len(correction_reason) <= 500):
            raise ValidationError(
                "Explain why the delivery branch differs from Odoo (3–500 characters)."
            )
    data = {
        **request.data,
        "command_key": f"market:{row.pk}",
        "customer": str(customer.pk),
        "address": str(address.pk),
        "lines": lines,
        "payment_method": "UNCONFIRMED",
        "payment_status": "UNPAID",
        "expected_collection": "0",
        "currency": quote["currency_id"][1],
        "notes": f"{quote['name']} / {quote.get('client_order_ref') or ''}",
    }
    serializer = NewOrder(data=data)
    serializer.is_valid(raise_exception=True)
    if not lines:
        raise ValidationError("Quotation has no deliverable product lines.")
    order = create_order(serializer.validated_data, request.user)
    order.total = Decimal(str(quote["amount_total"]))
    order.currency = quote["currency_id"][1]
    order.courier = courier
    order.save(update_fields=["total", "currency", "courier"])
    IntegrationJob.objects.filter(order=order).update(
        status="SYNCED",
        external_id=str(row.quotation_id),
        external_number=row.number,
        last_success_at=timezone.now(),
    )
    row.order, row.status = order, "SCHEDULED"
    row.save()
    audit(
        request.user,
        "market.reservation_scheduled",
        order,
        after={
            "quotation": row.number,
            "delivery_date": order.delivery_date,
            "odoo_shipping_partner": quote["partner_shipping_id"][0],
            "original_address": str(original_address.pk),
            "delivery_address": str(address.pk),
            "delivery_branch": address.branch_code,
            "branch_correction_reason": correction_reason
            if address.pk != original_address.pk
            else "",
        },
    )
    return Response({"order": str(order.pk)}, status=201)
