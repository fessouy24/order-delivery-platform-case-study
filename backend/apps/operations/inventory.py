from collections import defaultdict
from decimal import ROUND_CEILING, Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .common import audit, business_today
from .models import Alert, Location, Order, Reservation, StockBalance, StockLot, StockMovement

SCALE = Decimal("0.000001")


def usable_stock(stock):
    expired = StockLot.objects.filter(
        item_id=stock.item_id, location_id=stock.location_id, expires_on__lt=business_today()
    ).aggregate(total=Sum("remaining"))["total"] or Decimal(0)
    return max(Decimal(0), stock.on_hand - expired)


def consume_lots(stock, amount):
    lots = list(
        StockLot.objects.select_for_update()
        .filter(item_id=stock.item_id, location_id=stock.location_id, remaining__gt=0)
        .order_by("expires_on", "created_at", "pk")
    )
    untracked = stock.on_hand - sum((lot.remaining for lot in lots), Decimal(0))
    valid = [lot for lot in lots if lot.expires_on >= business_today()]
    if untracked < 0 or amount > untracked + sum((lot.remaining for lot in valid), Decimal(0)):
        raise ValidationError(
            "Not enough unexpired stock. Review the dated batches before issuing stock."
        )
    for lot in valid:
        used = min(amount, lot.remaining)
        lot.remaining -= used
        amount -= used
        lot.save(update_fields=["remaining"])
        if amount == 0:
            break


def line_requirements(line):
    demand = defaultdict(lambda: Decimal(0))
    if line.recipe_id and not line.production_complete:
        if line.produced_quantity:
            demand[line.item_id] += line.produced_quantity
        for component in line.recipe.components.all():
            total = (
                line.fulfillment_quantity * component.quantity / line.recipe.output_yield
            ).quantize(SCALE, rounding=ROUND_CEILING)
            made = (
                line.produced_quantity * component.quantity / line.recipe.output_yield
            ).quantize(SCALE, rounding=ROUND_CEILING)
            if total > made:
                demand[component.material_id] += total - made
    elif line.fulfillment_quantity:
        demand[line.item_id] += line.fulfillment_quantity
    return dict(demand)


def requirements(order):
    demand = defaultdict(lambda: Decimal(0))
    for line in order.lines.select_related("item", "recipe").prefetch_related("recipe__components"):
        if line.fulfillment_quantity == 0:
            continue
        for item_id, amount in line_requirements(line).items():
            demand[item_id] += amount
    return dict(demand)


def balances(location_id, item_ids):
    # Lock the location as well as existing rows so absent balances cannot race.
    Location.objects.select_for_update().get(pk=location_id)
    result = {}
    for item_id in sorted(item_ids, key=str):
        balance, _ = StockBalance.objects.get_or_create(item_id=item_id, location_id=location_id)
        result[item_id] = StockBalance.objects.select_for_update().get(pk=balance.pk)
    return result


@transaction.atomic
def receive(item, location, delta, reason, command_key, actor, lot_reference="", expires_on=None):
    if not reason.strip() or not command_key.strip():
        raise ValidationError("Reason and idempotency key are required.")
    if not delta.is_finite() or delta == 0:
        raise ValidationError("Stock change must be finite and nonzero.")
    stock = balances(location.pk, [item.pk])[item.pk]
    previous = StockMovement.objects.filter(command_key=command_key).first()
    if previous:
        if (
            previous.balance_id != stock.pk
            or previous.delta != delta
            or previous.reason != reason
            or (previous.lot.reference if previous.lot_id else "") != lot_reference
            or (previous.lot.expires_on if previous.lot_id else None) != expires_on
        ):
            raise ValidationError("This idempotency key was already used for another stock change.")
        return stock
    if stock.on_hand + delta < stock.reserved:
        raise ValidationError("Adjustment would consume reserved stock or make stock negative.")
    if bool(lot_reference) != bool(expires_on):
        raise ValidationError("Supply both batch reference and expiry date, or leave both unknown.")
    lot = None
    if lot_reference:
        if delta > 0 and expires_on < business_today():
            raise ValidationError("New stock cannot be received into an expired batch.")
        lot, _ = StockLot.objects.get_or_create(
            item=item,
            location=location,
            reference=lot_reference,
            defaults={"expires_on": expires_on, "remaining": 0},
        )
        if lot.expires_on != expires_on or lot.remaining + delta < 0:
            raise ValidationError(
                "Batch expiry must match and batch quantity cannot become negative."
            )
        lot.remaining += delta
        lot.save(update_fields=["remaining"])
    elif delta < 0:
        consume_lots(stock, -delta)
    before = {"on_hand": stock.on_hand}
    stock.on_hand += delta
    stock.save(update_fields=["on_hand"])
    StockMovement.objects.create(
        balance=stock, delta=delta, reason=reason, command_key=command_key, actor=actor, lot=lot
    )
    audit(
        actor,
        "inventory.movement",
        stock,
        before,
        {"on_hand": stock.on_hand, "delta": delta, "reason": reason},
    )
    return stock


