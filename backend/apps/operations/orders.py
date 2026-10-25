import hashlib
import json
import uuid
from collections import defaultdict
from decimal import ROUND_CEILING, Decimal

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.identity.permissions import allowed

from .common import audit, business_today, work_calendar
from .inventory import SCALE, allocate, balances, consume, release
from .models import (
    Alert,
    CourierAssignment,
    FreshnessReview,
    IntegrationJob,
    Order,
    OrderLine,
    Postponement,
    ProductionAllocation,
    ProductionBatch,
    ProductionWork,
    Recipe,
    Reservation,
    StockMovement,
    StockReturn,
)


@transaction.atomic
def create_order(data, actor, source=Order.Source.B2B):
    key = data["command_key"]
    fingerprint = hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()
    # Serialize initial creations per warehouse, including first use of an idempotency key.
    balances(data["location"].pk, [])
    previous = Order.objects.filter(command_key=key).first()
    if previous:
        if previous.command_hash != fingerprint:
            raise ValidationError("Idempotency key already belongs to different order data.")
        if actor and previous.created_by_id != actor.pk and not allowed(actor, "Admin"):
            raise ValidationError("Idempotency key is not available.")
        return previous
    customer, address = data["customer"], data["address"]
    if address.customer_id != customer.pk:
        raise ValidationError("The delivery branch does not belong to this customer.")
    prepared = []
    lead = 0
    total = Decimal(0)
    for entry in data["lines"]:
        item = entry["item"]
        price = entry.get("unit_price", item.price)
        if (
            not entry["quantity"].is_finite()
            or entry["quantity"] <= 0
            or not price.is_finite()
            or price < 0
        ):
            raise ValidationError(
                "Order quantities and prices must be valid positive quantities and nonnegative prices."
            )
        if not item.active or item.kind != "FINISHED_GOOD":
            raise ValidationError("Only active finished products can be ordered.")
        recipe = (
            Recipe.objects.filter(product=item, active=True).prefetch_related("components").first()
            if item.manufactured
            else None
        )
        if item.manufactured and (not recipe or not recipe.components.exists()):
            raise ValidationError(f"Product {item.sku} requires a complete active recipe.")
        if item.fresh:
            lead = max(lead, item.lead_workdays)
        prepared.append((entry, recipe))
        total += entry["quantity"] * entry.get("unit_price", item.price)
    if not total.is_finite() or total >= Decimal("1000000000000"):
        raise ValidationError("Order amount is outside the supported range.")
    total = total.quantize(Decimal("0.01"))
    due = work_calendar().on_or_after(data["delivery_date"])
    if any(entry["item"].fresh for entry, _ in prepared):
        due = max(due, work_calendar().add_workdays(business_today(), lead))
    expected = data.get(
        "expected_collection",
        Decimal(0) if data.get("payment_status", "UNPAID") == "PAID" else total,
    )
    if not expected.is_finite() or expected < 0:
        raise ValidationError("Expected collection must be a valid nonnegative amount.")
    expected = expected.quantize(Decimal("0.01"))
    if expected > total:
        raise ValidationError("Expected collection cannot exceed the order total.")
    if data.get("payment_status") == "PAID" and expected != 0:
        raise ValidationError("A fully paid order cannot require another collection.")
    if data.get("payment_status") == "PARTIAL" and "expected_collection" not in data:
        raise ValidationError("Partially paid orders require an explicit remaining collection.")
    order = Order.objects.create(
        number=f"OPS-{uuid.uuid4().hex[:12].upper()}",
        source=source,
        command_key=key,
        command_hash=fingerprint,
        customer=customer,
        address=address,
        location=data["location"],
        created_by=actor,
        customer_name=customer.name,
        customer_phone=address.phone or customer.phone,
        full_address=address.full_address,
        original_area=address.original_area,
        area=address.area,
        notes=data.get("notes", ""),
        delivery_date=due,
        earliest_time=data.get("earliest_time"),
        latest_time=data.get("latest_time"),
        payment_method=data.get("payment_method", "COD"),
        payment_status=data.get("payment_status", "UNPAID"),
        currency=data.get("currency", "EGP"),
        total=total.quantize(Decimal("0.01")),
        expected_collection=expected,
    )
    for entry, recipe in prepared:
        item = entry["item"]
        line = OrderLine.objects.create(
            order=order,
            item=item,
            name=item.name,
            sku=item.sku,
            quantity=entry["quantity"],
            unit_price=entry.get("unit_price", item.price),
            recipe=recipe,
            fresh=item.fresh,
        )
        if recipe:
            batch, _ = ProductionBatch.objects.get_or_create(
                item=item, recipe=recipe, location=order.location, due_date=due
            )
            ProductionAllocation.objects.create(
                batch=batch, order_line=line, quantity=line.quantity
            )
    IntegrationJob.objects.create(order=order)
    audit(
        actor,
        "order.created",
        order,
        after={"source": source, "delivery_date": due, "total": total},
    )
    if due != data["delivery_date"]:
        Postponement.objects.create(
            order=order,
            original_date=data["delivery_date"],
            new_date=due,
            reason="Configured fresh production lead time or work calendar",
            actor=actor,
        )
        audit(
            actor,
            "order.production_rescheduled",
            order,
            {"date": data["delivery_date"]},
            {"date": due},
        )
    allocate(order.pk, actor)
    order.refresh_from_db()
    return order


