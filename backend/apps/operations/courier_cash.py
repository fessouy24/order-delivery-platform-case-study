"""Audited office cash handovers for delivered website COD orders only."""

import hashlib
import json
import uuid
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Sum
from rest_framework import serializers
from rest_framework.decorators import api_view
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from apps.identity.permissions import require_role

from .common import audit
from .models import CourierCashEntry, CourierSettlement, Order

CASH_METHODS = {"COD", "CASH", "CASH_ON_DELIVERY"}


def website_cod(order):
    return order.source == Order.Source.SHOPIFY and order.payment_method.upper() in CASH_METHODS


def outstanding(courier_id):
    return CourierCashEntry.objects.filter(
        courier_id=courier_id, settlement__isnull=True
    ).select_related("order", "order__shopify")


def balance(courier_id):
    return {
        row["currency"]: format(row["total"], ".2f")
        for row in outstanding(courier_id).values("currency").annotate(total=Sum("amount"))
    }


def statement(courier_id):
    entries = list(outstanding(courier_id).order_by("created_at", "pk"))
    return {
        "balance": balance(courier_id),
        "entries": [
            {
                "id": str(row.pk),
                "order_id": str(row.order_id),
                "number": row.order.shopify.order_number
                if hasattr(row.order, "shopify")
                else row.order.number,
                "amount": str(row.amount),
                "currency": row.currency,
                "delivered_at": row.order.delivered_at,
            }
            for row in entries
        ],
    }


@api_view(["GET"])
def my_cash(request):
    require_role(request.user, "Courier")
    return Response(statement(request.user.pk))


class SettleCommand(serializers.Serializer):
    command_key = serializers.CharField(max_length=160)
    currency = serializers.RegexField(r"^[A-Z]{3}$")
    entry_ids = serializers.ListField(
        child=serializers.UUIDField(), allow_empty=False, max_length=1000
    )
    amount = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=0)
    reference = serializers.CharField(max_length=250, required=False, allow_blank=True, default="")


@transaction.atomic
def settle(courier_id, data, actor):
    get_user_model().objects.select_for_update().get(pk=courier_id)
    data = {**data, "reference": data.get("reference", "")}
    fingerprint = hashlib.sha256(
        json.dumps(
            {**data, "courier": str(courier_id), "actor": str(actor.pk)},
            sort_keys=True,
            default=str,
        ).encode()
    ).hexdigest()
    old = CourierSettlement.objects.filter(command_key=data["command_key"]).first()
    if old:
        if old.fingerprint != fingerprint:
            raise ValidationError("This cash receipt was already used for different data.")
        return old
    entries = list(
        outstanding(courier_id)
        .select_for_update(of=("self",))
        .filter(currency=data["currency"])
        .order_by("pk")
    )
    ids = data["entry_ids"]
    if len(set(ids)) != len(ids) or set(ids) != {row.pk for row in entries}:
        raise ValidationError(
            "The courier cash balance changed. Refresh and review every outstanding order."
        )
    if any(
        row.order.review_required
        or row.order.state != Order.State.DELIVERED
        or not website_cod(row.order)
        for row in entries
    ):
        raise ValidationError(
            "Resolve the order or payment review before completing the cash handover."
        )
    total = sum((row.amount for row in entries), Decimal(0))
    if total != data["amount"]:
        raise ValidationError(
            "Received cash must match the reviewed outstanding balance. Do not clear a short payment."
        )
    receipt_id = uuid.uuid4()
    reference = data["reference"] or f"HANDOVER-{receipt_id.hex[:12].upper()}"
    receipt = CourierSettlement.objects.create(
        id=receipt_id,
        courier_id=courier_id,
        actor=actor,
        command_key=data["command_key"],
        fingerprint=fingerprint,
        currency=data["currency"],
        amount=total,
        reference=reference,
    )
    CourierCashEntry.objects.filter(pk__in=ids).update(settlement=receipt)
    from .finance_completion import finish
    from .shopify_fulfillment import queue_fulfillment

    for entry in entries:
        queue_fulfillment(entry.order, "COD_SETTLED")
        finish(entry.order_id, actor)
    audit(
        actor,
        "courier.cash_handed_over",
        receipt,
        after={
            "courier": courier_id,
            "currency": data["currency"],
            "amount": total,
            "orders": [str(row.order_id) for row in entries],
            "reference": receipt.reference,
        },
    )
    return receipt


@api_view(["POST"])
def complete(request, courier_id):
    require_role(request.user, "Finance")
    command = SettleCommand(data=request.data)
    command.is_valid(raise_exception=True)
    try:
        receipt = settle(courier_id, command.validated_data, request.user)
    except get_user_model().DoesNotExist:
        raise ValidationError("Courier was not found.")
    from .finance_completion import data
    from .models import FinanceCompletion

    completions = FinanceCompletion.objects.filter(order__cash_entry__settlement=receipt)
    return Response(
        {
            "receipt": str(receipt.pk),
            "reference": receipt.reference,
            "completions": [data(row) for row in completions],
            **statement(courier_id),
        }
    )
