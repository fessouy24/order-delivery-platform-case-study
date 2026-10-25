from datetime import date
from decimal import Decimal
from string import Formatter

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from apps.core.calendar import WorkCalendar
from apps.identity.permissions import ROLES, allowed

from .common import business_today, normalize
from .models import (
    Area,
    AreaAlias,
    Customer,
    CustomerAddress,
    ExternalMapping,
    Item,
    Location,
    Order,
    OrderLine,
    Recipe,
    RecipeLine,
    StockBalance,
    WorkPolicy,
)


class MappingSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExternalMapping
        fields = ["id", "provider", "entity_type", "entity_id", "external_id"]

    def validate(self, data):
        if data.get("provider", self.instance.provider if self.instance else "ODOO") != "ODOO":
            raise serializers.ValidationError(
                "Shopify identity mappings are maintained by the importer."
            )
        kind = data.get("entity_type", self.instance.entity_type if self.instance else None)
        model = {
            "customer": Customer,
            "address": CustomerAddress,
            "item": Item,
            "item_uom": Item,
        }.get(kind)
        entity_id = data.get("entity_id", self.instance.entity_id if self.instance else None)
        external_id = data.get("external_id", self.instance.external_id if self.instance else "")
        if (
            model is None
            or not model.objects.filter(pk=entity_id).exists()
            or not external_id.isascii()
            or not external_id.isdecimal()
            or int(external_id) <= 0
            or int(external_id) > 2147483647
        ):
            raise serializers.ValidationError(
                "Choose an existing entity and a positive Odoo record ID."
            )
        return data


class UserSerializer(serializers.ModelSerializer):
    roles = serializers.ListField(
        child=serializers.ChoiceField(choices=ROLES), write_only=True, required=False
    )
    password = serializers.CharField(write_only=True, required=False, min_length=12)

    class Meta:
        model = get_user_model()
        fields = [
            "id",
            "username",
            "first_name",
            "last_name",
            "email",
            "is_active",
            "roles",
            "password",
        ]
        read_only_fields = ["id"]

    def validate_password(self, value):
        try:
            validate_password(value, self.instance)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages) from exc
        return value

    def create(self, validated_data):
        if "password" not in validated_data:
            raise serializers.ValidationError("A secure initial password is required.")
        role_names = validated_data.pop("roles", [])
        user = get_user_model().objects.create_user(**validated_data)
        user.groups.set([Group.objects.get_or_create(name=name)[0] for name in role_names])
        return user

    def update(self, instance, validated_data):
        role_names = validated_data.pop("roles", None)
        password = validated_data.pop("password", None)
        if instance.pk == self.context["request"].user.pk and (
            validated_data.get("is_active") is False
            or (role_names is not None and "Admin" not in role_names and not instance.is_superuser)
        ):
            raise serializers.ValidationError("You cannot remove your own administrative access.")
        for key, value in validated_data.items():
            setattr(instance, key, value)
        if password:
            instance.set_password(password)
        instance.save()
        if role_names is not None:
            instance.groups.set([Group.objects.get_or_create(name=name)[0] for name in role_names])
        return instance

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["roles"] = list(instance.groups.values_list("name", flat=True))
        return data


class AreaSerializer(serializers.ModelSerializer):
    class Meta:
        model = Area
        fields = ["id", "name", "region"]


class AliasSerializer(serializers.ModelSerializer):
    class Meta:
        model = AreaAlias
        fields = ["id", "area", "original_text", "normalized_text"]
        read_only_fields = ["normalized_text"]

    def validate(self, data):
        text = normalize(
            data.get("original_text", self.instance.original_text if self.instance else "")
        )
        if not text:
            raise serializers.ValidationError("Area alias cannot be empty.")
        duplicates = AreaAlias.objects.filter(normalized_text=text)
        if self.instance:
            duplicates = duplicates.exclude(pk=self.instance.pk)
        if duplicates.exists():
            raise serializers.ValidationError("This normalized alias is already mapped.")
        data["normalized_text"] = text
        return data


