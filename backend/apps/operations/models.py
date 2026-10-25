import uuid
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import F, Q


class Record(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        abstract = True


class Area(Record):
    name = models.CharField(max_length=160, unique=True)
    region = models.CharField(max_length=160, blank=True)


class AreaAlias(Record):
    area = models.ForeignKey(Area, on_delete=models.PROTECT, related_name="aliases")
    normalized_text = models.CharField(max_length=160, unique=True)
    original_text = models.CharField(max_length=160)


class Customer(Record):
    name = models.CharField(max_length=200)
    phone = models.CharField(max_length=40, blank=True)
    company = models.BooleanField(default=False)
    sales_owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT
    )
    active = models.BooleanField(default=True)


class CustomerAddress(Record):
    maps_url = models.URLField(max_length=2000, blank=True)

    @property
    def navigation_url(self):
        from urllib.parse import quote

        if self.maps_url:
            return self.maps_url
        destination = (
            f"{self.latitude},{self.longitude}"
            if self.latitude is not None and self.longitude is not None
            else self.full_address
        )
        return "https://www.google.com/maps/search/?api=1&query=" + quote(destination)

    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="addresses")
    branch_code = models.CharField(max_length=80)
    full_address = models.TextField()
    original_area = models.CharField(max_length=160, blank=True)
    area = models.ForeignKey(Area, null=True, blank=True, on_delete=models.PROTECT)
    phone = models.CharField(max_length=40, blank=True)
    latitude = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    longitude = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["customer", "branch_code"], name="unique_company_ds")
        ]


class Item(Record):
    class Kind(models.TextChoices):
        RAW = "RAW_MATERIAL", "Raw material"
        FINISHED = "FINISHED_GOOD", "Finished good"

    sku = models.CharField(max_length=100, unique=True)
    name = models.CharField(max_length=200)
    product_name = models.CharField(max_length=200, blank=True)
    variant_name = models.CharField(max_length=160, blank=True)
    photo = models.ImageField(upload_to="catalog", blank=True)
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.FINISHED)
    unit = models.CharField(max_length=30, default="unit")
    manufactured = models.BooleanField(default=False)
    fresh = models.BooleanField(default=False)
    lead_workdays = models.PositiveSmallIntegerField(default=1)
    specifications = models.JSONField(default=dict, blank=True)
    produce_on_order = models.BooleanField(default=False)
    price = models.DecimalField(
        max_digits=14, decimal_places=2, default=0, validators=[MinValueValidator(0)]
    )
    active = models.BooleanField(default=True)

    @property
    def photo_url(self):
        if not self.photo:
            return ""
        return f"/api/v1/items/{self.pk}/photo/?v={self.photo.name.rsplit('/', 1)[-1]}"

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(price__gte=0), name="nonnegative_item_price"),
            models.CheckConstraint(
                condition=Q(fresh=False) | Q(manufactured=True), name="fresh_requires_manufacturing"
            ),
            models.CheckConstraint(
                condition=Q(manufactured=False) | Q(kind="FINISHED_GOOD"),
                name="manufactured_is_finished_good",
            ),
        ]


class Recipe(Record):
    product = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="recipes")
    version = models.PositiveIntegerField(default=1)
    output_yield = models.DecimalField(
        max_digits=20,
        decimal_places=6,
        default=1,
        validators=[MinValueValidator(Decimal("0.000001"))],
    )
    active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["product", "version"], name="unique_recipe_version"),
            models.UniqueConstraint(
                fields=["product"], condition=Q(active=True), name="one_active_recipe"
            ),
            models.CheckConstraint(condition=Q(output_yield__gt=0), name="positive_recipe_yield"),
        ]


class RecipeLine(Record):
    recipe = models.ForeignKey(Recipe, on_delete=models.PROTECT, related_name="components")
    material = models.ForeignKey(Item, on_delete=models.PROTECT)
    quantity = models.DecimalField(
        max_digits=20, decimal_places=6, validators=[MinValueValidator(Decimal("0.000001"))]
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["recipe", "material"], name="one_component_per_recipe"),
            models.CheckConstraint(condition=Q(quantity__gt=0), name="positive_component_quantity"),
        ]


class Location(Record):
    name = models.CharField(max_length=160, unique=True)
    active = models.BooleanField(default=True)


