import uuid
from datetime import date

from django.db.models import Q
from rest_framework.exceptions import ValidationError

from .models import Order


def filter_orders(qs, params):
    """Apply the same filters to permission-scoped online rows and CSV exports."""
    for key, lookup in (("start", "delivery_date__gte"), ("end", "delivery_date__lte")):
        if params.get(key):
            try:
                day = date.fromisoformat(params[key])
            except ValueError as exc:
                raise ValidationError("Use YYYY-MM-DD report dates.") from exc
            qs = qs.filter(**{lookup: day})
    if params.get("state"):
        if params["state"] not in Order.State.values:
            raise ValidationError("Choose a valid order status.")
        qs = qs.filter(state=params["state"])
    if params.get("courier"):
        if params["courier"] == "unassigned":
            qs = qs.filter(courier__isnull=True)
        else:
            try:
                courier_id = uuid.UUID(params["courier"])
            except ValueError as exc:
                raise ValidationError("Choose a valid courier.") from exc
            qs = qs.filter(courier_id=courier_id)
    if params.get("q", "").strip():
        query = params["q"].strip()[:160]
        qs = qs.filter(
            Q(number__icontains=query)
            | Q(shopify__order_number__icontains=query)
            | Q(customer_phone__icontains=query)
            | Q(odoo__external_number__icontains=query)
            | Q(notes__icontains=query)
            | Q(customer_name__icontains=query)
            | Q(area__name__icontains=query)
            | Q(lines__sku__icontains=query)
            | Q(lines__name__icontains=query)
        ).distinct()
    return qs
