import re
from datetime import date
from decimal import Decimal
from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action, api_view
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from apps.identity.permissions import allowed, require_role, roles

from . import inventory, orders
from .common import area_for, audit, business_today, normalize
from .filters import filter_orders
from .models import (
    Alert,
    AlertRead,
    Area,
    AreaAlias,
    AuditEvent,
    Customer,
    CustomerAddress,
    ExternalMapping,
    Item,
    Location,
    Order,
    ProductionBatch,
    Recipe,
    RecipeLine,
    StockBalance,
    WorkPolicy,
)
from .serializers import (
    AddressSerializer,
    AliasSerializer,
    AmendCommand,
    AreaSerializer,
    AssignCommand,
    BalanceSerializer,
    BulkAssignment,
    CancelCommand,
    CollectionReviewCommand,
    CustomerSerializer,
    DeliveryCommand,
    FreshnessCommand,
    ItemSerializer,
    LocationSerializer,
    MappingSerializer,
    MarketQuantitiesCommand,
    NewOrder,
    OrderCommand,
    OrderSerializer,
    PolicySerializer,
    PostponeCommand,
    RecipeSerializer,
    ReturnCommand,
    RoutingCommand,
    StockCommand,
    UserSerializer,
)


class ManagedViewSet(viewsets.ModelViewSet):
    http_method_names = ["get", "post", "patch", "head", "options"]
    lookup_value_converter = "uuid"
    read_roles = ("Management", "Sales", "Supply Chain", "Warehouse")
    write_roles = ()

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        require_role(
            request.user,
            *(
                self.read_roles
                if request.method in {"GET", "HEAD", "OPTIONS"}
                else self.write_roles
            ),
        )

    @transaction.atomic
    def perform_create(self, serializer):
        obj = serializer.save()
        audit(self.request.user, "record.created", obj, after=serializer.data)

    @transaction.atomic
    def perform_update(self, serializer):
        before = self.get_serializer(serializer.instance).data
        obj = serializer.save()
        audit(self.request.user, "record.updated", obj, before=before, after=serializer.data)


class Mappings(ManagedViewSet):
    queryset = ExternalMapping.objects.filter(provider="ODOO").order_by("entity_type")
    serializer_class = MappingSerializer
    read_roles = ("Supply Chain",)


class Users(ManagedViewSet):
    queryset = get_user_model().objects.all().order_by("username")
    serializer_class = UserSerializer
    read_roles = ()


class Areas(ManagedViewSet):
    queryset = Area.objects.all().order_by("name")
    serializer_class = AreaSerializer
    write_roles = ("Supply Chain",)

    @transaction.atomic
    def perform_create(self, serializer):
        obj = serializer.save()
        AreaAlias.objects.get_or_create(
            normalized_text=normalize(obj.name), defaults={"area": obj, "original_text": obj.name}
        )
        audit(self.request.user, "area.created", obj, after=serializer.data)


class Aliases(ManagedViewSet):
    queryset = AreaAlias.objects.all().order_by("original_text")
    serializer_class = AliasSerializer
    write_roles = ("Supply Chain",)


class Customers(ManagedViewSet):
    serializer_class = CustomerSerializer
    write_roles = ("Sales", "Supply Chain", "Management")

    def get_queryset(self):
        qs = Customer.objects.prefetch_related("addresses").order_by("name")
        if allowed(self.request.user, "Management", "Supply Chain", "Warehouse", "Finance"):
            return qs
        return qs.filter(sales_owner=self.request.user)

    @transaction.atomic
    def perform_create(self, serializer):
        if not allowed(self.request.user, "Admin"):
            serializer.validated_data["sales_owner"] = self.request.user
        super().perform_create(serializer)

    def perform_update(self, serializer):
        if not allowed(self.request.user, "Admin") and "sales_owner" in serializer.validated_data:
            raise PermissionDenied("Only Admin may transfer customer ownership.")
        super().perform_update(serializer)


