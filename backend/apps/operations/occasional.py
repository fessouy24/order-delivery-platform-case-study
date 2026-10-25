"""Occasional phone/partner requests are pending until an explicit reservation."""

import hashlib
import json

from django.contrib.auth import get_user_model
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.decorators import api_view
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from apps.identity.permissions import require_role

from .common import audit, business_today
from .models import Customer, CustomerAddress, Item, OccasionalOrder
from .orders import create_order
from .serializers import NewOrder


class PendingRequest(NewOrder):
    delivery_date = serializers.DateField(required=False, allow_null=True)
    channel = serializers.ChoiceField(choices=["PHONE", "PARTNER", "OTHER"])
    reference = serializers.CharField(max_length=160, required=False, allow_blank=True)

    def validate(self, data):
        # Reuse commercial validation without inventing a stored delivery date.
        data["delivery_date"] = business_today()
        result = super().validate(data)
        result.pop("delivery_date")
        return result


def representation(row):
    return {
        "id": str(row.pk),
        "channel": row.channel,
        "reference": row.reference,
        "customer_name": Customer.objects.get(pk=row.payload["customer"]).name,
        "address": CustomerAddress.objects.get(pk=row.payload["address"]).full_address,
        "notes": row.payload.get("notes", ""),
        "lines": [
            {
                "name": Item.objects.get(pk=line["item"]).name,
                "quantity": line["quantity"],
                "unit_price": line.get("unit_price"),
            }
            for line in row.payload["lines"]
        ],
        "order": str(row.order_id) if row.order_id else None,
        "status": "SCHEDULED" if row.order_id else "PENDING",
    }


@api_view(["GET", "POST"])
@transaction.atomic
def requests(request):
    require_role(request.user, "Management", "Supply Chain")
    if request.method == "POST":
        require_role(request.user, "Supply Chain", "Management")
    if request.method == "GET":
        return Response(
            [
                representation(row)
                for row in OccasionalOrder.objects.filter(order__isnull=True).order_by(
                    "-created_at"
                )[:500]
            ]
        )
    serializer = PendingRequest(data=request.data)
    serializer.is_valid(raise_exception=True)
    # Store only validated primitive values, not caller-supplied extra attributes.
    values = serializer.validated_data
    payload = {}
    for key, value in values.items():
        if key == "lines":
            payload[key] = [
                {
                    name: str(getattr(entry_value, "pk", entry_value))
                    for name, entry_value in line.items()
                }
                for line in value
            ]
        else:
            payload[key] = str(getattr(value, "pk", value)) if value is not None else None
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    row, created = OccasionalOrder.objects.get_or_create(
        command_key=values["command_key"],
        defaults={
            "command_hash": digest,
            "channel": values["channel"],
            "reference": values.get("reference", ""),
            "payload": payload,
            "created_by": request.user,
        },
    )
    if row.command_hash != digest:
        raise ValidationError("This request key already belongs to different order details.")
    if created:
        audit(
            request.user,
            "occasional.pending_created",
            row,
            after={"channel": row.channel, "reference": row.reference},
        )
    return Response(representation(row), status=201 if created else 200)


class Reservation(serializers.Serializer):
    delivery_date = serializers.DateField()
    courier = serializers.PrimaryKeyRelatedField(
        queryset=get_user_model().objects.filter(is_active=True, groups__name="Courier").distinct()
    )


@api_view(["POST"])
@transaction.atomic
def schedule(request, request_id):
    require_role(request.user, "Supply Chain", "Management")
    row = get_object_or_404(OccasionalOrder.objects.select_for_update(), pk=request_id)
    if row.order_id:
        return Response({"order": str(row.order_id)})
    reservation = Reservation(data=request.data)
    reservation.is_valid(raise_exception=True)
    data = {
        **row.payload,
        "command_key": f"occasional:{row.pk}",
        "delivery_date": reservation.validated_data["delivery_date"],
        "notes": f"{row.channel} / {row.reference}\n{row.payload.get('notes', '')}",
    }
    serializer = NewOrder(data=data)
    serializer.is_valid(raise_exception=True)
    order = create_order(serializer.validated_data, request.user)
    order.courier = reservation.validated_data["courier"]
    order.save(update_fields=["courier"])
    row.order = order
    row.save(update_fields=["order"])
    audit(
        request.user,
        "occasional.reservation_scheduled",
        order,
        after={
            "request": str(row.pk),
            "channel": row.channel,
            "courier": str(order.courier_id),
            "delivery_date": order.delivery_date,
        },
    )
    return Response({"order": str(order.pk)}, status=201)
