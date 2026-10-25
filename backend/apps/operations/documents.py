"""Printable operational documents. Escape every customer-provided field."""

from collections import defaultdict
from decimal import Decimal

from django.utils.html import escape

from . import inventory


def cell(value):
    return f"<td>{escape(str(value))}</td>"


def page(title, body):
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        + f"<title>{escape(title)}</title>"
        + "<style>body{font:14px/1.5 sans-serif;margin:30px;color:#183b2c}table{border-collapse:collapse;width:100%;margin-bottom:24px}td,th{padding:10px;border:1px solid #ccc;text-align:left}h1{font-size:24px}small{display:block}section{break-inside:avoid}@media print{body{margin:0}}</style></head><body>"
        + body
        + "</body></html>"
    )


def preparation(selected, day):
    materials = inventory.material_report(selected)
    totals = defaultdict(lambda: Decimal(0))
    for order in selected:
        if order.stock_issued or order.review_required:
            continue
        for line in order.lines.select_related("item"):
            totals[(line.sku, line.name, line.item.unit)] += line.fulfillment_quantity
    body = f"<h1>OPSDEMO warehouse preparation</h1><p>{escape(day)}</p>"
    body += "<h2>Finished products to prepare</h2><table><tr><th>SKU</th><th>Product</th><th>Unit</th><th>Quantity</th></tr>"
    for keys, amount in sorted(totals.items()):
        body += "<tr>" + "".join(cell(value) for value in (*keys, amount)) + "</tr>"
    body += "</table><h2>Combined material and stock requirements</h2><table><tr><th>Item</th><th>Warehouse</th><th>Unit</th><th>Required</th><th>Available</th><th>Reserved</th><th>Shortage</th></tr>"
    for row in materials:
        body += (
            "<tr>"
            + "".join(
                cell(row[key])
                for key in [
                    "name",
                    "location",
                    "unit",
                    "required",
                    "available",
                    "reserved",
                    "shortage",
                ]
            )
            + "</tr>"
        )
    body += "</table><h2>Orders, delivery windows and couriers</h2>"
    for order in selected:
        if order.review_required:
            body += f"<section><h3>{escape(order.number)} — HELD FOR REVIEW</h3><p>Do not prepare or dispatch until reconciliation is complete.</p></section>"
            continue
        body += f"<section><h3>{escape(order.number)} · {escape(order.customer_name)}</h3><p>{escape(order.address.branch_code)} · {escape(order.full_address)}</p><p>Courier: {escape(order.courier.username if order.courier else 'Unassigned')} · Area: {escape(order.area.name if order.area else 'Needs mapping')} · After: {escape(order.earliest_time or '—')} · Before: {escape(order.latest_time or '—')}</p><p>{escape(order.notes)}</p><ul>"
        for line in order.lines.all():
            body += f"<li>{escape(line.name)} · {escape(line.sku)} × {escape(line.fulfillment_quantity)} · {'Produced' if line.production_complete else 'Production pending' if line.recipe_id else 'Stocked'}</li>"
        body += "</ul></section>"
    return page("OPSDEMO preparation", body)


def packing(order, show_collection):
    body = f"<h1>OPSDEMO dispatch · {escape(order.number)}</h1><h2>{escape(order.customer_name)}</h2><p>{escape(order.full_address)}</p><p>{escape(order.customer_phone)} · {escape(order.delivery_date)}</p><p>After: {escape(order.earliest_time or '—')} · Before: {escape(order.latest_time or '—')}</p><p>{escape(order.notes)}</p><table><tr><th>SKU</th><th>Product</th><th>Quantity</th></tr>"
    for line in order.lines.all():
        body += (
            "<tr>"
            + "".join(cell(value) for value in [line.sku, line.name, line.fulfillment_quantity])
            + "</tr>"
        )
    body += "</table>"
    if show_collection:
        body += f"<p>Approved collection: {escape(order.expected_collection)} {escape(order.currency)}</p>"
    return page("OPSDEMO dispatch", body)