class Addresses(ManagedViewSet):
    serializer_class = AddressSerializer
    write_roles = ("Sales", "Supply Chain", "Management")

    def get_queryset(self):
        qs = CustomerAddress.objects.select_related("area").order_by("branch_code")
        if not allowed(self.request.user, "Management", "Supply Chain", "Warehouse", "Finance"):
            qs = qs.filter(customer__sales_owner=self.request.user)
        customer = self.request.query_params.get("customer")
        if customer:
            try:
                qs = qs.filter(customer_id=serializers.UUIDField().run_validation(customer))
            except serializers.ValidationError as exc:
                raise ValidationError("Invalid customer ID.") from exc
        return qs

    def _ownership(self, serializer):
        customer = serializer.validated_data.get(
            "customer", serializer.instance.customer if serializer.instance else None
        )
        if (
            not allowed(self.request.user, "Admin", "Management", "Supply Chain")
            and customer.sales_owner_id != self.request.user.pk
        ):
            raise PermissionDenied("This customer belongs to another Sales user.")
        if "original_area" in serializer.validated_data and "area" not in serializer.validated_data:
            serializer.validated_data["area"] = area_for(serializer.validated_data["original_area"])

    def perform_create(self, serializer):
        self._ownership(serializer)
        super().perform_create(serializer)

    def perform_update(self, serializer):
        self._ownership(serializer)
        super().perform_update(serializer)


class Items(ManagedViewSet):
    serializer_class = ItemSerializer
    write_roles = ("Warehouse",)

    @transaction.atomic
    def perform_create(self, serializer):
        opening = serializer.validated_data.pop("opening_stock", None)
        obj = serializer.save()
        if opening:
            inventory.receive(
                obj,
                opening["location"],
                opening["quantity"],
                opening["reason"],
                opening["command_key"],
                self.request.user,
            )
        audit(self.request.user, "record.created", obj, after=serializer.data)

    def get_queryset(self):
        qs = Item.objects.all().order_by("sku")
        if not allowed(self.request.user, "Warehouse"):
            qs = qs.filter(kind=Item.Kind.FINISHED)
        return qs


class Recipes(ManagedViewSet):
    queryset = Recipe.objects.prefetch_related("components").order_by("product_id", "-version")
    serializer_class = RecipeSerializer
    write_roles = ("Warehouse",)
    read_roles = ("Warehouse", "Management")
    http_method_names = ["get", "post", "head", "options"]

    @transaction.atomic
    def perform_create(self, serializer):
        data = serializer.validated_data.copy()
        components = data.pop("components")
        Item.objects.select_for_update().get(pk=data["product"].pk)
        if Recipe.objects.filter(product=data["product"], version=data.get("version", 1)).exists():
            raise ValidationError("This recipe version already exists. Use a new version.")
        if data.get("active", True):
            Recipe.objects.filter(product=data["product"], active=True).update(active=False)
        obj = Recipe.objects.create(**data)
        for component in components:
            RecipeLine.objects.create(recipe=obj, **component)
        serializer.instance = obj
        audit(self.request.user, "recipe.version_created", obj, after=serializer.data)


class Locations(ManagedViewSet):
    queryset = Location.objects.all().order_by("name")
    serializer_class = LocationSerializer
    write_roles = ("Warehouse",)


class Policies(ManagedViewSet):
    queryset = WorkPolicy.objects.all().order_by("name")
    serializer_class = PolicySerializer
    read_roles = ("Supply Chain", "Warehouse", "Management")


class Inventory(viewsets.ReadOnlyModelViewSet):
    queryset = StockBalance.objects.select_related("item", "location").order_by("item__sku")
    serializer_class = BalanceSerializer
    lookup_value_converter = "uuid"

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        require_role(request.user, "Warehouse", "Supply Chain", "Management")

    @action(detail=False, methods=["post"], url_path="movement")
    def movement(self, request):
        require_role(request.user, "Warehouse")
        serializer = StockCommand(data=request.data)
        serializer.is_valid(raise_exception=True)
        stock = inventory.receive(actor=request.user, **serializer.validated_data)
        return Response(self.get_serializer(stock).data)


def visible_orders(user):
    qs = (
        Order.objects.select_related("customer", "address", "area", "courier", "odoo", "shopify")
        .prefetch_related("lines__item")
        .order_by("delivery_date", "created_at")
    )
    if allowed(user, "Management", "Supply Chain", "Warehouse", "Finance"):
        return qs
    if allowed(user, "Sales"):
        return qs.filter(Q(created_by=user) | Q(customer__sales_owner=user))
    if allowed(user, "Courier"):
        # Retain the courier on deferred issued stock as its physical custodian,
        # but hide the deferred delivery until Supply Chain dispatches it again.
        return (
            qs.filter(courier=user)
            .exclude(state=Order.State.ASSIGNED, postponements__isnull=False)
            .distinct()
        )
    return qs.none()