class StockBalance(Record):
    item = models.ForeignKey(Item, on_delete=models.PROTECT)
    location = models.ForeignKey(Location, on_delete=models.PROTECT)
    on_hand = models.DecimalField(max_digits=20, decimal_places=6, default=0)
    reserved = models.DecimalField(max_digits=20, decimal_places=6, default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["item", "location"], name="unique_stock_location"),
            models.CheckConstraint(condition=Q(on_hand__gte=0), name="nonnegative_stock"),
            models.CheckConstraint(
                condition=Q(reserved__gte=0) & Q(reserved__lte=F("on_hand")),
                name="valid_stock_reservation",
            ),
        ]


class StockLot(Record):
    item = models.ForeignKey(Item, on_delete=models.PROTECT)
    location = models.ForeignKey(Location, on_delete=models.PROTECT)
    reference = models.CharField(max_length=100)
    expires_on = models.DateField()
    remaining = models.DecimalField(max_digits=20, decimal_places=6)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["item", "location", "reference"], name="unique_stock_lot"
            ),
            models.CheckConstraint(condition=Q(remaining__gte=0), name="nonnegative_lot"),
        ]


class StockMovement(Record):
    lot = models.ForeignKey(StockLot, null=True, blank=True, on_delete=models.PROTECT)
    balance = models.ForeignKey(StockBalance, on_delete=models.PROTECT, related_name="movements")
    delta = models.DecimalField(max_digits=20, decimal_places=6)
    reason = models.CharField(max_length=250)
    command_key = models.CharField(max_length=160, unique=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT)


class Order(Record):
    class Source(models.TextChoices):
        B2B = "B2B", "B2B"
        SHOPIFY = "SHOPIFY", "Shopify"

    class State(models.TextChoices):
        SCHEDULED = "SCHEDULED", "Scheduled"
        PREPARING = "PREPARING", "Preparing"
        READY = "READY", "Ready"
        ASSIGNED = "ASSIGNED", "Assigned"
        DISPATCHED = "OUT_FOR_DELIVERY", "Out for delivery"
        DELIVERED = "DELIVERED", "Delivered"
        CANCELLED = "CANCELLED", "Cancelled"

    number = models.CharField(max_length=50, unique=True)
    source = models.CharField(max_length=20, choices=Source.choices)
    no_answer_count = models.PositiveSmallIntegerField(default=0)
    command_key = models.CharField(max_length=160, unique=True)
    command_hash = models.CharField(max_length=64)
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT)
    address = models.ForeignKey(CustomerAddress, on_delete=models.PROTECT)
    location = models.ForeignKey(Location, on_delete=models.PROTECT)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT, related_name="created_orders"
    )
    customer_name = models.CharField(max_length=200)
    customer_phone = models.CharField(max_length=40, blank=True)
    full_address = models.TextField()
    original_area = models.CharField(max_length=160, blank=True)
    area = models.ForeignKey(Area, null=True, on_delete=models.PROTECT)
    notes = models.TextField(blank=True)
    delivery_date = models.DateField(db_index=True)
    earliest_time = models.TimeField(null=True, blank=True)
    latest_time = models.TimeField(null=True, blank=True)
    state = models.CharField(
        max_length=30, choices=State.choices, default=State.SCHEDULED, db_index=True
    )
    version = models.PositiveIntegerField(default=1)
    warehouse_ready = models.BooleanField(default=False)
    allocated = models.BooleanField(default=False)
    stock_issued = models.BooleanField(default=False)
    review_required = models.BooleanField(default=False)
    courier = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="assigned_orders",
    )
    delivered_at = models.DateTimeField(null=True, blank=True)
    payment_method = models.CharField(max_length=60, default="COD")
    payment_status = models.CharField(max_length=60, default="UNPAID")
    currency = models.CharField(max_length=3, default="EGP")
    total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    expected_collection = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    collected = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)

    @property
    def courier_collection(self):
        return (
            0
            if self.source == self.Source.B2B and self.customer.company
            else self.expected_collection
        )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(total__gte=0) & Q(expected_collection__gte=0),
                name="nonnegative_order_finance",
            ),
            models.CheckConstraint(
                condition=Q(earliest_time__isnull=True)
                | Q(latest_time__isnull=True)
                | Q(latest_time__gte=F("earliest_time")),
                name="valid_delivery_window",
            ),
        ]