@transaction.atomic
def register_lot(item, location, quantity, reference, expires_on, command_key, actor):
    stock = balances(location.pk, [item.pk])[item.pk]
    reason = f"Batch dates registered: {quantity}"
    previous = StockMovement.objects.select_related("lot").filter(command_key=command_key).first()
    if previous:
        if (
            previous.balance_id != stock.pk
            or previous.reason != reason
            or not previous.lot_id
            or previous.lot.reference != reference
            or previous.lot.expires_on != expires_on
        ):
            raise ValidationError(
                "This batch registration key was already used for different data."
            )
        return previous.lot
    tracked = StockLot.objects.filter(item=item, location=location).aggregate(
        total=Sum("remaining")
    )["total"] or Decimal(0)
    if quantity <= 0 or not quantity.is_finite() or quantity > stock.on_hand - tracked:
        raise ValidationError("Batch quantity cannot exceed stock without recorded batch dates.")
    lot, _ = StockLot.objects.get_or_create(
        item=item,
        location=location,
        reference=reference,
        defaults={"expires_on": expires_on, "remaining": 0},
    )
    if lot.expires_on != expires_on:
        raise ValidationError("This batch already has a different expiry date.")
    lot.remaining += quantity
    lot.save(update_fields=["remaining"])
    StockMovement.objects.create(
        balance=stock, lot=lot, delta=0, reason=reason, command_key=command_key, actor=actor
    )
    audit(
        actor,
        "warehouse.batch_dates_registered",
        lot,
        after={"quantity": quantity, "expiry": expires_on},
    )
    return lot


def release(order, actor):
    for allocation in (
        order.reservations.filter(active=True).select_related("balance").order_by("balance_id")
    ):
        balance = StockBalance.objects.select_for_update().get(pk=allocation.balance_id)
        balance.reserved -= allocation.quantity
        balance.save(update_fields=["reserved"])
        allocation.active = False
        allocation.save(update_fields=["active"])
        audit(
            actor,
            "inventory.release",
            order,
            after={"item": balance.item_id, "quantity": allocation.quantity},
        )
    order.allocated = False
    order.save(update_fields=["allocated"])


@transaction.atomic
def allocate(order_id, actor=None):
    order = Order.objects.select_for_update().get(pk=order_id)
    if order.review_required:
        raise ValidationError("Resolve upstream review before stock reallocation.")
    if order.stock_issued or order.state in {Order.State.CANCELLED, Order.State.DELIVERED}:
        return order
    needed = requirements(order)
    stocks = balances(order.location_id, needed)
    release(order, actor)
    # Refresh after releasing existing reservations under the same locks.
    for stock in stocks.values():
        stock.refresh_from_db()
    short = {
        str(item): str(amount - (usable_stock(stocks[item]) - stocks[item].reserved))
        for item, amount in needed.items()
        if usable_stock(stocks[item]) - stocks[item].reserved < amount
    }
    if short:
        Alert.objects.update_or_create(
            key=f"shortage:{order.pk}",
            defaults={
                "kind": "INVENTORY_SHORTAGE",
                "order": order,
                "target_role": "Warehouse",
                "message": "Insufficient material or finished stock. Open requirements for details.",
                "resolved_at": None,
            },
        )
        order.warehouse_ready = False
        order.save(update_fields=["warehouse_ready"])
        return order
    for item_id, amount in needed.items():
        if amount <= 0:
            continue
        stock = stocks[item_id]
        stock.reserved += amount
        stock.save(update_fields=["reserved"])
        Reservation.objects.create(order=order, balance=stock, quantity=amount)
        audit(actor, "inventory.reserve", order, after={"item": item_id, "quantity": amount})
    order.allocated = True
    order.save(update_fields=["allocated"])
    Alert.objects.filter(key=f"shortage:{order.pk}").update(resolved_at=timezone.now())
    return order