def locked(order_id, expected_version):
    order = Order.objects.select_for_update().get(pk=order_id)
    if expected_version != order.version:
        raise ValidationError("The order changed. Refresh before retrying this action.")
    if order.state in {Order.State.DELIVERED, Order.State.CANCELLED}:
        raise ValidationError("This order is already closed.")
    return order


def changed(order, actor, action, before, after):
    order.version += 1
    order.save()
    audit(actor, action, order, before, after)
    return order


@transaction.atomic
def market_quantities(order_id, version, actor, entries, reason, received=False):
    order = Order.objects.select_for_update().select_related("customer").get(pk=order_id)
    if order.version != version:
        raise ValidationError("The order changed. Refresh before retrying this action.")
    if order.source != Order.Source.B2B or not order.customer.company:
        raise ValidationError("Item reconciliation is only available for market orders.")
    if order.state == Order.State.CANCELLED or order.review_required:
        raise ValidationError("Resolve order review or cancellation before recording quantities.")
    if received and not order.stock_issued:
        raise ValidationError("Dispatch must be recorded before customer receipt.")
    if not received and (order.stock_issued or order.state == Order.State.DELIVERED):
        raise ValidationError("Sent quantities are locked after dispatch.")
    if hasattr(order, "finance_completion"):
        raise ValidationError(
            "Finance already completed this order. Reconcile changes with Finance before editing quantities."
        )
    lines = list(order.lines.select_for_update().all())
    values = {str(entry["line"]): entry["quantity"] for entry in entries}
    if len(values) != len(entries) or set(values) != {str(line.pk) for line in lines}:
        raise ValidationError("Submit every order line exactly once.")
    field = "received_quantity" if received else "sent_quantity"
    before = {str(line.pk): getattr(line, field) for line in lines}
    for line in lines:
        amount = values[str(line.pk)]
        limit = line.fulfillment_quantity if received else line.quantity
        if not amount.is_finite() or amount < 0 or amount > limit:
            raise ValidationError(
                "Quantities must be nonnegative and cannot exceed the previous stage."
            )
        if not received and line.production_complete and amount != line.fulfillment_quantity:
            raise ValidationError(
                "Production is already complete. Contact operations before changing its quantities."
            )
        if not received and amount < line.produced_quantity:
            raise ValidationError(
                "Sent quantity cannot be lower than the quantity already produced for this line."
            )
    if not received and not any(values.values()):
        raise ValidationError("At least one item must leave the warehouse.")
    for line in lines:
        setattr(line, field, values[str(line.pk)])
        line.save(update_fields=[field])
        if not received:
            ProductionAllocation.objects.filter(order_line=line, completed_at__isnull=True).update(
                quantity=line.fulfillment_quantity
            )
    if not received:
        order.warehouse_ready = False
        order.state = Order.State.SCHEDULED
        order.save(update_fields=["warehouse_ready", "state"])
        allocate(order.pk, actor)
        order.refresh_from_db()
    else:
        discrepancy = any(line.received_quantity < line.fulfillment_quantity for line in lines)
        if discrepancy:
            Alert.objects.update_or_create(
                key=f"market-receipt:{order.pk}",
                defaults={
                    "kind": "MARKET_RECEIPT",
                    "order": order,
                    "target_role": "Finance",
                    "message": "Market received fewer items than dispatched. Reconcile the invoice and physical stock return; no stock or price was changed automatically.",
                    "resolved_at": None,
                },
            )
        else:
            Alert.objects.filter(key=f"market-receipt:{order.pk}").update(
                resolved_at=timezone.now()
            )
    return changed(
        order,
        actor,
        "finance.market_received" if received else "warehouse.market_sent",
        before,
        {"quantities": values, "reason": reason},
    )