class CourierPosition(Record):
    courier = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="delivery_position"
    )
    latitude = models.FloatField()
    longitude = models.FloatField()
    accuracy = models.FloatField()
    sampled_at = models.DateTimeField()
    route_key = models.CharField(max_length=64)


class CourierSettlement(Record):
    courier = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="cash_settlements"
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="recorded_settlements"
    )
    command_key = models.CharField(max_length=160, unique=True)
    fingerprint = models.CharField(max_length=64)
    currency = models.CharField(max_length=3)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    reference = models.CharField(max_length=250)


class CourierCashEntry(Record):
    order = models.OneToOneField(Order, on_delete=models.PROTECT, related_name="cash_entry")
    courier = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="cash_entries"
    )
    currency = models.CharField(max_length=3)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    settlement = models.ForeignKey(
        CourierSettlement, null=True, on_delete=models.PROTECT, related_name="entries"
    )

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(amount__gte=0), name="nonnegative_courier_cash")
        ]


class ShopifyFulfillmentJob(Record):
    order = models.OneToOneField(Order, on_delete=models.PROTECT, related_name="fulfillment_job")
    status = models.CharField(max_length=20, default="PENDING", db_index=True)
    gate = models.CharField(max_length=20)
    plan = models.JSONField(default=dict)
    pending_group = models.JSONField(default=list)
    remote_ids = models.JSONField(default=list)
    attempts = models.PositiveIntegerField(default=0)
    claimed_at = models.DateTimeField(null=True)
    claim_token = models.UUIDField(null=True)
    next_attempt_at = models.DateTimeField(null=True)
    completed_at = models.DateTimeField(null=True)
    error = models.CharField(max_length=500, blank=True)


class ActiveOrderLines(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(active=True)


class OrderLine(Record):
    objects = ActiveOrderLines()
    all_objects = models.Manager()
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="lines")
    item = models.ForeignKey(Item, on_delete=models.PROTECT)
    name = models.CharField(max_length=200)
    sku = models.CharField(max_length=100)
    quantity = models.DecimalField(max_digits=20, decimal_places=6)
    unit_price = models.DecimalField(max_digits=14, decimal_places=2)
    recipe = models.ForeignKey(Recipe, null=True, on_delete=models.PROTECT)
    production_complete = models.BooleanField(default=False)
    fresh = models.BooleanField(default=False)
    active = models.BooleanField(default=True)
    sent_quantity = models.DecimalField(max_digits=20, decimal_places=6, null=True, blank=True)
    received_quantity = models.DecimalField(max_digits=20, decimal_places=6, null=True, blank=True)
    produced_quantity = models.DecimalField(max_digits=20, decimal_places=6, default=0)

    @property
    def fulfillment_quantity(self):
        return self.quantity if self.sent_quantity is None else self.sent_quantity

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(quantity__gt=0) & Q(unit_price__gte=0), name="valid_order_line"
            ),
            models.CheckConstraint(
                condition=Q(sent_quantity__isnull=True)
                | (Q(sent_quantity__gte=0) & Q(sent_quantity__lte=F("quantity"))),
                name="valid_sent_quantity",
            ),
            models.CheckConstraint(
                condition=Q(received_quantity__isnull=True)
                | (
                    Q(sent_quantity__isnull=False)
                    & Q(received_quantity__gte=0)
                    & Q(received_quantity__lte=F("sent_quantity"))
                ),
                name="valid_received_quantity",
            ),
        ]


class ProductionWork(Record):
    key = models.CharField(max_length=200, unique=True)
    kind = models.CharField(max_length=20)
    item = models.ForeignKey(Item, on_delete=models.PROTECT)
    location = models.ForeignKey(Location, on_delete=models.PROTECT)
    line = models.ForeignKey(OrderLine, null=True, blank=True, on_delete=models.PROTECT)
    required = models.DecimalField(max_digits=20, decimal_places=6)
    made = models.DecimalField(max_digits=20, decimal_places=6, default=0)
    version = models.PositiveIntegerField(default=1)
    active = models.BooleanField(default=True)


class ProductionReceipt(Record):
    command_key = models.CharField(max_length=160, unique=True)
    work = models.ForeignKey(ProductionWork, on_delete=models.PROTECT)
    quantity = models.DecimalField(max_digits=20, decimal_places=6)
    reference = models.CharField(max_length=100)
    expires_on = models.DateField(null=True, blank=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)