class Orders(viewsets.ReadOnlyModelViewSet):
    serializer_class = OrderSerializer
    lookup_value_converter = "uuid"

    def list(self, request, *args, **kwargs):
        require_role(request.user, "Supply Chain", "Management", "Sales", "Courier")
        return super().list(request, *args, **kwargs)

    def retrieve(self, request, *args, **kwargs):
        require_role(request.user, "Supply Chain", "Management", "Sales", "Courier")
        return super().retrieve(request, *args, **kwargs)

    def get_queryset(self):
        qs = visible_orders(self.request.user)
        window = self.request.query_params.get("window")
        if window == "today":
            qs = qs.filter(delivery_date=business_today()).exclude(
                state__in=[Order.State.CANCELLED, Order.State.DELIVERED]
            )
        elif window == "upcoming":
            qs = qs.filter(delivery_date__gt=business_today()).exclude(
                state__in=[Order.State.CANCELLED, Order.State.DELIVERED]
            )
        elif window == "exceptions":
            qs = qs.filter(
                Q(allocated=False)
                | Q(review_required=True)
                | Q(odoo__status__in=["ERROR", "UNKNOWN"])
                | Q(customer_phone="")
                | Q(area__isnull=True)
                | Q(delivery_date__lt=business_today())
            ).exclude(state__in=[Order.State.CANCELLED, Order.State.DELIVERED])
        elif window == "postponed":
            qs = (
                qs.filter(postponements__isnull=False)
                .exclude(state__in=[Order.State.CANCELLED, Order.State.DELIVERED])
                .distinct()
            )
        elif window == "urgent":
            qs = (
                qs.filter(delivery_date=business_today())
                .filter(Q(earliest_time__isnull=False) | Q(latest_time__isnull=False))
                .exclude(state__in=[Order.State.CANCELLED, Order.State.DELIVERED])
            )
        return filter_orders(qs, self.request.query_params)

    def create(self, request):
        require_role(request.user, "Sales")
        serializer = NewOrder(data=request.data)
        serializer.is_valid(raise_exception=True)
        customer = serializer.validated_data["customer"]
        if not allowed(request.user, "Admin") and customer.sales_owner_id != request.user.pk:
            raise PermissionDenied("Choose a customer assigned to your Sales account.")
        order = orders.create_order(serializer.validated_data, request.user)
        return Response(self.get_serializer(order).data, status=status.HTTP_201_CREATED)

    def _command(self, serializer_class):
        serializer = serializer_class(data=self.request.data)
        serializer.is_valid(raise_exception=True)
        return serializer.validated_data

    @action(detail=False, methods=["post"], url_path="assign-batch")
    @transaction.atomic
    def assign_batch(self, request):
        require_role(request.user, "Supply Chain")
        data = self._command(BulkAssignment)
        assigned = []
        for entry in data["orders"]:
            get_object_or_404(visible_orders(request.user), pk=entry["id"])
            assigned.append(
                orders.assign(entry["id"], entry["version"], request.user, data["courier"])
            )
        return Response(self.get_serializer(assigned, many=True).data)

    @action(detail=True, methods=["post"])
    def reserve(self, request, pk=None):
        require_role(request.user, "Warehouse", "Supply Chain")
        order = inventory.allocate(self.get_object().pk, request.user)
        return Response(self.get_serializer(order).data)

    @action(detail=True, methods=["post"])
    def ready(self, request, pk=None):
        require_role(request.user, "Warehouse")
        data = self._command(OrderCommand)
        return Response(
            self.get_serializer(
                orders.ready(self.get_object().pk, data["version"], request.user)
            ).data
        )

    @action(detail=True, methods=["post"])
    def assign(self, request, pk=None):
        require_role(request.user, "Supply Chain")
        data = self._command(AssignCommand)
        return Response(
            self.get_serializer(
                orders.assign(
                    self.get_object().pk,
                    data["version"],
                    request.user,
                    data["courier"],
                    data.get("handover_reason", ""),
                )
            ).data
        )

    @action(detail=True, methods=["post"], url_path="freshness-review")
    def freshness_review(self, request, pk=None):
        require_role(request.user, "Warehouse")
        data = self._command(FreshnessCommand)
        order = orders.review_freshness(
            self.get_object().pk,
            data["version"],
            request.user,
            data["delivery_date"],
            data["reason"],
        )
        return Response(self.get_serializer(order).data)

    @action(detail=True, methods=["post"], url_path="receive-return")
    def receive_return(self, request, pk=None):
        require_role(request.user, "Warehouse")
        data = self._command(ReturnCommand)
        order = orders.return_stock(
            self.get_object().pk, data["version"], request.user, data["disposition"], data["reason"]
        )
        return Response(self.get_serializer(order).data)

    @action(detail=True, methods=["post"], url_path="routing")
    def routing(self, request, pk=None):
        require_role(request.user, "Supply Chain")
        data = self._command(RoutingCommand)
        order = orders.correct_routing(self.get_object().pk, data["version"], request.user, data)
        return Response(self.get_serializer(order).data)

    @action(detail=True, methods=["post"], url_path="amend")
    def amend_order(self, request, pk=None):
        require_role(request.user, "Sales")
        data = self._command(AmendCommand)
        order = orders.amend(self.get_object().pk, data["version"], request.user, data)
        return Response(self.get_serializer(order).data)

    @action(detail=True, methods=["post"], url_path="dispatch", url_name="dispatch")
    def dispatch_order(self, request, pk=None):
        require_role(request.user, "Supply Chain")
        data = self._command(OrderCommand)
        return Response(
            self.get_serializer(
                orders.dispatch(self.get_object().pk, data["version"], request.user)
            ).data
        )

    @action(detail=True, methods=["post"], url_path="market-sent")
    def market_sent(self, request, pk=None):
        require_role(request.user, "Warehouse")
        data = self._command(MarketQuantitiesCommand)
        return Response(
            self.get_serializer(
                orders.market_quantities(
                    self.get_object().pk,
                    data["version"],
                    request.user,
                    data["lines"],
                    data["reason"],
                )
            ).data
        )

    @action(detail=True, methods=["post"], url_path="market-received")
    def market_received(self, request, pk=None):
        require_role(request.user, "Finance")
        data = self._command(MarketQuantitiesCommand)
        return Response(
            self.get_serializer(
                orders.market_quantities(
                    self.get_object().pk,
                    data["version"],
                    request.user,
                    data["lines"],
                    data["reason"],
                    received=True,
                )
            ).data
        )

    @action(detail=True, methods=["post"])
    def deliver(self, request, pk=None):
        require_role(request.user, "Courier", "Supply Chain")
        data = self._command(DeliveryCommand)
        return Response(
            self.get_serializer(
                orders.deliver(
                    self.get_object().pk, data["version"], request.user, data["collected"]
                )
            ).data
        )

    @action(detail=True, methods=["post"], url_path="collection-review")
    def collection_review(self, request, pk=None):
        require_role(request.user, "Finance")
        data = self._command(CollectionReviewCommand)
        order = orders.review_collection(
            self.get_object().pk,
            data["version"],
            request.user,
            data["expected_collection"],
            data["payment_status"],
            data["reason"],
        )
        return Response(self.get_serializer(order).data)

    @action(detail=True, methods=["post"], url_path="no-answer")
    def no_answer(self, request, pk=None):
        require_role(request.user, "Courier", "Supply Chain")
        data = self._command(OrderCommand)
        result = orders.no_answer(self.get_object().pk, data["version"], request.user)
        return Response(self.get_serializer(result).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        require_role(request.user, "Courier", "Supply Chain")
        data = self._command(CancelCommand)
        return Response(
            self.get_serializer(
                orders.cancel(self.get_object().pk, data["version"], request.user, data["reason"])
            ).data
        )

    @action(detail=True, methods=["post"])
    def postpone(self, request, pk=None):
        require_role(request.user, "Courier", "Supply Chain")
        data = self._command(PostponeCommand)
        return Response(
            self.get_serializer(
                orders.postpone(self.get_object().pk, data["version"], request.user, data)
            ).data
        )

    @action(detail=True, methods=["get"])
    def history(self, request, pk=None):
        order = self.get_object()
        events = []
        for event in order.events.select_related("actor").order_by("sequence"):
            # Warehouse/Courier history does not expose commercial amounts or raw sync details.
            event_data = {
                "action": event.action,
                "time": event.created_at,
                "actor": event.actor.username if event.actor else "System",
            }
            if allowed(request.user, "Sales", "Finance", "Supply Chain", "Management"):
                event_data.update(before=event.before, after=event.after)
                if not allowed(request.user, "Sales", "Finance", "Management"):
                    hidden = {"total", "unit_price", "collected"}
                    if not allowed(request.user, "Supply Chain"):
                        hidden |= {"expected_collection", "payment_status", "payment_method"}

                    def redact(value):
                        if isinstance(value, dict):
                            return {
                                key: redact(entry)
                                for key, entry in value.items()
                                if key not in hidden
                            }
                        if isinstance(value, list):
                            return [redact(entry) for entry in value]
                        return value

                    event_data["before"] = redact(event_data["before"])
                    event_data["after"] = redact(event_data["after"])
            events.append(event_data)
        return Response(events)

    @action(detail=True, methods=["post"])
    def whatsapp(self, request, pk=None):
        require_role(request.user, "Courier", "Supply Chain")
        order = self.get_object()
        if order.delivery_date != business_today() or order.state in {
            Order.State.CANCELLED,
            Order.State.DELIVERED,
        }:
            raise ValidationError(
                "The configured delivery-today message is only available for an active order due today."
            )
        digits = re.sub(r"\D", "", order.customer_phone)
        if re.fullmatch(r"01[0125]\d{8}", digits):
            digits = "20" + digits[1:]
        elif digits.startswith("00"):
            digits = digits[2:]
        if not re.fullmatch(r"[1-9]\d{7,14}", digits):
            raise ValidationError("Store the phone in full international format, starting with +.")
        policy = WorkPolicy.objects.filter(name="default").first()
        template = (
            policy.communication_template
            if policy
            else WorkPolicy._meta.get_field("communication_template").default
        )
        message = template.format(customer=order.customer_name, order=order.number)
        if order.source == Order.Source.SHOPIFY:
            from .communication import WEBSITE_DELIVERY_MESSAGE

            message = WEBSITE_DELIVERY_MESSAGE
        audit(
            request.user,
            "communication.whatsapp_launched",
            order,
            after={"template": "delivery_today"},
        )
        return Response(
            {
                "url": f"https://wa.me/{digits}?{urlencode({'text': message})}",
                "phone": digits,
                "message": message,
            }
        )


@api_view(["GET"])
def dashboard(request):
    require_role(request.user, "Supply Chain", "Management", "Sales", "Courier")
    qs = visible_orders(request.user)
    active = qs.exclude(state__in=[Order.State.DELIVERED, Order.State.CANCELLED])
    result = {
        "today": active.filter(delivery_date=business_today()).count(),
        "upcoming": active.filter(delivery_date__gt=business_today()).count(),
        "unassigned": active.filter(delivery_date=business_today(), courier__isnull=True).count(),
        "overdue": active.filter(delivery_date__lt=business_today()).count(),
        "delivered": qs.filter(
            state=Order.State.DELIVERED, delivered_at__date=business_today()
        ).count(),
        "by_area": list(
            active.filter(delivery_date=business_today())
            .values("area__name")
            .annotate(count=Count("id"))
            .order_by("area__name")
        ),
    }
    if allowed(request.user, "Finance", "Management"):
        expected = {
            row["currency"]: row["total"]
            for row in qs.filter(delivery_date=business_today())
            .exclude(state=Order.State.CANCELLED)
            .values("currency")
            .annotate(total=Sum("expected_collection"))
            .order_by("currency")
        }
        collected = {
            row["currency"]: row["total"]
            for row in qs.filter(state=Order.State.DELIVERED, delivered_at__date=business_today())
            .values("currency")
            .annotate(total=Sum("collected"))
            .order_by("currency")
        }
        result["finance"] = {
            "by_currency": [
                {
                    "currency": currency,
                    "expected_today": expected.get(currency, 0),
                    "collected_today": collected.get(currency, 0),
                }
                for currency in sorted(expected.keys() | collected.keys())
            ]
        }
    return Response(result)


@api_view(["GET"])
def couriers(request):
    require_role(request.user, "Supply Chain", "Management", "Finance")
    return Response(
        list(
            get_user_model()
            .objects.filter(is_active=True, groups__name="Courier")
            .values("id", "username", "first_name", "last_name")
        )
    )


@api_view(["GET"])
def warehouse(request):
    require_role(request.user, "Warehouse", "Supply Chain", "Management")
    qs = Order.objects.exclude(state__in=[Order.State.CANCELLED, Order.State.DELIVERED])
    raw_day = request.query_params.get("date")
    day = business_today()
    if raw_day:
        try:
            day = date.fromisoformat(raw_day)
        except ValueError as exc:
            raise ValidationError("Use a delivery date in YYYY-MM-DD format.") from exc
    selected = list(qs.filter(delivery_date=day))
    return Response({"date": day, "materials": inventory.material_report(selected)})


@api_view(["GET"])
def production(request):
    require_role(request.user, "Production", "Supply Chain", "Management")
    rows = []
    for batch in ProductionBatch.objects.select_related("item", "location").order_by("due_date"):
        allocations = (
            batch.allocations.filter(order_line__active=True)
            .filter(
                order_line__order__state__in=[
                    s
                    for s in Order.State.values
                    if s not in {Order.State.CANCELLED, Order.State.DELIVERED}
                ]
            )
            .select_related("order_line__order")
        )
        if not allocations.exists():
            continue
        rows.append(
            {
                "id": str(batch.pk),
                "product": batch.item.name,
                "sku": batch.item.sku,
                "fresh": allocations.filter(order_line__fresh=True).exists(),
                "due_date": batch.due_date,
                "location": batch.location.name,
                "quantity": str(
                    sum(
                        (
                            entry.quantity
                            for entry in allocations
                            if not entry.order_line.order.review_required
                        ),
                        Decimal(0),
                    )
                ),
                "allocations": [
                    {
                        "line": str(entry.order_line_id),
                        "order": entry.order_line.order.number,
                        "quantity": str(entry.quantity),
                        "completed": entry.completed_at is not None,
                        "review_required": entry.order_line.order.review_required,
                    }
                    for entry in allocations
                ],
            }
        )
    return Response(rows)


@api_view(["POST"])
def produce(request, line_id):
    require_role(request.user, "Production")
    lot = serializers.CharField(max_length=100).run_validation(request.data.get("lot_reference"))
    get_object_or_404(Order.objects.filter(lines__id=line_id, lines__active=True))
    order = orders.produce(line_id, request.user, lot)
    return Response(OrderSerializer(order, context={"request": request}).data)


def visible_alerts(user):
    qs = Alert.objects.all()
    if not allowed(user, "Management", "Supply Chain"):
        qs = qs.exclude(kind="FINANCE_COMPLETION")
    if allowed(user, "Management", "Supply Chain"):
        return qs
    return qs.filter(target_role__in=roles(user))


@api_view(["GET"])
def alerts(request):
    qs = visible_alerts(request.user).filter(resolved_at__isnull=True).order_by("created_at")
    return Response(
        [
            {
                "id": str(alert.pk),
                "type": alert.kind,
                "severity": alert.severity,
                "target_role": alert.target_role,
                "message": alert.message,
                "order_id": str(alert.order_id) if alert.order_id else None,
                "created_at": alert.created_at,
                "read": alert.reads.filter(user=request.user).exists(),
            }
            for alert in qs[:200]
        ]
    )


@api_view(["POST"])
def read_alert(request, alert_id):
    alert = get_object_or_404(visible_alerts(request.user), pk=alert_id)
    AlertRead.objects.get_or_create(alert=alert, user=request.user)
    return Response({"read": True})


@api_view(["GET"])
def audit_log(request):
    require_role(request.user)
    return Response(
        list(
            AuditEvent.objects.order_by("-created_at").values(
                "id",
                "actor__username",
                "action",
                "entity_type",
                "entity_id",
                "before",
                "after",
                "created_at",
            )[:200]
        )
    )


@api_view(["GET"])
def preparation_document(request):
    require_role(request.user, "Warehouse", "Supply Chain", "Management")
    from .documents import preparation

    try:
        day = date.fromisoformat(request.query_params.get("date", str(business_today())))
    except ValueError as exc:
        raise ValidationError("Use a delivery date in YYYY-MM-DD format.") from exc
    selected = list(
        visible_orders(request.user)
        .filter(delivery_date=day)
        .exclude(state__in=[Order.State.CANCELLED, Order.State.DELIVERED])
    )
    return HttpResponse(preparation(selected, str(day)), content_type="text/html; charset=utf-8")