@transaction.atomic
def produce(line_id, actor, lot_reference, quantity=None, expires_on=None):
    # Order first, then location and stock, matching allocation lock order.
    initial = OrderLine.objects.get(pk=line_id)
    order = Order.objects.select_for_update().get(pk=initial.order_id)
    # Recipe is nullable: PostgreSQL cannot lock the nullable side of this outer join.
    line = (
        OrderLine.objects.select_for_update(of=("self",))
        .select_related("recipe", "item")
        .get(pk=line_id)
    )
    if line.production_complete:
        return order
    amount = line.fulfillment_quantity - line.produced_quantity if quantity is None else quantity
    if (
        not amount.is_finite()
        or amount <= 0
        or amount > line.fulfillment_quantity - line.produced_quantity
    ):
        raise ValidationError(
            "Production quantity must be positive and cannot exceed the remaining quantity."
        )
    if order.review_required:
        raise ValidationError("Resolve the upstream order review before production.")
    if (
        not lot_reference.strip()
        or line.fulfillment_quantity <= 0
        or not line.recipe_id
        or not order.allocated
        or order.state in {Order.State.CANCELLED, Order.State.DELIVERED}
    ):
        raise ValidationError(
            "An allocated open manufacturing order and production lot are required."
        )
    needed = defaultdict(lambda: Decimal(0))
    for component in line.recipe.components.all():
        after = (
            (line.produced_quantity + amount) * component.quantity / line.recipe.output_yield
        ).quantize(SCALE, rounding=ROUND_CEILING)
        before = (line.produced_quantity * component.quantity / line.recipe.output_yield).quantize(
            SCALE, rounding=ROUND_CEILING
        )
        if after > before:
            needed[component.material_id] += after - before
    stocks = balances(order.location_id, list(needed) + [line.item_id])
    consume(order, needed, actor, f"production:{line.pk}:{line.produced_quantity}")
    stock = stocks[line.item_id]
    stock.refresh_from_db()
    from .inventory import receive

    stock = receive(
        line.item,
        order.location,
        amount,
        f"Production lot {lot_reference}",
        f"output:{line.pk}:{line.produced_quantity}",
        actor,
        lot_reference if expires_on else "",
        expires_on,
    )
    stock.reserved += amount
    stock.save(update_fields=["reserved"])
    allocation = Reservation.objects.filter(order=order, balance=stock, active=True).first()
    if allocation:
        allocation.quantity += amount
        allocation.save(update_fields=["quantity"])
    else:
        Reservation.objects.create(order=order, balance=stock, quantity=amount)
    line.produced_quantity += amount
    line.production_complete = line.produced_quantity == line.fulfillment_quantity
    order.state = Order.State.PREPARING
    line.save(update_fields=["production_complete", "produced_quantity"])
    ProductionAllocation.objects.filter(order_line=line).update(
        completed_at=timezone.now() if line.production_complete else None,
        lot_reference=lot_reference,
    )
    return changed(
        order,
        actor,
        "production.completed",
        {},
        {"line": line.pk, "quantity": amount, "lot": lot_reference},
    )