class AddressSerializer(serializers.ModelSerializer):
    def validate_maps_url(self, value):
        from urllib.parse import urlsplit

        if not value:
            return value
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower()
        if (
            parsed.scheme != "https"
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 443)
            or not (
                host == "maps.app.goo.gl"
                or (host == "goo.gl" and parsed.path.startswith("/maps"))
                or (
                    host
                    in {
                        "google.com",
                        "www.google.com",
                        "maps.google.com",
                        "google.com.eg",
                        "www.google.com.eg",
                    }
                    and (host == "maps.google.com" or parsed.path.startswith("/maps"))
                )
            )
        ):
            raise serializers.ValidationError("Paste an HTTPS Google Maps location link.")
        return value

    area_name = serializers.CharField(source="area.name", read_only=True, default=None)

    class Meta:
        model = CustomerAddress
        fields = [
            "id",
            "customer",
            "branch_code",
            "full_address",
            "original_area",
            "area",
            "area_name",
            "phone",
            "latitude",
            "longitude",
            "maps_url",
        ]


class CustomerSerializer(serializers.ModelSerializer):
    addresses = AddressSerializer(many=True, read_only=True)

    class Meta:
        model = Customer
        fields = ["id", "name", "phone", "company", "sales_owner", "active", "addresses"]


class OpeningStockSerializer(serializers.Serializer):
    location = serializers.PrimaryKeyRelatedField(queryset=Location.objects.filter(active=True))
    quantity = serializers.DecimalField(
        max_digits=20, decimal_places=6, min_value=Decimal("0.000001")
    )
    reason = serializers.CharField(max_length=250)
    command_key = serializers.CharField(max_length=160)


class ItemSerializer(serializers.ModelSerializer):
    opening_stock = OpeningStockSerializer(write_only=True, required=False)
    photo_url = serializers.SerializerMethodField()

    def get_photo_url(self, obj):
        return obj.photo_url

    class Meta:
        model = Item
        fields = [
            "id",
            "sku",
            "name",
            "product_name",
            "variant_name",
            "photo_url",
            "kind",
            "unit",
            "manufactured",
            "fresh",
            "lead_workdays",
            "specifications",
            "produce_on_order",
            "price",
            "active",
            "opening_stock",
        ]

    def validate(self, data):
        if self.instance and "opening_stock" in data:
            raise serializers.ValidationError(
                "Use a stock movement to change an existing product count."
            )
        fresh = data.get("fresh", self.instance.fresh if self.instance else False)
        manufactured = data.get(
            "manufactured", self.instance.manufactured if self.instance else False
        )
        kind = data.get("kind", self.instance.kind if self.instance else Item.Kind.FINISHED)
        if fresh and not manufactured or manufactured and kind != Item.Kind.FINISHED:
            raise serializers.ValidationError("Fresh products must be manufactured finished goods.")
        if (
            self.instance
            and (
                self.instance.recipes.exists()
                or RecipeLine.objects.filter(material=self.instance).exists()
                or StockBalance.objects.filter(item=self.instance).exists()
            )
            and data.get("unit", self.instance.unit) != self.instance.unit
        ):
            raise serializers.ValidationError(
                "Recipe or stocked item units cannot change without an explicit unit conversion migration."
            )
        return data


class RecipeLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = RecipeLine
        fields = ["material", "quantity"]


class RecipeSerializer(serializers.ModelSerializer):
    components = RecipeLineSerializer(many=True)

    class Meta:
        model = Recipe
        fields = ["id", "product", "version", "output_yield", "active", "components"]
        validators = []  # Version transitions are serialized in the catalog service.

    def validate(self, data):
        if not data["product"].manufactured:
            raise serializers.ValidationError("Recipe output must be a manufactured product.")
        if not data["components"] or len(data["components"]) > 100:
            raise serializers.ValidationError("A recipe needs between 1 and 100 materials.")
        seen = set()
        for entry in data["components"]:
            material = entry["material"]
            if material.kind != Item.Kind.RAW or material.manufactured:
                raise serializers.ValidationError("Initial recipes support raw materials only.")
            if material.pk in seen:
                raise serializers.ValidationError(
                    "Combine repeated recipe materials into one line."
                )
            seen.add(material.pk)
        return data


class LocationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Location
        fields = ["id", "name", "active"]


class BalanceSerializer(serializers.ModelSerializer):
    photo_url = serializers.CharField(source="item.photo_url", read_only=True)
    name = serializers.CharField(source="item.name")
    sku = serializers.CharField(source="item.sku")
    unit = serializers.CharField(source="item.unit")
    available = serializers.SerializerMethodField()

    class Meta:
        model = StockBalance
        fields = [
            "id",
            "item",
            "photo_url",
            "name",
            "sku",
            "unit",
            "location",
            "on_hand",
            "reserved",
            "available",
        ]

    def get_available(self, obj):
        from .inventory import usable_stock

        return str(max(Decimal(0), usable_stock(obj) - obj.reserved))


class StockCommand(serializers.Serializer):
    lot_reference = serializers.CharField(max_length=100, required=False, allow_blank=True)
    expires_on = serializers.DateField(required=False, allow_null=True)
    item = serializers.PrimaryKeyRelatedField(queryset=Item.objects.filter(active=True))
    location = serializers.PrimaryKeyRelatedField(queryset=Location.objects.filter(active=True))
    delta = serializers.DecimalField(max_digits=20, decimal_places=6)
    reason = serializers.CharField(max_length=250)
    command_key = serializers.CharField(max_length=160)


class NewOrderLine(serializers.Serializer):
    item = serializers.PrimaryKeyRelatedField(
        queryset=Item.objects.filter(active=True, kind=Item.Kind.FINISHED)
    )
    quantity = serializers.DecimalField(
        max_digits=14, decimal_places=6, min_value=Decimal("0.000001")
    )
    unit_price = serializers.DecimalField(
        max_digits=14, decimal_places=2, min_value=0, required=False
    )


class NewOrder(serializers.Serializer):
    command_key = serializers.CharField(max_length=160)
    customer = serializers.PrimaryKeyRelatedField(queryset=Customer.objects.filter(active=True))
    address = serializers.PrimaryKeyRelatedField(queryset=CustomerAddress.objects.all())
    location = serializers.PrimaryKeyRelatedField(queryset=Location.objects.filter(active=True))
    delivery_date = serializers.DateField()
    earliest_time = serializers.TimeField(required=False, allow_null=True)
    latest_time = serializers.TimeField(required=False, allow_null=True)
    notes = serializers.CharField(max_length=5000, required=False, allow_blank=True)
    payment_method = serializers.ChoiceField(
        choices=["COD", "ONLINE", "BANK_TRANSFER", "UNCONFIRMED"], default="COD"
    )
    payment_status = serializers.ChoiceField(
        choices=["UNPAID", "PAID", "PARTIAL"], default="UNPAID"
    )
    currency = serializers.CharField(min_length=3, max_length=3, default="EGP")
    expected_collection = serializers.DecimalField(
        max_digits=14, decimal_places=2, min_value=0, required=False
    )
    lines = NewOrderLine(many=True, min_length=1, max_length=100)

    def validate(self, data):
        if data["address"].customer_id != data["customer"].pk:
            raise serializers.ValidationError("Delivery branch must belong to the chosen customer.")
        if (
            data.get("earliest_time")
            and data.get("latest_time")
            and data["earliest_time"] > data["latest_time"]
        ):
            raise serializers.ValidationError("Delivery time window is reversed.")
        if data["delivery_date"] < business_today() and self.context.get("forbid_past", True):
            raise serializers.ValidationError("Delivery date cannot be in the past.")
        data["currency"] = data["currency"].upper()
        total = sum(
            (
                entry["quantity"] * entry.get("unit_price", entry["item"].price)
                for entry in data["lines"]
            ),
            Decimal(0),
        )
        if total >= Decimal("1000000000000"):
            raise serializers.ValidationError("Order amount is outside the supported range.")
        return data


class AmendCommand(serializers.Serializer):
    version = serializers.IntegerField(min_value=1)
    reason = serializers.CharField(max_length=500)
    lines = NewOrderLine(many=True, min_length=1, max_length=100)
    notes = serializers.CharField(max_length=5000, allow_blank=True, required=False)


