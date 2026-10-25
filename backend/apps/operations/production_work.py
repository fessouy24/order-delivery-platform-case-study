from collections import defaultdict
from decimal import Decimal

from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.decorators import api_view
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from apps.identity.permissions import require_role

from . import inventory, orders
from .common import audit
from .models import (
    Item,
    Order,
    OrderLine,
    ProductionDevice,
    ProductionNotice,
    ProductionReceipt,
    ProductionWork,
    StockBalance,
)


@transaction.atomic
def sync_work():
    live = set()
    demand = defaultdict(lambda: Decimal(0))
    locations = {}
    lines = OrderLine.objects.filter(
        order__state__in=[
            value
            for value in Order.State.values
            if value not in {Order.State.CANCELLED, Order.State.DELIVERED}
        ],
        order__stock_issued=False,
        order__review_required=False,
    ).select_related("item", "order", "order__location", "recipe")
    for line in lines:
        if line.fulfillment_quantity <= 0:
            continue
        if line.item.produce_on_order or line.recipe_id:
            key = f"order:{line.pk}"
            live.add(key)
            row, created = ProductionWork.objects.get_or_create(
                key=key,
                defaults={
                    "kind": "LIMITED" if line.item.produce_on_order else "SHORTAGE",
                    "item": line.item,
                    "location": line.order.location,
                    "line": line,
                    "required": line.fulfillment_quantity,
                    "made": line.produced_quantity,
                },
            )
            previous = row.required
            active = line.fulfillment_quantity > row.made
            if (
                row.made != line.produced_quantity
                or row.required != line.fulfillment_quantity
                or row.active != active
            ):
                row.made = line.produced_quantity
                row.required = max(row.made, line.fulfillment_quantity)
                row.active = row.required > row.made
                active = row.active
                row.version += 1
                row.save(update_fields=["made", "required", "active", "version"])
            if active and (created or row.required > previous):
                ProductionNotice.objects.create(
                    work=row,
                    message="New limited-edition box order"
                    if row.kind == "LIMITED"
                    else "Production required for an order",
                )
        else:
            demand[(line.item_id, line.order.location_id)] += line.fulfillment_quantity
            locations[line.order.location_id] = line.order.location
    for (item_id, location_id), needed in demand.items():
        item = Item.objects.get(pk=item_id)
        if item.produce_on_order:
            continue
        stock = StockBalance.objects.filter(item=item, location_id=location_id).first()
        shortage = max(Decimal(0), needed - (inventory.usable_stock(stock) if stock else 0))
        key = f"shortage:{item_id}:{location_id}"
        row = ProductionWork.objects.filter(key=key).first()
        if shortage <= 0:
            if row and row.active:
                row.active = False
                row.version += 1
                row.save(update_fields=["active", "version"])
            continue
        live.add(key)
        row, created = ProductionWork.objects.get_or_create(
            key=key,
            defaults={
                "kind": "SHORTAGE",
                "item": item,
                "location_id": location_id,
                "required": shortage,
            },
        )
        previous = row.required
        was_active = row.active
        target = row.made + shortage
        if target != row.required or not row.active:
            row.required = target
            row.active = True
            row.version += 1
            row.save(update_fields=["required", "active", "version"])
        if created or not was_active or target > previous:
            ProductionNotice.objects.create(
                work=row, message="Product shortage — more stock needed"
            )
    for stale in ProductionWork.objects.filter(active=True).exclude(key__in=live):
        stale.active = False
        stale.version += 1
        stale.save(update_fields=["active", "version"])


def work_data(row):
    return {
        "id": str(row.pk),
        "kind": row.kind,
        "name": row.item.name,
        "sku": row.item.sku,
        "photo_url": row.item.photo_url,
        "location": row.location.name,
        "order": row.line.order.number if row.line_id else "",
        "required": str(row.required),
        "made": str(row.made),
        "remaining": str(max(Decimal(0), row.required - row.made)),
        "version": row.version,
        "active": row.active,
    }


class MadeCommand(serializers.Serializer):
    version = serializers.IntegerField(min_value=1)
    quantity = serializers.DecimalField(
        max_digits=20, decimal_places=6, min_value=Decimal("0.000001")
    )
    command_key = serializers.CharField(max_length=160)
    reference = serializers.CharField(max_length=100)
    expires_on = serializers.DateField(required=False, allow_null=True, default=None)