@transaction.atomic
def ready(order_id, version, actor):
    order = locked(order_id, version)
    if order.review_required:
        raise ValidationError("Resolve the upstream order review before preparation or delivery.")
    if any(
        line.item.produce_on_order
        and not ProductionWork.objects.filter(
            line=line, made__gte=line.fulfillment_quantity
        ).exists()
        for line in order.lines.select_related("item")
    ):
        raise ValidationError("The made-to-order box must be produced before warehouse readiness.")
    if not order.allocated or any(
        line.recipe_id and not line.production_complete and line.fulfillment_quantity > 0
        for line in order.lines.all()
    ):
        raise ValidationError(
            "Stock must be allocated and manufacturing complete before readiness."
        )
    if not order.customer_phone or not order.full_address.strip():
        raise ValidationError("A customer phone and full address are required.")
    order.warehouse_ready = True
    order.state = Order.State.ASSIGNED if order.courier_id else Order.State.READY
    return changed(order, actor, "warehouse.ready", {}, {"ready": True})


@transaction.atomic
def assign(order_id, version, actor, courier, handover_reason=""):
    order = locked(order_id, version)
    if order.review_required:
        raise ValidationError("Resolve the upstream order review before assignment.")
    if not courier.is_active or not courier.groups.filter(name="Courier").exists():
        raise ValidationError("Select an active courier.")
    if not order.warehouse_ready:
        raise ValidationError("Warehouse must confirm readiness before courier assignment.")
    if order.courier_id == courier.pk:
        return order
    if order.stock_issued and not handover_reason.strip():
        raise ValidationError("Issued stock needs a recorded custody handover before reassignment.")
    before = {"courier": order.courier_id}
    CourierAssignment.objects.filter(order=order, ended_at__isnull=True).update(
        ended_at=timezone.now()
    )
    CourierAssignment.objects.create(order=order, courier=courier, assigned_by=actor)
    order.courier = courier
    if order.state != Order.State.DISPATCHED:
        order.state = Order.State.ASSIGNED
    return changed(
        order,
        actor,
        "courier.assigned",
        before,
        {"courier": courier.pk, "handover_reason": handover_reason if order.stock_issued else ""},
    )


@transaction.atomic
def dispatch(order_id, version, actor):
    order = locked(order_id, version)
    if order.review_required:
        raise ValidationError("Resolve the upstream order review before dispatch.")
    if not order.courier_id or not order.warehouse_ready or order.delivery_date != business_today():
        raise ValidationError("Only a ready assigned order due today can be dispatched.")
    if (
        order.no_answer_count
        and order.lines.filter(fresh=True, production_complete=True).exists()
        and not order.freshness_reviews.filter(delivery_date=order.delivery_date).exists()
    ):
        raise ValidationError(
            "Warehouse must review the produced fresh items before retrying this delivery."
        )
    if not order.stock_issued:
        needed = defaultdict(lambda: Decimal(0))
        for line in order.lines.all():
            if line.fulfillment_quantity > 0:
                needed[line.item_id] += line.fulfillment_quantity
            if line.sent_quantity is None:
                line.sent_quantity = line.quantity
                line.save(update_fields=["sent_quantity"])
        balances(order.location_id, needed)
        consume(order, needed, actor, f"dispatch:{order.pk}")
        order.stock_issued = True
    order.state = Order.State.DISPATCHED
    return changed(order, actor, "order.dispatched", {}, {"state": order.state})