class OrderLineSerializer(serializers.ModelSerializer):
    photo_url = serializers.SerializerMethodField()

    def get_photo_url(self, obj):
        return obj.item.photo_url

    class Meta:
        model = OrderLine
        fields = [
            "id",
            "item",
            "name",
            "sku",
            "photo_url",
            "quantity",
            "unit_price",
            "recipe",
            "production_complete",
            "fresh",
            "sent_quantity",
            "received_quantity",
        ]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if not allowed(self.context["request"].user, "Sales", "Finance", "Management"):
            data.pop("unit_price")
        return data


class OrderSerializer(serializers.ModelSerializer):
    courier_label = serializers.SerializerMethodField()
    courier_label_ar = serializers.SerializerMethodField()

    def get_courier_label(self, obj):
        from .courier_labels import label

        return label(obj)

    def get_courier_label_ar(self, obj):
        from .courier_labels import label

        return label(obj, arabic=True)

    expected_collection = serializers.DecimalField(
        source="courier_collection", max_digits=14, decimal_places=2, read_only=True
    )
    fulfillment_status = serializers.CharField(
        source="fulfillment_job.status", read_only=True, default=""
    )
    cash_settled = serializers.SerializerMethodField()

    def get_cash_settled(self, obj):
        return bool(hasattr(obj, "cash_entry") and obj.cash_entry.settlement_id)

    website_number = serializers.CharField(
        source="shopify.order_number", read_only=True, default=""
    )
    market_order = serializers.SerializerMethodField()

    def get_market_order(self, obj):
        from .finance_completion import kind

        return kind(obj) == "MARKET"

    odoo_number = serializers.CharField(source="odoo.external_number", read_only=True, default="")
    navigation_url = serializers.CharField(source="address.navigation_url", read_only=True)
    lines = OrderLineSerializer(many=True, read_only=True)
    area_name = serializers.CharField(source="area.name", read_only=True, default=None)
    branch_code = serializers.CharField(source="address.branch_code", read_only=True)
    sync_status = serializers.CharField(source="odoo.status", read_only=True, default="PENDING")
    sync_error = serializers.CharField(source="odoo.error", read_only=True, default="")
    courier_name = serializers.CharField(source="courier.username", read_only=True, default=None)

    class Meta:
        model = Order
        fields = [
            "id",
            "number",
            "courier_label",
            "courier_label_ar",
            "no_answer_count",
            "source",
            "market_order",
            "customer",
            "address",
            "customer_name",
            "customer_phone",
            "full_address",
            "navigation_url",
            "original_area",
            "area_name",
            "branch_code",
            "location",
            "notes",
            "delivery_date",
            "earliest_time",
            "latest_time",
            "state",
            "version",
            "warehouse_ready",
            "allocated",
            "stock_issued",
            "review_required",
            "courier",
            "courier_name",
            "delivered_at",
            "payment_method",
            "payment_status",
            "currency",
            "total",
            "expected_collection",
            "collected",
            "fulfillment_status",
            "cash_settled",
            "lines",
            "sync_status",
            "odoo_number",
            "website_number",
            "sync_error",
            "created_at",
        ]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        user = self.context["request"].user
        if not allowed(user, "Sales", "Finance", "Management"):
            data.pop("total")
        if not allowed(user, "Sales", "Finance", "Management", "Supply Chain", "Courier"):
            for key in ["expected_collection", "collected", "payment_method", "payment_status"]:
                data.pop(key)
        if not allowed(user, "Supply Chain"):
            data.pop("sync_error")
        return data


class OrderCommand(serializers.Serializer):
    version = serializers.IntegerField(min_value=1)


class MarketQuantityLine(serializers.Serializer):
    line = serializers.UUIDField()
    quantity = serializers.DecimalField(max_digits=20, decimal_places=6, min_value=0)


class MarketQuantitiesCommand(OrderCommand):
    lines = MarketQuantityLine(many=True, allow_empty=False)
    reason = serializers.CharField(max_length=500)


