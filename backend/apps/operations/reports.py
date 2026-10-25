import csv
from datetime import date, timedelta

from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view
from rest_framework.exceptions import ValidationError

from apps.identity.permissions import allowed, require_role

from .common import business_today
from .documents import packing
from .filters import filter_orders


def spreadsheet_value(value):
    text = str("" if value is None else value)
    # Neutralize spreadsheet formulas in user-provided names, addresses and phone numbers.
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@", "\t", "\r", "\n")) else text


@api_view(["GET"])
def order_export(request):
    require_role(request.user, "Management", "Sales", "Supply Chain", "Finance")
    from .views import visible_orders

    if request.query_params.get("all_dates") == "1":
        start, end = date.min, date.max
    else:
        try:
            start = date.fromisoformat(
                request.query_params.get("start", str(business_today() - timedelta(days=30)))
            )
            end = date.fromisoformat(request.query_params.get("end", str(business_today())))
        except ValueError as exc:
            raise ValidationError("Use YYYY-MM-DD report dates.") from exc
        if start > end or (end - start).days > 366:
            raise ValidationError("Select a reporting period of up to 366 days.")
    rows = filter_orders(
        visible_orders(request.user).filter(delivery_date__range=[start, end]), request.query_params
    )
    if rows.count() > 10000:
        raise ValidationError("Narrow the report period to fewer than 10,000 orders.")
    if request.query_params.get("export") == "xlsx":
        from .order_details_xlsx import export_response

        return export_response(rows)
    financial = allowed(request.user, "Sales", "Finance", "Management")
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="opsdemo-orders.csv"'
    response.write("\ufeff")
    writer = csv.writer(response)
    headers = [
        "Order",
        "Source",
        "Customer",
        "DS",
        "Area",
        "Delivery date",
        "State",
        "Courier",
        "Products",
    ]
    if financial:
        headers += ["Currency", "Total", "Expected collection", "Collected", "Payment status"]
    arabic = request.query_params.get("lang") == "ar"
    header_labels = {
        "Order": "الطلب",
        "Source": "المصدر",
        "Customer": "العميل",
        "DS": "فرع التسليم",
        "Area": "المنطقة",
        "Delivery date": "تاريخ التسليم",
        "State": "الحالة",
        "Courier": "المندوب",
        "Products": "المنتجات",
        "Currency": "العملة",
        "Total": "الإجمالي",
        "Expected collection": "المطلوب تحصيله",
        "Collected": "المحصّل",
        "Payment status": "حالة الدفع",
    }
    writer.writerow(
        [header_labels.get(header, header) for header in headers] if arabic else headers
    )
    for order in rows:
        values = [
            order.number,
            order.source,
            order.customer_name,
            order.address.branch_code,
            order.area.name if order.area else "",
            order.delivery_date,
            order.state,
            order.courier.username if order.courier else "",
            "; ".join(f"{line.sku} × {line.quantity}" for line in order.lines.all()),
        ]
        if financial:
            values += [
                order.currency,
                order.total,
                order.expected_collection,
                order.collected,
                order.payment_status,
            ]
        writer.writerow([spreadsheet_value(value) for value in values])
    return response


@api_view(["GET"])
def dispatch_document(request, order_id):
    require_role(request.user, "Warehouse", "Supply Chain", "Courier", "Management")
    from .views import visible_orders

    order = get_object_or_404(visible_orders(request.user), pk=order_id)
    if order.review_required:
        raise ValidationError("Resolve the upstream review before printing a dispatch document.")
    return HttpResponse(
        packing(order, allowed(request.user, "Supply Chain", "Courier", "Finance")),
        content_type="text/html; charset=utf-8",
    )
