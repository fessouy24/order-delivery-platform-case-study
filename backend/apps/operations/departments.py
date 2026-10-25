from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db.models import Q
from rest_framework.decorators import api_view
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from apps.identity.permissions import require_role

from . import inventory
from .common import business_today
from .models import Order, StockBalance, StockLot
from .serializers import OrderSerializer


def day_for(request):
    try:
        return date.fromisoformat(request.query_params.get("date", str(business_today())))
    except ValueError:
        raise ValidationError("Use a delivery date in YYYY-MM-DD format.")


def day_orders(day):
    return (
        Order.objects.filter(delivery_date=day)
        .exclude(state=Order.State.CANCELLED)
        .select_related("customer", "address", "area", "courier", "odoo", "shopify")
        .prefetch_related("lines__item")
        .order_by("created_at")
    )


def serialized(rows, request):
    data = OrderSerializer(rows, many=True, context={"request": request}).data
    for row in data:
        for key in [
            "customer_phone",
            "full_address",
            "earliest_time",
            "latest_time",
            "original_area",
            "area_name",
            "sync_error",
        ]:
            row.pop(key, None)
    return data


def wire(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: wire(entry) for key, entry in value.items()}
    if isinstance(value, list):
        return [wire(entry) for entry in value]
    return value


@api_view(["POST"])
def register_batch(request):
    require_role(request.user, "Warehouse")
    from .serializers import StockCommand

    fields = StockCommand(data=request.data)
    fields.is_valid(raise_exception=True)
    data = fields.validated_data
    if not data.get("lot_reference") or not data.get("expires_on"):
        raise ValidationError("Batch reference and expiry date are required.")
    lot = inventory.register_lot(
        data["item"],
        data["location"],
        data["delta"],
        data["lot_reference"],
        data["expires_on"],
        data["command_key"],
        request.user,
    )
    return Response({"id": str(lot.pk), "remaining": str(lot.remaining)})


@api_view(["GET"])
def warehouse_board(request):
    require_role(request.user, "Warehouse", "Supply Chain", "Management")
    day = day_for(request)
    rows = list(day_orders(day))
    loads = {}
    for order in rows:
        key = str(order.courier_id or "unassigned")
        load = loads.setdefault(
            key,
            {
                "id": key,
                "name": order.courier.get_full_name() or order.courier.username
                if order.courier
                else "Unassigned",
                "orders": [],
                "products": {},
            },
        )
        load["orders"].append(order)
        for line in order.lines.all():
            quantity = line.fulfillment_quantity
            if quantity <= 0:
                continue
            product = load["products"].setdefault(
                str(line.item_id),
                {
                    "id": str(line.item_id),
                    "name": line.name,
                    "sku": line.sku,
                    "photo_url": line.item.photo_url,
                    "quantity": Decimal(0),
                },
            )
            product["quantity"] += quantity
    for load in loads.values():
        load["orders"] = serialized(load["orders"], request)
        load["products"] = list(load["products"].values())
    stock = []
    for balance in StockBalance.objects.select_related("item", "location").filter(
        item__active=True
    ):
        lots = list(
            StockLot.objects.filter(
                item=balance.item, location=balance.location, remaining__gt=0
            ).order_by("expires_on", "created_at")
        )
        stock.append(
            {
                "id": str(balance.pk),
                "item": str(balance.item_id),
                "sku": balance.item.sku,
                "name": balance.item.name,
                "photo_url": balance.item.photo_url,
                "specifications": balance.item.specifications,
                "location": balance.location.name,
                "on_hand": balance.on_hand,
                "reserved": balance.reserved,
                "unit": balance.item.unit,
                "untracked_quantity": balance.on_hand
                - sum((lot.remaining for lot in lots), Decimal(0)),
                "lots": [
                    {
                        "reference": lot.reference,
                        "expires_on": lot.expires_on,
                        "remaining": lot.remaining,
                        "expired": lot.expires_on < business_today(),
                    }
                    for lot in lots
                ],
            }
        )
    return Response(wire({"date": day, "loads": list(loads.values()), "stock": stock}))


@api_view(["GET"])
def finance_board(request):
    require_role(request.user, "Finance", "Management")
    day = day_for(request)
    rows = list(day_orders(day))
    couriers = {}
    from .courier_cash import statement, website_cod

    for user in (
        get_user_model()
        .objects.filter(
            Q(is_active=True, groups__name="Courier")
            | Q(assigned_orders__delivery_date=day)
            | Q(cash_entries__settlement__isnull=True, cash_entries__isnull=False)
        )
        .distinct()
    ):
        couriers[str(user.pk)] = {
            "id": str(user.pk),
            "name": user.get_full_name() or user.username,
            "orders": [],
            "expected_cod": {},
            "cash_statement": statement(user.pk),
        }
    for order in rows:
        if not order.courier_id or not order.stock_issued:
            continue
        row = couriers[str(order.courier_id)]
        row["orders"].append(order)
        if website_cod(order):
            row["expected_cod"][order.currency] = (
                row["expected_cod"].get(order.currency, Decimal(0)) + order.expected_collection
            )
    for row in couriers.values():
        row["orders"] = serialized(row["orders"], request)
    return Response(
        wire(
            {
                "date": day,
                "couriers": list(couriers.values()),
                "markets": serialized(
                    [
                        order
                        for order in rows
                        if order.source == Order.Source.B2B and order.customer.company
                    ],
                    request,
                ),
            }
        )
    )