class PostponeCommand(OrderCommand):
    delivery_date = serializers.DateField(required=False)
    earliest_time = serializers.TimeField(required=False, allow_null=True)
    latest_time = serializers.TimeField(required=False, allow_null=True)
    reason = serializers.CharField(max_length=500)

    def validate(self, data):
        if (
            data.get("earliest_time")
            and data.get("latest_time")
            and data["earliest_time"] > data["latest_time"]
        ):
            raise serializers.ValidationError("Delivery time window is reversed.")
        return data


class CancelCommand(OrderCommand):
    reason = serializers.CharField(max_length=500)


class DeliveryCommand(OrderCommand):
    collected = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=0)


class CollectionReviewCommand(OrderCommand):
    expected_collection = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=0)
    payment_status = serializers.ChoiceField(choices=["UNPAID", "PAID", "PARTIAL"])
    reason = serializers.CharField(max_length=500)


class AssignCommand(OrderCommand):
    courier = serializers.PrimaryKeyRelatedField(
        queryset=get_user_model().objects.filter(is_active=True, groups__name="Courier")
    )
    handover_reason = serializers.CharField(max_length=500, required=False, allow_blank=True)


class FreshnessCommand(OrderCommand):
    delivery_date = serializers.DateField()
    reason = serializers.CharField(max_length=500)


class ReturnCommand(OrderCommand):
    disposition = serializers.ChoiceField(choices=["RESTOCK", "DISCARD"])
    reason = serializers.CharField(max_length=500)


class RoutingCommand(OrderCommand):
    area = serializers.PrimaryKeyRelatedField(
        queryset=Area.objects.all(), allow_null=True, required=False
    )
    customer_phone = serializers.CharField(max_length=40, required=False)
    full_address = serializers.CharField(max_length=5000, required=False)
    notes = serializers.CharField(max_length=5000, allow_blank=True, required=False)
    reason = serializers.CharField(max_length=500)

    def validate(self, data):
        if not any(field in data for field in ["area", "customer_phone", "full_address", "notes"]):
            raise serializers.ValidationError("Choose a routing or contact field to correct.")
        return data


class BulkAssignment(serializers.Serializer):
    courier = serializers.PrimaryKeyRelatedField(
        queryset=get_user_model().objects.filter(is_active=True, groups__name="Courier")
    )
    orders = serializers.ListField(child=serializers.DictField(), min_length=1, max_length=100)

    def validate_orders(self, value):
        rows = []
        for entry in value:
            rows.append(
                {
                    "id": serializers.UUIDField().run_validation(entry.get("id")),
                    "version": serializers.IntegerField(min_value=1).run_validation(
                        entry.get("version")
                    ),
                }
            )
        if len({entry["id"] for entry in rows}) != len(rows):
            raise serializers.ValidationError("Select each order once.")
        return sorted(rows, key=lambda entry: str(entry["id"]))


class PolicySerializer(serializers.ModelSerializer):
    class Meta:
        model = WorkPolicy
        fields = [
            "id",
            "name",
            "weekdays",
            "holidays",
            "extra_workdays",
            "shopify_due_tag",
            "communication_template",
        ]

    def validate(self, data):
        weekdays = data.get(
            "weekdays", self.instance.weekdays if self.instance else [6, 0, 1, 2, 3]
        )
        holidays = data.get("holidays", self.instance.holidays if self.instance else [])
        overrides = data.get(
            "extra_workdays", self.instance.extra_workdays if self.instance else []
        )
        try:
            if not isinstance(weekdays, list) or any(type(value) is not int for value in weekdays):
                raise ValueError
            WorkCalendar(
                frozenset(weekdays),
                frozenset(date.fromisoformat(v) for v in holidays),
                frozenset(date.fromisoformat(v) for v in overrides),
            )
            template = data.get(
                "communication_template",
                self.instance.communication_template if self.instance else "{customer} {order}",
            )
            for _, field, spec, conversion in Formatter().parse(template):
                if field is not None and (field not in {"customer", "order"} or spec or conversion):
                    raise ValueError
        except (TypeError, ValueError) as exc:
            raise serializers.ValidationError(
                "Invalid work calendar or message placeholders."
            ) from exc
        data["weekdays"] = weekdays
        return data