@transaction.atomic
def record_made(work_id, data, actor):
    # Lock order before work, matching order preparation paths.
    initial = get_object_or_404(ProductionWork, pk=work_id)
    if initial.line_id:
        Order.objects.select_for_update().get(pk=initial.line.order_id)
    row = (
        ProductionWork.objects.select_for_update(of=("self",))
        .select_related("item", "location", "line", "line__order")
        .get(pk=work_id)
    )
    previous = ProductionReceipt.objects.filter(command_key=data["command_key"]).first()
    if previous:
        if (
            previous.work_id != row.pk
            or previous.quantity != data["quantity"]
            or previous.reference != data["reference"]
            or previous.expires_on != data["expires_on"]
        ):
            raise ValidationError(
                "This production confirmation was already used for different data."
            )
        return row
    if data["version"] != row.version or not row.active:
        raise ValidationError("Production work changed. Refresh before retrying.")
    amount = data["quantity"]
    if amount > row.required - row.made:
        raise ValidationError("Quantity cannot exceed the remaining production request.")
    if row.line_id:
        line = row.line
        order = line.order
        if line.produced_quantity != row.made:
            raise ValidationError("Production changed elsewhere. Refresh before recording more.")
        if (
            not line.active
            or order.review_required
            or order.stock_issued
            or order.state in {Order.State.CANCELLED, Order.State.DELIVERED}
        ):
            raise ValidationError("This order no longer permits production. Refresh the list.")
        if row.made + amount > line.fulfillment_quantity:
            raise ValidationError("The order quantity changed. Refresh production work.")
        if line.recipe_id:
            if not order.allocated:
                inventory.allocate(order.pk, actor)
            orders.produce(line.pk, actor, data["reference"], amount, data["expires_on"])
        else:
            inventory.receive(
                row.item,
                row.location,
                amount,
                "Made to order",
                data["command_key"],
                actor,
                data["reference"] if data["expires_on"] else "",
                data["expires_on"],
            )
            line.produced_quantity += amount
            line.production_complete = line.produced_quantity == line.fulfillment_quantity
            line.save(update_fields=["produced_quantity", "production_complete"])
            orders.changed(
                order,
                actor,
                "production.quantity_recorded",
                {},
                {"line": line.pk, "quantity": amount},
            )
    else:
        stock = inventory.balances(row.location_id, [row.item_id])[row.item_id]
        current_lines = OrderLine.objects.filter(
            item=row.item,
            recipe__isnull=True,
            order__location=row.location,
            order__stock_issued=False,
            order__review_required=False,
        ).exclude(order__state__in=[Order.State.CANCELLED, Order.State.DELIVERED])
        demand = sum((line.fulfillment_quantity for line in current_lines), Decimal(0))
        if amount > max(Decimal(0), demand - inventory.usable_stock(stock)):
            raise ValidationError("The shortage changed. Refresh before recording production.")
        inventory.receive(
            row.item,
            row.location,
            amount,
            "Shortage production",
            data["command_key"],
            actor,
            data["reference"] if data["expires_on"] else "",
            data["expires_on"],
        )
    row.made += amount
    row.active = row.made < row.required
    row.version += 1
    row.save(update_fields=["made", "active", "version"])
    ProductionReceipt.objects.create(
        work=row,
        quantity=amount,
        reference=data["reference"],
        expires_on=data["expires_on"],
        actor=actor,
        command_key=data["command_key"],
    )
    audit(
        actor,
        "production.work_recorded",
        row,
        after={"quantity": amount, "batch": data["reference"], "expiry": data["expires_on"]},
    )
    for order_id in (
        Order.objects.filter(location=row.location, lines__item=row.item, stock_issued=False)
        .exclude(state__in=[Order.State.CANCELLED, Order.State.DELIVERED])
        .values_list("pk", flat=True)
        .distinct()
    ):
        inventory.allocate(order_id, actor)
    return row


@api_view(["GET"])
def work_list(request):
    require_role(request.user, "Production", "Management", "Supply Chain")
    sync_work()
    rows = (
        ProductionWork.objects.filter(active=True)
        .select_related("item", "location", "line__order")
        .order_by("created_at")
    )
    notices = ProductionNotice.objects.filter(work__active=True).order_by("-created_at")[:100]
    return Response(
        {
            "work": [work_data(row) for row in rows],
            "notices": [
                {"id": str(note.pk), "kind": note.work.kind, "message": note.message}
                for note in notices
            ],
        }
    )


@api_view(["POST"])
def made(request, work_id):
    require_role(request.user, "Production")
    command = MadeCommand(data=request.data)
    command.is_valid(raise_exception=True)
    row = record_made(work_id, command.validated_data, request.user)
    return Response(work_data(row))


@api_view(["POST", "DELETE"])
def device(request):
    require_role(request.user, "Production")
    token = serializers.CharField(max_length=2048).run_validation(request.data.get("token"))
    if request.method == "DELETE":
        ProductionDevice.objects.filter(user=request.user, token=token).delete()
    else:
        ProductionDevice.objects.update_or_create(token=token, defaults={"user": request.user})
    return Response({"saved": True})