@transaction.atomic
def deliver(order_id, version, actor, collected):
    from django.contrib.auth import get_user_model

    from .courier_cash import website_cod
    from .models import CourierCashEntry

    initial = Order.objects.get(pk=order_id)
    if initial.courier_id:
        get_user_model().objects.select_for_update().get(pk=initial.courier_id)
    order = locked(order_id, version)
    if order.courier_id != initial.courier_id:
        raise ValidationError("The courier changed. Refresh before delivery.")
    if order.review_required:
        raise ValidationError(
            "Resolve the upstream order/payment review before confirming delivery."
        )
    if order.state != Order.State.DISPATCHED:
        raise ValidationError("Dispatch must be recorded before delivery.")
    if collected != order.courier_collection:
        raise ValidationError(
            "Collected amount must match the approved amount; report exceptions to Supply Chain."
        )
    order.collected = collected
    if collected > 0:
        order.payment_status = "PAID"
    order.delivered_at = timezone.now()
    order.state = Order.State.DELIVERED
    CourierAssignment.objects.filter(order=order, ended_at__isnull=True).update(
        ended_at=timezone.now()
    )
    result = changed(
        order, actor, "order.delivered", {}, {"state": order.state, "collected": collected}
    )
    from .shopify_fulfillment import queue_fulfillment

    if website_cod(order):
        if not order.courier_id:
            raise ValidationError("A website cash delivery must have an assigned courier.")
        CourierCashEntry.objects.create(
            order=order, courier=order.courier, currency=order.currency, amount=collected
        )
    else:
        queue_fulfillment(order, "ONLINE_PAID")
        if (
            order.source == Order.Source.SHOPIFY
            and order.payment_method == "ONLINE"
            and order.payment_status == "PAID"
            and order.expected_collection == 0
        ):
            from .finance_completion import notify_completion

            notify_completion(order, "WEBSITE")
    return result


@transaction.atomic
def cancel(order_id, version, actor, reason):
    order = locked(order_id, version)
    if hasattr(order, "finance_completion"):
        raise ValidationError(
            "Finance completed this order. Use an approved return or accounting adjustment instead of cancelling it."
        )
    if not reason.strip():
        raise ValidationError("Cancellation requires a reason.")
    job = IntegrationJob.objects.select_for_update().filter(order=order).first()
    balances(
        order.location_id,
        list(order.reservations.filter(active=True).values_list("balance__item_id", flat=True)),
    )
    release(order, actor)
    order.state = Order.State.CANCELLED
    order.warehouse_ready = False
    order.review_required = False
    Alert.objects.filter(order=order, resolved_at__isnull=True).update(resolved_at=timezone.now())
    CourierAssignment.objects.filter(order=order, ended_at__isnull=True).update(
        ended_at=timezone.now()
    )
    if job and (job.external_id or job.status in {"CLAIMED", "CREATING", "UNKNOWN"}):
        Alert.objects.update_or_create(
            key=f"odoo-cancel:{order.pk}",
            defaults={
                "kind": "ODOO_CANCEL_REVIEW",
                "order": order,
                "target_role": "Supply Chain",
                "message": "The internal order was cancelled after an Odoo request started. Review its remote quotation; no remote document was silently deleted.",
                "resolved_at": None,
            },
        )
    elif job:
        job.status, job.error, job.next_attempt_at = "CANCELLED", "", None
        job.save(update_fields=["status", "error", "next_attempt_at"])
    if order.stock_issued:
        Alert.objects.get_or_create(
            key=f"return:{order.pk}",
            defaults={
                "kind": "STOCK_RETURN",
                "order": order,
                "target_role": "Warehouse",
                "message": "Cancelled delivery has issued stock. Confirm a physical return before restocking.",
            },
        )
    return changed(order, actor, "order.cancelled", {}, {"reason": reason})