def consume(order, needed, actor, command_prefix):
    for item_id in sorted(needed, key=str):
        amount = needed[item_id]
        allocation = (
            Reservation.objects.select_for_update()
            .filter(order=order, active=True, balance__item_id=item_id)
            .select_related("balance")
            .first()
        )
        if allocation is None or allocation.quantity < amount:
            raise ValidationError(
                "Stock allocation is incomplete. Refresh reservations before preparation."
            )
        stock = StockBalance.objects.select_for_update().get(pk=allocation.balance_id)
        consume_lots(stock, amount)
        stock.on_hand -= amount
        stock.reserved -= amount
        stock.save(update_fields=["on_hand", "reserved"])
        if allocation.quantity == amount:
            allocation.active = False
            allocation.save(update_fields=["active"])
        else:
            allocation.quantity -= amount
            allocation.save(update_fields=["quantity"])
        StockMovement.objects.create(
            balance=stock,
            delta=-amount,
            reason=command_prefix,
            command_key=f"{command_prefix}:{item_id}",
            actor=actor,
        )
        audit(actor, "inventory.consume", order, after={"item": item_id, "quantity": amount})


def material_report(orders):
    orders = [order for order in orders if not order.stock_issued and not order.review_required]
    required = defaultdict(lambda: Decimal(0))
    trace = defaultdict(list)
    for order in orders:
        if order.stock_issued:
            continue
        products = defaultdict(list)
        for line in order.lines.select_related("item", "recipe").prefetch_related(
            "recipe__components"
        ):
            line_demand = line_requirements(line)
            for item_id, amount in line_demand.items():
                products[item_id].append(
                    {
                        "line_id": str(line.pk),
                        "sku": line.sku,
                        "name": line.name,
                        "quantity": str(line.fulfillment_quantity),
                        "required": str(amount),
                    }
                )
        for item_id, amount in requirements(order).items():
            key = (item_id, order.location_id)
            required[key] += amount
            trace[key].append(
                {
                    "order_id": str(order.pk),
                    "order": order.number,
                    "customer": order.customer_name,
                    "required": str(amount),
                    "products": products[item_id],
                }
            )
    order_ids = [order.pk for order in orders]
    own = defaultdict(lambda: Decimal(0))
    for allocation in Reservation.objects.filter(
        order_id__in=order_ids, active=True
    ).select_related("balance"):
        own[(allocation.balance.item_id, allocation.balance.location_id)] += allocation.quantity
    result = []
    for (item_id, location_id), amount in required.items():
        stock = StockBalance.objects.select_related("item", "location").get(
            item_id=item_id, location_id=location_id
        )
        usable = usable_stock(stock) - stock.reserved + own[(item_id, location_id)]
        result.append(
            {
                "item_id": str(item_id),
                "photo_url": stock.item.photo_url,
                "sku": stock.item.sku,
                "name": stock.item.name,
                "kind": stock.item.kind,
                "unit": stock.item.unit,
                "location": stock.location.name,
                "required": str(amount),
                "on_hand": str(stock.on_hand),
                "reserved": str(stock.reserved),
                "reserved_for_selection": str(own[(item_id, location_id)]),
                "available": str(max(Decimal(0), usable_stock(stock) - stock.reserved)),
                "remaining": str(max(Decimal(0), usable - amount)),
                "shortage": str(max(Decimal(0), amount - usable)),
                "orders": trace[(item_id, location_id)],
            }
        )
    return result