class ProductionNotice(Record):
    work = models.ForeignKey(ProductionWork, on_delete=models.PROTECT)
    message = models.CharField(max_length=250)


class ProductionDevice(Record):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    token = models.CharField(max_length=2048, unique=True)


class PushDelivery(Record):
    notice = models.ForeignKey(ProductionNotice, on_delete=models.CASCADE)
    device = models.ForeignKey(ProductionDevice, on_delete=models.CASCADE)
    sent_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["notice", "device"], name="unique_production_push")
        ]


class Reservation(Record):
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="reservations")
    balance = models.ForeignKey(StockBalance, on_delete=models.PROTECT)
    quantity = models.DecimalField(max_digits=20, decimal_places=6)
    active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["order", "balance"],
                condition=Q(active=True),
                name="unique_active_allocation",
            ),
            models.CheckConstraint(condition=Q(quantity__gt=0), name="positive_reservation"),
        ]


class ProductionBatch(Record):
    item = models.ForeignKey(Item, on_delete=models.PROTECT)
    recipe = models.ForeignKey(Recipe, on_delete=models.PROTECT)
    location = models.ForeignKey(Location, on_delete=models.PROTECT)
    due_date = models.DateField(db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["item", "recipe", "location", "due_date"], name="unique_production_batch"
            )
        ]


class ProductionAllocation(Record):
    batch = models.ForeignKey(ProductionBatch, on_delete=models.PROTECT, related_name="allocations")
    order_line = models.OneToOneField(
        OrderLine, on_delete=models.PROTECT, related_name="production"
    )
    quantity = models.DecimalField(max_digits=20, decimal_places=6)
    completed_at = models.DateTimeField(null=True, blank=True)
    lot_reference = models.CharField(max_length=100, blank=True)


class OrderEvent(Record):
    sequence = models.PositiveBigIntegerField(default=0)
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="events")
    action = models.CharField(max_length=80)
    before = models.JSONField(default=dict)
    after = models.JSONField(default=dict)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["order", "sequence"], name="unique_order_event_sequence"
            )
        ]


class Postponement(Record):
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="postponements")
    original_date = models.DateField()
    new_date = models.DateField()
    earliest_time = models.TimeField(null=True)
    latest_time = models.TimeField(null=True)
    reason = models.CharField(max_length=500)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT)


class CourierAssignment(Record):
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="assignments")
    courier = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.PROTECT,
        related_name="dispatch_actions",
    )
    ended_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["order"], condition=Q(ended_at__isnull=True), name="one_active_assignment"
            )
        ]


class FreshnessReview(Record):
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="freshness_reviews")
    delivery_date = models.DateField()
    order_version = models.PositiveIntegerField()
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    reason = models.CharField(max_length=500)


class StockReturn(Record):
    order = models.OneToOneField(Order, on_delete=models.PROTECT, related_name="stock_return")
    received_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    disposition = models.CharField(
        max_length=20,
        choices=[("RESTOCK", "Inspected and restocked"), ("DISCARD", "Returned and discarded")],
    )
    reason = models.CharField(max_length=500)


class AuditEvent(Record):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT)
    action = models.CharField(max_length=80)
    entity_type = models.CharField(max_length=80)
    entity_id = models.UUIDField()
    before = models.JSONField(default=dict)
    after = models.JSONField(default=dict)


class Alert(Record):
    key = models.CharField(max_length=200, unique=True)
    kind = models.CharField(max_length=60)
    severity = models.CharField(max_length=20, default="HIGH")
    target_role = models.CharField(max_length=40, default="Supply Chain")
    order = models.ForeignKey(Order, null=True, on_delete=models.PROTECT)
    message = models.CharField(max_length=500)
    resolved_at = models.DateTimeField(null=True)


class AlertRead(Record):
    alert = models.ForeignKey(Alert, on_delete=models.PROTECT, related_name="reads")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["alert", "user"], name="one_alert_read")]


class WorkPolicy(Record):
    name = models.CharField(max_length=60, unique=True, default="default")
    weekdays = models.JSONField(default=list)
    holidays = models.JSONField(default=list)
    extra_workdays = models.JSONField(default=list)
    shopify_due_tag = models.CharField(max_length=80, default="delivery-today")
    communication_template = models.TextField(
        default="Hello {customer}, OPSDEMO will deliver order {order} today. Please confirm your location."
    )