@transaction.atomic
def no_answer(order_id, version, actor):
    order = locked(order_id, version)
    if order.source != Order.Source.SHOPIFY:
        raise ValidationError("Did not answer is only available for website orders.")
    if order.state != Order.State.DISPATCHED:
        raise ValidationError("Record dispatch before reporting a new unanswered delivery attempt.")
    order.no_answer_count += 1
    if order.no_answer_count >= 2:
        order.save(update_fields=["no_answer_count"])
        order = cancel(
            order.pk,
            order.version,
            actor,
            "Customer did not answer on the second delivery attempt.",
        )
        message = "Customer did not answer twice; cancelled in the system. / العميل لم يرد مرتين؛ تم إلغاء الطلب في النظام."
    else:
        original = order.delivery_date
        due = work_calendar().add_workdays(max(business_today(), original), 1)
        Postponement.objects.create(
            order=order,
            original_date=original,
            new_date=due,
            reason="Customer did not answer — Admin follow-up.",
            actor=actor,
        )
        order.delivery_date = due
        order.earliest_time = order.latest_time = None
        order.state = Order.State.ASSIGNED
        # Issued goods remain in courier custody; never silently restock or consume twice.
        if order.lines.filter(fresh=True, production_complete=True).exists():
            order.warehouse_ready = False
            Alert.objects.update_or_create(
                key=f"no-answer-freshness:{order.pk}",
                defaults={
                    "kind": "FRESHNESS_REVIEW",
                    "order": order,
                    "target_role": "Warehouse",
                    "message": "Unanswered delivery moved to another day. Inspect produced fresh items before confirming readiness and re-dispatch.",
                    "resolved_at": None,
                },
            )
        order = changed(
            order, actor, "order.no_answer", {"date": original}, {"date": due, "attempt": 1}
        )
        message = f"No answer 1/2. Contact customer: {order.customer_phone}. Upcoming: {due}. / لم يرد للمرة الأولى؛ يرجى التواصل مع العميل. الموعد القادم {due}."
    from .courier_labels import label

    Alert.objects.update_or_create(
        key=f"no-answer:{order.pk}:{order.no_answer_count}",
        defaults={
            "kind": "CUSTOMER_NO_ANSWER",
            "target_role": "Admin",
            "order": order,
            "message": f"{label(order)} — {message}"[:500],
            "resolved_at": None,
        },
    )
    return order


@transaction.atomic
def postpone(order_id, version, actor, data):
    order = locked(order_id, version)
    if not data["reason"].strip():
        raise ValidationError("Postponement requires a reason.")
    due = data.get("delivery_date") or order.delivery_date
    if not work_calendar().is_workday(due) or due < business_today():
        raise ValidationError("Choose a valid current or future company workday.")
    if (
        not data.get("delivery_date")
        and not data.get("earliest_time")
        and not data.get("latest_time")
    ):
        raise ValidationError("A new date or time window is required.")
    if (
        order.lines.filter(fresh=True, production_complete=True).exists()
        and due != order.delivery_date
        and not order.freshness_reviews.filter(
            delivery_date=due, order_version=order.version
        ).exists()
    ):
        raise ValidationError(
            "Already-produced fresh items need a warehouse freshness review before changing delivery day."
        )
    before = {
        "date": order.delivery_date,
        "earliest_time": order.earliest_time,
        "latest_time": order.latest_time,
    }
    Postponement.objects.create(
        order=order,
        original_date=order.delivery_date,
        new_date=due,
        earliest_time=data.get("earliest_time"),
        latest_time=data.get("latest_time"),
        reason=data["reason"],
        actor=actor,
    )
    order.delivery_date = due
    order.earliest_time, order.latest_time = data.get("earliest_time"), data.get("latest_time")
    for allocation in ProductionAllocation.objects.filter(
        order_line__order=order, order_line__active=True, completed_at__isnull=True
    ).select_related("batch"):
        batch, _ = ProductionBatch.objects.get_or_create(
            item_id=allocation.batch.item_id,
            recipe_id=allocation.batch.recipe_id,
            location_id=order.location_id,
            due_date=due,
        )
        allocation.batch = batch
        allocation.save(update_fields=["batch"])
    if not order.stock_issued:
        CourierAssignment.objects.filter(order=order, ended_at__isnull=True).update(
            ended_at=timezone.now()
        )
        order.courier = None
        order.state = Order.State.SCHEDULED
    else:
        # Stock stays with its current courier; no second consumption on re-dispatch.
        order.state = Order.State.ASSIGNED
    return changed(order, actor, "order.postponed", before, {"date": due, "reason": data["reason"]})


