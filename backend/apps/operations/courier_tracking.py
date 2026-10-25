"""Latest position only, gated by the courier's currently dispatched orders."""

import hashlib
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers
from rest_framework.decorators import api_view
from rest_framework.response import Response

from apps.identity.permissions import require_role

from .models import CourierPosition, Order


def route(courier):
    rows = list(
        Order.objects.filter(courier=courier, state=Order.State.DISPATCHED)
        .order_by("id")
        .values("id", "number", "customer_name", "version")
    )
    key = hashlib.sha256(
        ",".join(f"{row['id']}:{row['version']}" for row in rows).encode()
    ).hexdigest()
    for row in rows:
        row.pop("version")
    return rows, key


class Sample(serializers.Serializer):
    latitude = serializers.FloatField(min_value=-90, max_value=90)
    longitude = serializers.FloatField(min_value=-180, max_value=180)
    accuracy = serializers.FloatField(min_value=0, max_value=10000)
    sampled_at = serializers.DateTimeField()

    def validate(self, data):
        import math

        if not all(math.isfinite(data[k]) for k in ["latitude", "longitude", "accuracy"]):
            raise serializers.ValidationError("Location must contain finite numbers.")
        age = timezone.now() - data["sampled_at"]
        if age > timedelta(minutes=2) or age < timedelta(seconds=-30):
            raise serializers.ValidationError("Use a fresh device location.")
        return data


@api_view(["GET", "POST", "DELETE"])
def mine(request):
    require_role(request.user, "Courier")
    if request.method == "DELETE":
        CourierPosition.objects.filter(courier=request.user).delete()
        return Response({"active": False})
    # Delivery commands serialize on orders. Lock the active rows while checking a write.
    with transaction.atomic():
        if request.method == "POST":
            list(
                Order.objects.select_for_update()
                .filter(courier=request.user, state=Order.State.DISPATCHED)
                .values_list("pk", flat=True)
            )
        rows, key = route(request.user)
        if not rows:
            CourierPosition.objects.filter(courier=request.user).delete()
            return Response({"active": False, "orders": []})
        if request.method == "POST":
            fields = Sample(data=request.data)
            fields.is_valid(raise_exception=True)
            previous = CourierPosition.objects.filter(courier=request.user).first()
            if not previous or fields.validated_data["sampled_at"] > previous.sampled_at:
                CourierPosition.objects.update_or_create(
                    courier=request.user, defaults={**fields.validated_data, "route_key": key}
                )
    return Response({"active": True, "orders": rows})


@api_view(["GET"])
def board(request):
    require_role(request.user, "Supply Chain", "Management")
    result = []
    users = (
        get_user_model()
        .objects.filter(is_active=True, assigned_orders__state=Order.State.DISPATCHED)
        .distinct()
        .order_by("username")
    )
    positions = {p.courier_id: p for p in CourierPosition.objects.filter(courier__in=users)}
    for user in users:
        rows, key = route(user)
        position = positions.get(user.pk)
        valid = bool(
            position
            and position.route_key == key
            and position.sampled_at >= timezone.now() - timedelta(minutes=5)
        )
        result.append(
            {
                "id": str(user.pk),
                "name": user.get_full_name() or user.username,
                "orders": rows,
                "position": (
                    {
                        "latitude": position.latitude,
                        "longitude": position.longitude,
                        "accuracy": position.accuracy,
                        "sampled_at": position.sampled_at,
                    }
                    if valid
                    else None
                ),
                "status": "RECENT" if valid else "NO_RECENT_LOCATION",
            }
        )
    return Response(result)