class ExternalMapping(Record):
    provider = models.CharField(max_length=20, default="ODOO")
    entity_type = models.CharField(max_length=40)
    entity_id = models.UUIDField()
    external_id = models.CharField(max_length=160)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "entity_type", "entity_id"], name="unique_provider_mapping"
            )
        ]


class ShopifyReference(Record):
    commercial_snapshot = models.JSONField(default=dict)
    order = models.OneToOneField(Order, null=True, on_delete=models.PROTECT, related_name="shopify")
    store = models.CharField(max_length=200)
    external_id = models.CharField(max_length=160)
    order_number = models.CharField(max_length=100)
    order_created_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(null=True)
    tags = models.JSONField(default=list)
    fulfillment_status = models.CharField(max_length=60, blank=True)
    payment_gateways = models.JSONField(default=list)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["store", "external_id"], name="unique_shopify_order")
        ]


class IntegrationJob(Record):
    order = models.OneToOneField(Order, on_delete=models.PROTECT, related_name="odoo")
    status = models.CharField(max_length=20, default="PENDING", db_index=True)
    external_id = models.CharField(max_length=100, blank=True)
    external_number = models.CharField(max_length=100, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, db_index=True)
    last_success_at = models.DateTimeField(null=True)
    error = models.CharField(max_length=500, blank=True)
    claimed_at = models.DateTimeField(null=True)
    website_batch_date = models.DateField(null=True, blank=True, db_index=True)


class DeliveryQuotationBatch(Record):
    day = models.DateField(unique=True)
    claimed_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    snapshot = models.JSONField(null=True, blank=True)
    next_event = models.PositiveIntegerField(default=0)


class WebhookInbox(Record):
    store = models.CharField(max_length=200)
    event_id = models.CharField(max_length=160)
    topic = models.CharField(max_length=100)
    payload = models.JSONField(default=dict)
    processed_at = models.DateTimeField(null=True)
    error = models.CharField(max_length=500, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True)
    quotation_batch_date = models.DateField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["store", "event_id"], name="unique_shopify_webhook")
        ]


class IntegrationCursor(Record):
    name = models.CharField(max_length=100, unique=True)
    synced_through = models.DateTimeField(null=True)


class MarketQuotation(Record):
    database = models.CharField(max_length=200)
    company_id = models.PositiveIntegerField()
    quotation_id = models.PositiveIntegerField()
    number = models.CharField(max_length=100)
    snapshot = models.JSONField(default=dict)
    status = models.CharField(max_length=20, default="PENDING")
    order = models.OneToOneField(Order, null=True, blank=True, on_delete=models.PROTECT)
    changed_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["database", "company_id", "quotation_id"], name="unique_market_quotation"
            )
        ]


class OccasionalOrder(Record):
    command_key = models.CharField(max_length=160, unique=True)
    command_hash = models.CharField(max_length=64)
    channel = models.CharField(max_length=20)
    reference = models.CharField(max_length=160, blank=True)
    payload = models.JSONField(default=dict)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    order = models.OneToOneField(Order, null=True, blank=True, on_delete=models.PROTECT)


class OrderWorkbookSnapshot(Record):
    """Read-only source history, deliberately separate from actionable orders."""

    source_name = models.CharField(max_length=255)
    source_sha256 = models.CharField(max_length=64, unique=True)
    data = models.JSONField(default=dict)


class FinanceCompletion(Record):
    order = models.OneToOneField(Order, on_delete=models.PROTECT, related_name="finance_completion")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    kind = models.CharField(max_length=20)
    message = models.CharField(max_length=250)
    warning = models.CharField(max_length=250, blank=True)
    snapshot = models.JSONField(default=list)
    odoo_status = models.CharField(max_length=20, default="PENDING", db_index=True)
    odoo_stage = models.CharField(max_length=30, blank=True)
    database = models.CharField(max_length=200, blank=True)
    sales_order_id = models.PositiveIntegerField(null=True)
    invoice_id = models.PositiveIntegerField(null=True)
    invoice_name = models.CharField(max_length=100, blank=True)
    error = models.CharField(max_length=500, blank=True)
    claim_token = models.UUIDField(null=True)
    claimed_at = models.DateTimeField(null=True)