@transaction.atomic
def review_freshness(order_id, version, actor, due, reason):
    order = locked(order_id, version)
    if not reason.strip() or not work_calendar().is_workday(due) or due < business_today():
        raise ValidationError(
            "A valid future workday and recorded warehouse inspection reason are required."
        )
    if not order.lines.filter(fresh=True, production_complete=True).exists():
        raise ValidationError("This review applies to already-produced fresh items.")
    FreshnessReview.objects.create(
        order=order, delivery_date=due, order_version=version, reviewed_by=actor, reason=reason
    )
    audit(
        actor,
        "warehouse.freshness_approved",
        order,
        after={"date": due, "version": version, "reason": reason},
    )
    return order


@transaction.atomic
def correct_routing(order_id, version, actor, data):
    order = locked(order_id, version)
    before, after = {}, {}
    for field in ["area", "customer_phone", "full_address", "notes"]:
        if field in data:
            before[field] = getattr(order, field + "_id" if field == "area" else field)
            setattr(order, field, data[field])
            after[field] = data[field].pk if field == "area" and data[field] else data[field]
    after["reason"] = data["reason"]
    return changed(order, actor, "order.routing_corrected", before, after)


@transaction.atomic
def amend(order_id, version, actor, data):
    order = locked(order_id, version)
    job = IntegrationJob.objects.select_for_update().get(order=order)
    if (
        order.source != Order.Source.B2B
        or order.stock_issued
        or order.lines.filter(production_complete=True).exists()
    ):
        raise ValidationError(
            "Amendments require an unissued B2B order with no completed production."
        )
    if job.external_id or job.status not in {"PENDING", "ERROR"}:
        raise ValidationError(
            "The Odoo request has started or linked a quotation. Commercial reconciliation is required before amendment."
        )
    previous = {line.item_id: line for line in order.lines.select_related("item", "recipe")}
    prepared, total, lead = [], Decimal(0), 0
    for entry in data["lines"]:
        item = entry["item"]
        if not item.active or item.kind != "FINISHED_GOOD":
            raise ValidationError("Only active finished products can be ordered.")
        old = previous.get(item.pk)
        recipe = (
            old.recipe
            if old
            else Recipe.objects.filter(product=item, active=True).first()
            if item.manufactured
            else None
        )
        if item.manufactured and (recipe is None or not recipe.components.exists()):
            raise ValidationError(f"Product {item.sku} requires a complete active recipe.")
        price = entry.get("unit_price", old.unit_price if old else item.price)
        fresh = old.fresh if old else item.fresh
        if fresh:
            lead = max(lead, item.lead_workdays)
        total += entry["quantity"] * price
        prepared.append((entry, recipe, price, fresh))
    total = total.quantize(Decimal("0.01"))
    if total >= Decimal("1000000000000"):
        raise ValidationError("Order amount is outside the supported range.")
    paid = order.total - order.expected_collection
    if total < paid:
        raise ValidationError(
            "The amendment requires a refund. Finance reconciliation must happen first."
        )
    before = {
        "total": order.total,
        "delivery_date": order.delivery_date,
        "lines": [
            {
                "id": str(line.pk),
                "sku": line.sku,
                "quantity": str(line.quantity),
                "unit_price": str(line.unit_price),
            }
            for line in order.lines.all()
        ],
    }
    balances(
        order.location_id,
        list(order.reservations.filter(active=True).values_list("balance__item_id", flat=True)),
    )
    release(order, actor)
    order.lines.update(active=False)
    order.total, order.expected_collection = total, total - paid
    order.payment_status = (
        "PAID" if order.expected_collection == 0 else "PARTIAL" if paid else "UNPAID"
    )
    order.warehouse_ready, order.courier, order.state = False, None, Order.State.SCHEDULED
    CourierAssignment.objects.filter(order=order, ended_at__isnull=True).update(
        ended_at=timezone.now()
    )
    original_date = order.delivery_date
    if any(fresh for _, _, _, fresh in prepared):
        order.delivery_date = max(
            order.delivery_date, work_calendar().add_workdays(business_today(), lead)
        )
    if "notes" in data:
        order.notes = data["notes"]
    order.save()
    for entry, recipe, price, fresh in prepared:
        item = entry["item"]
        line = OrderLine.objects.create(
            order=order,
            item=item,
            name=item.name,
            sku=item.sku,
            quantity=entry["quantity"],
            unit_price=price,
            recipe=recipe,
            fresh=fresh,
        )
        if recipe:
            batch, _ = ProductionBatch.objects.get_or_create(
                item=item, recipe=recipe, location=order.location, due_date=order.delivery_date
            )
            ProductionAllocation.objects.create(
                batch=batch, order_line=line, quantity=line.quantity
            )
    if order.delivery_date != original_date:
        Postponement.objects.create(
            order=order,
            original_date=original_date,
            new_date=order.delivery_date,
            reason="Fresh lead time after order amendment",
            actor=actor,
        )
    job.status, job.error, job.next_attempt_at = "PENDING", "", None
    job.save(update_fields=["status", "error", "next_attempt_at"])
    changed(
        order,
        actor,
        "order.amended",
        before,
        {
            "total": total,
            "delivery_date": order.delivery_date,
            "reason": data["reason"],
            "lines": [
                {
                    "id": str(line.pk),
                    "sku": line.sku,
                    "quantity": str(line.quantity),
                    "unit_price": str(line.unit_price),
                }
                for line in order.lines.all()
            ],
        },
    )
    allocate(order.pk, actor)
    order.refresh_from_db()
    return order


