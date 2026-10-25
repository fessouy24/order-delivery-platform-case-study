"""The supplied Excel layout, with scoped live data and isolated workbook history."""

from collections import Counter, defaultdict
from datetime import date, datetime

from django.http import HttpResponse
from rest_framework.decorators import api_view
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from apps.identity.permissions import require_role

from .filters import filter_orders
from .models import OrderWorkbookSnapshot
from .order_details_xlsx import HEADERS, STATUS_COLORS, order_values, workbook_bytes


@api_view(["GET"])
def table(request):
    require_role(request.user, "Management", "Supply Chain", "Sales", "Finance")
    p = request.query_params
    dataset = p.get("dataset", "live")
    if dataset not in ["live", "history"]:
        raise ValidationError("Choose live orders or original Excel history.")
    try:
        page = int(p.get("page", "1"))
        if page < 1:
            raise ValueError()
        start = date.fromisoformat(p["start"]) if p.get("start") else None
        end = date.fromisoformat(p["end"]) if p.get("end") else None
        if start and end and start > end:
            raise ValueError()
    except ValueError as exc:
        raise ValidationError("Choose a valid page and date range.") from exc
    source_name = ""
    if dataset == "history":
        # Sales ownership cannot be inferred for historical rows.
        require_role(request.user, "Management", "Supply Chain")
        snapshot = OrderWorkbookSnapshot.objects.order_by("-created_at").first()
        source_name = snapshot.source_name if snapshot else ""
        rows = [
            {"id": f"history-{m}-{i}", "month": month["name"], "values": values}
            for m, month in enumerate(snapshot.data["months"] if snapshot else [])
            for i, values in enumerate(month["rows"])
        ]
    else:
        from .views import visible_orders

        params = {key: p[key] for key in ["start", "end", "q"] if p.get(key)}
        qs = (
            filter_orders(visible_orders(request.user), params)
            .select_related("shopify", "odoo", "courier", "area")
            .order_by("-created_at")
        )
        if qs.count() > 10000:
            raise ValidationError("Narrow the report to 10,000 orders or fewer.")
        rows = []
        for order in qs:
            values = order_values(order)
            rows.append(
                {
                    "id": str(order.pk),
                    "number": order.number,
                    "month": values[4].strftime("%b %Y") if values[4] else "Creation date unknown",
                    "values": values,
                }
            )
    # Month, status and courier choices describe this permitted dataset, not other users' orders.
    months = list(dict.fromkeys(row["month"] for row in rows))
    statuses = sorted({str(row["values"][5] or "") for row in rows})
    couriers = sorted({str(row["values"][7]) for row in rows if row["values"][7]})
    query = p.get("q", "").strip().casefold()[:160]

    def matches(row):
        v = row["values"]
        if p.get("month") and row["month"] != p["month"]:
            return False
        if p.get("status") and str(v[5] or "") != p["status"]:
            return False
        if p.get("report_courier") and str(v[7] or "") != p["report_courier"]:
            return False
        if dataset == "history":
            if query and not any(query in str(value or "").casefold() for value in v):
                return False
            try:
                delivery = date.fromisoformat(str(v[6])[:10]) if v[6] else None
            except ValueError:
                delivery = None  # Preserve the source's N/A text; it is not a date.
            if (start and (not delivery or delivery < start)) or (
                end and (not delivery or delivery > end)
            ):
                return False
        return True

    rows = [row for row in rows if matches(row)]
    if p.get("export") == "xlsx":
        groups = defaultdict(list)
        for row in rows:
            values = list(row["values"])
            for index in [4, 6]:
                if isinstance(values[index], str) and values[index]:
                    try:
                        values[index] = (
                            datetime.fromisoformat(values[index])
                            if index == 4
                            else date.fromisoformat(values[index][:10])
                        )
                    except ValueError:
                        pass  # Export source literals such as N/A unchanged.
            groups[row["month"]].append(values)
        response = HttpResponse(
            workbook_bytes(groups or {"Orders": []}),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = 'attachment; filename="opsdemo-orders-excel.xlsx"'
        return response
    return Response(
        {
            "headers": HEADERS,
            "colors": STATUS_COLORS,
            "dataset": dataset,
            "source_name": source_name,
            "count": len(rows),
            "page": page,
            "pages": max(1, (len(rows) + 49) // 50),
            "months": months,
            "statuses": statuses,
            "couriers": couriers,
            "summary": dict(Counter(str(row["values"][5] or "") for row in rows)),
            "results": rows[(page - 1) * 50 : page * 50],
        }
    )