@transaction.atomic
def review_collection(order_id, version, actor, amount, payment_status, reason):
    order = locked(order_id, version)
    if not amount.is_finite() or amount < 0 or amount > order.total or not reason.strip():
        raise ValidationError("Provide a valid remaining amount and finance review reason.")
    if (
        payment_status == "PAID"
        and amount != 0
        or payment_status == "PARTIAL"
        and not 0 < amount < order.total
        or payment_status == "UNPAID"
        and amount != order.total
    ):
        raise ValidationError("Payment status and remaining collection are inconsistent.")
    before = {
        "expected_collection": order.expected_collection,
        "payment_status": order.payment_status,
    }
    order.expected_collection, order.payment_status = amount, payment_status
    Alert.objects.filter(key=f"payment-change:{order.pk}").update(resolved_at=timezone.now())
    order.review_required = Alert.objects.filter(
        key=f"shopify-change:{order.pk}", resolved_at__isnull=True
    ).exists()
    return changed(
        order,
        actor,
        "finance.collection_approved",
        before,
        {"expected_collection": amount, "payment_status": payment_status, "reason": reason},
    )


@transaction.atomic
def return_stock(order_id, version, actor, disposition, reason):
    order = Order.objects.select_for_update().get(pk=order_id)
    previous = StockReturn.objects.filter(order=order).first()
    if previous:
        if previous.disposition != disposition or previous.reason != reason:
            raise ValidationError("This return has already been recorded with different details.")
        return order
    if (
        order.version != version
        or order.state != Order.State.CANCELLED
        or not order.stock_issued
        or not reason.strip()
    ):
        raise ValidationError(
            "A current cancelled order with issued stock and a physical inspection reason is required."
        )
    if disposition not in {"RESTOCK", "DISCARD"}:
        raise ValidationError("Choose restock or discard after physical receipt and inspection.")
    needed = defaultdict(lambda: Decimal(0))
    for line in order.lines.all():
        needed[line.item_id] += line.fulfillment_quantity
    stocks = balances(order.location_id, needed)
    if disposition == "RESTOCK":
        for item_id, amount in needed.items():
            stock = stocks[item_id]
            stock.on_hand += amount
            stock.save(update_fields=["on_hand"])
            StockMovement.objects.create(
                balance=stock,
                delta=amount,
                reason=f"Physical return: {reason}"[:250],
                command_key=f"return:{order.pk}:{item_id}",
                actor=actor,
            )
    StockReturn.objects.create(
        order=order, received_by=actor, disposition=disposition, reason=reason
    )
    Alert.objects.filter(key=f"return:{order.pk}").update(resolved_at=timezone.now())
    return changed(
        order,
        actor,
        "warehouse.return_received",
        {},
        {"disposition": disposition, "reason": reason},
    )
