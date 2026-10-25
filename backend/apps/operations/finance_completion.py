"""Finance closeout and reconciled Odoo sales order / draft invoice outbox."""

import os
import uuid
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from apps.identity.permissions import require_role

from .common import audit
from .models import (
    Alert,
    ExternalMapping,
    FinanceCompletion,
    MarketQuotation,
    OccasionalOrder,
    Order,
)
from .providers import IntegrationError, Odoo, UnknownCreateResult

MESSAGES = {
    "WEBSITE": "الأوردر دون يا إكسلانس 😂😂",
    "MARKET": "الماركت متوصل وكلو تمام التمام 😂😂",
    "SPECIAL": "كَبِّر الـ G وروِّق الـ D، الأوردر وِصِل 😂😂",
}
WARNING = "حرام عليك يا أخي، ليه كده؟ فيه إيه؟"


def kind(order):
    if order.source == Order.Source.SHOPIFY:
        return "WEBSITE"
    return "SPECIAL" if OccasionalOrder.objects.filter(order=order).exists() else "MARKET"


def data(row):
    return {
        "id": str(row.pk),
        "kind": row.kind,
        "message": row.message,
        "warning": row.warning,
        "odoo_status": row.odoo_status,
        "odoo_stage": row.odoo_stage,
        "invoice_id": row.invoice_id,
        "invoice_name": row.invoice_name,
        "error": row.error,
        "completed_at": row.created_at,
    }


def notify_completion(order, k, warning=""):
    # One business-completion notice per order, shared by delivery and Finance closeout.
    # Management/Admin and Supply Chain see this through visible_alerts.
    return Alert.objects.get_or_create(
        key=f"finance-completed:{order.pk}",
        defaults={
            "kind": "FINANCE_COMPLETION",
            "severity": "INFO",
            "order": order,
            "target_role": "Management",
            "message": f"{order.number}: {MESSAGES[k]}" + (f" {warning}" if warning else ""),
        },
    )[0]


@transaction.atomic
def finish(order_id, actor):
    order = Order.objects.select_for_update().get(pk=order_id)
    old = FinanceCompletion.objects.filter(order=order).first()
    if old:
        return old
    if order.state != Order.State.DELIVERED or order.review_required or not order.stock_issued:
        raise ValidationError(
            "Complete delivery and resolve order review before Finance completion."
        )
    k = kind(order)
    lines = list(order.lines.all())
    if not lines:
        raise ValidationError("Order has no products.")
    if k == "WEBSITE":
        from .courier_cash import CourierCashEntry, website_cod

        if website_cod(order):
            if not CourierCashEntry.objects.filter(order=order, settlement__isnull=False).exists():
                raise ValidationError("Complete the courier cash handover first.")
        elif order.payment_status != "PAID" or order.expected_collection != 0:
            raise ValidationError("Confirm full online payment before completion.")
    elif k == "MARKET":
        if any(line.received_quantity is None for line in lines):
            raise ValidationError("Finance must record every market received quantity first.")
    elif (
        order.courier_collection
        and order.collected != order.courier_collection
        and order.payment_status != "PAID"
    ):
        raise ValidationError("Reconcile the special order payment before completion.")
    snapshot = [
        {
            "item": str(line.item_id),
            "sku": line.sku,
            "ordered": str(line.quantity),
            "sent": str(line.fulfillment_quantity),
            "received": str(line.received_quantity if k == "MARKET" else line.fulfillment_quantity),
            "unit_price": str(line.unit_price),
        }
        for line in lines
    ]
    ordered = sum((line.quantity for line in lines), Decimal(0))
    sent = sum((line.fulfillment_quantity for line in lines), Decimal(0))
    warning = WARNING if k == "MARKET" and sent * 2 < ordered else ""
    row = FinanceCompletion.objects.create(
        order=order, actor=actor, kind=k, message=MESSAGES[k], warning=warning, snapshot=snapshot
    )
    notify_completion(order, k, warning)
    audit(
        actor,
        "finance.order_completed",
        order,
        after={"completion": str(row.pk), "kind": k, "received": snapshot, "warning": warning},
    )
    return row


@api_view(["GET", "POST"])
def completion(request, order_id):
    require_role(
        request.user, "Finance", "Management"
    ) if request.method == "GET" else require_role(request.user, "Finance")
    order = get_object_or_404(Order, pk=order_id)
    if request.method == "POST":
        if request.data.get("confirm") is not True:
            raise ValidationError("Confirm Finance completion explicitly.")
        row = finish(order.pk, request.user)
    else:
        row = FinanceCompletion.objects.filter(order=order).first()
    return (
        Response(data(row))
        if request.method == "POST"
        else Response({"completion": data(row) if row else None})
    )


@api_view(["POST"])
def retry(request, order_id):
    require_role(request.user, "Finance", "Supply Chain")
    with transaction.atomic():
        row = get_object_or_404(FinanceCompletion.objects.select_for_update(), order_id=order_id)
        if row.claimed_at and row.claimed_at > timezone.now() - timedelta(minutes=5):
            raise ValidationError("Odoo completion is being checked.")
        if row.odoo_status != "SUCCEEDED":
            row.odoo_status = "PENDING"
            row.save(update_fields=["odoo_status"])
        audit(
            request.user, "finance.odoo_retry_requested", row.order, after={"stage": row.odoo_stage}
        )
    return Response(data(row))


def one(adapter, model, domain, fields):
    rows = adapter.call(model, "search_read", domain=domain, fields=fields, limit=2)
    if len(rows) != 1:
        raise IntegrationError(
            f"Expected exactly one linked {model} record. Review Odoo before continuing."
        )
    return rows[0]


def draft_values(adapter, job, quote):
    """Invoice only Finance-accepted product quantities, linked to original sale lines.
    Odoo computes the accounts and tax totals using its own draft-invoice model.
    No delivery quantities, stock moves, sale prices, or posted invoices are rewritten.
    """
    remote = adapter.call(
        "sale.order.line",
        "search_read",
        domain=[["order_id", "=", quote["id"]], ["display_type", "=", False]],
        fields=[
            "id",
            "product_id",
            "product_uom_qty",
            "price_unit",
            "discount",
            "tax_id",
            "product_uom",
            "is_downpayment",
        ],
        limit=1001,
    )
    if len(remote) > 1000 or any(line.get("is_downpayment") for line in remote):
        raise IntegrationError("Odoo order lines or down payments need review.")
    wanted = {}
    accepted = {}
    prices = {}
    for line in job.snapshot:
        mapping = ExternalMapping.objects.filter(
            provider="ODOO", entity_type="item", entity_id=line["item"]
        ).first()
        if not mapping or not str(mapping.external_id).isdigit():
            raise IntegrationError("Product Odoo mapping is missing.")
        pid = int(mapping.external_id)
        if pid in wanted:
            raise IntegrationError("Repeated Odoo product lines need review before invoicing.")
        wanted[pid] = Decimal(line["ordered"])
        accepted[pid] = Decimal(line["received"])
        prices[pid] = Decimal(line["unit_price"])
    if len(remote) != len(wanted):
        raise IntegrationError("Odoo product lines differ from the completed local order.")
    result = []
    for line in remote:
        pid = line["product_id"][0] if line["product_id"] else None
        if pid not in wanted or Decimal(str(line["product_uom_qty"])) != wanted[pid]:
            raise IntegrationError("Odoo ordered products or quantities changed.")
        net = Decimal(str(line["price_unit"])) * (1 - Decimal(str(line["discount"])) / 100)
        if net.quantize(Decimal(".01")) != prices[pid].quantize(Decimal(".01")):
            raise IntegrationError("Odoo unit prices or discounts changed.")
        qty = accepted[pid]
        if qty > 0:
            result.append(
                [
                    0,
                    0,
                    {
                        "product_id": pid,
                        "quantity": float(qty),
                        "product_uom_id": line["product_uom"][0],
                        "price_unit": line["price_unit"],
                        "discount": line["discount"],
                        "tax_ids": [[6, 0, line["tax_id"]]],
                        "sale_line_ids": [[6, 0, [line["id"]]]],
                    },
                ]
            )
    if not result:
        raise IntegrationError(
            "No items were accepted. Review the order instead of creating an empty invoice."
        )
    return {
        "move_type": "out_invoice",
        "company_id": quote["company_id"][0],
        "partner_id": quote["partner_invoice_id"][0],
        "currency_id": quote["currency_id"][0],
        "invoice_origin": quote["name"],
        "ref": f"OPSDEMO-COMPLETE:{job.order_id}",
        "invoice_line_ids": result,
        "invoice_payment_term_id": quote["payment_term_id"][0]
        if quote["payment_term_id"]
        else False,
        "fiscal_position_id": quote["fiscal_position_id"][0]
        if quote["fiscal_position_id"]
        else False,
    }


def verify_invoice(adapter, invoice, values):
    if (
        invoice["state"] != "draft"
        or invoice["move_type"] != "out_invoice"
        or invoice["company_id"][0] != values["company_id"]
        or invoice["partner_id"][0] != values["partner_id"]
        or invoice["currency_id"][0] != values["currency_id"]
    ):
        raise IntegrationError(
            "The linked invoice is not the expected company draft. Finance must review it."
        )
    actual = adapter.call(
        "account.move.line",
        "search_read",
        domain=[["move_id", "=", invoice["id"]], ["display_type", "=", "product"]],
        fields=["product_id", "quantity", "price_unit", "discount", "tax_ids", "sale_line_ids"],
        limit=1001,
    )

    def signature(line):
        return (
            line["product_id"][0] if isinstance(line["product_id"], list) else line["product_id"],
            Decimal(str(line["quantity"])),
            Decimal(str(line["price_unit"])),
            Decimal(str(line["discount"])),
            tuple(sorted(line["tax_ids"])),
            tuple(sorted(line["sale_line_ids"])),
        )

    expected = []
    for _, _, line in values["invoice_line_ids"]:
        expected.append(
            signature(
                {
                    **line,
                    "tax_ids": line["tax_ids"][0][2],
                    "sale_line_ids": line["sale_line_ids"][0][2],
                }
            )
        )
    if sorted(map(signature, actual)) != sorted(expected):
        raise IntegrationError(
            "Invoice quantities, prices or sale links differ from Finance acceptance."
        )


def process(pk):
    if (
        os.environ.get("TRIAL_MODE") == "true"
        or os.environ.get("ODOO_COMPLETION_ENABLED") != "true"
    ):
        return
    with transaction.atomic():
        job = FinanceCompletion.objects.select_for_update().get(pk=pk)
        if job.odoo_status == "SUCCEEDED" or (
            job.claimed_at and job.claimed_at > timezone.now() - timedelta(minutes=5)
        ):
            return
        job.claim_token = uuid.uuid4()
        job.claimed_at = timezone.now()
        job.odoo_status = "RUNNING"
        job.save()
    token = job.claim_token

    def save(**values):
        if not FinanceCompletion.objects.filter(pk=pk, claim_token=token).update(**values):
            raise IntegrationError("Another worker owns this Odoo completion.")
        for key, value in values.items():
            setattr(job, key, value)

    try:
        order = job.order
        if order.state != Order.State.DELIVERED or order.review_required:
            raise IntegrationError("Local delivery needs review.")
        adapter = Odoo()
        market = MarketQuotation.objects.filter(order=order).first()
        if job.database and job.database != adapter.database:
            raise IntegrationError("Odoo database changed. Review the linked records.")
        if market:
            if market.database != adapter.database:
                raise IntegrationError("Imported quotation belongs to another Odoo database.")
            oid = market.quotation_id
        else:
            linked = getattr(order, "odoo", None)
            if not linked or linked.status != "SYNCED" or not linked.external_id.isdigit():
                raise IntegrationError("Wait for the linked Odoo quotation to finish syncing.")
            found = adapter.find(f"OPSDEMO:{order.pk}")
            if len(found) != 1 or str(found[0]["id"]) != linked.external_id:
                raise IntegrationError("The Odoo quotation reference needs review.")
            oid = int(linked.external_id)
        quote = one(
            adapter,
            "sale.order",
            [["id", "=", oid]],
            [
                "id",
                "name",
                "state",
                "company_id",
                "partner_invoice_id",
                "currency_id",
                "payment_term_id",
                "fiscal_position_id",
                "amount_total",
            ],
        )
        if (
            quote["currency_id"][1] != order.currency
            or Decimal(str(quote["amount_total"])).quantize(Decimal(".01")) != order.total
        ):
            raise IntegrationError("Odoo currency or order total changed.")
        values = draft_values(adapter, job, quote)
        save(database=adapter.database, sales_order_id=oid)
        if quote["state"] in ["draft", "sent"]:
            if job.odoo_stage == "CONFIRMING":
                raise UnknownCreateResult(
                    "Previous confirmation is uncertain. Check Odoo before another confirmation."
                )
            save(odoo_stage="CONFIRMING")
            adapter.call("sale.order", "action_confirm", ids=[oid])
            quote = one(adapter, "sale.order", [["id", "=", oid]], ["id", "state"])
        if quote["state"] not in ["sale", "done"]:
            raise IntegrationError("Odoo sales order is not confirmed.")
        if job.odoo_stage != "INVOICING":
            save(odoo_stage="CONFIRMED")
        key = values["ref"]
        fields = ["id", "name", "state", "move_type", "company_id", "partner_id", "currency_id"]
        invoices = adapter.call(
            "account.move", "search_read", domain=[["ref", "=", key]], fields=fields, limit=2
        )
        if len(invoices) > 1:
            raise IntegrationError("Multiple completion invoices need Finance review.")
        if not invoices:
            if job.odoo_stage == "INVOICING":
                raise UnknownCreateResult(
                    "Invoice submission is uncertain. Reconcile its reference before retrying."
                )
            # Do not create another invoice beside an existing invoice from another workflow.
            existing = adapter.call(
                "account.move",
                "search_read",
                domain=[
                    ["invoice_line_ids.sale_line_ids.order_id", "=", oid],
                    ["state", "!=", "cancel"],
                    ["move_type", "=", "out_invoice"],
                ],
                fields=["id"],
                limit=1,
            )
            if existing:
                raise IntegrationError(
                    "An invoice already exists for this sales order. Finance must reconcile it."
                )
            save(odoo_stage="INVOICING")
            adapter.call("account.move", "create", vals_list=[values])
            invoices = adapter.call(
                "account.move", "search_read", domain=[["ref", "=", key]], fields=fields, limit=2
            )
            if len(invoices) != 1:
                raise UnknownCreateResult(
                    "Draft invoice creation has not been verified. Check Odoo before retrying."
                )
        invoice = invoices[0]
        verify_invoice(adapter, invoice, values)
        save(
            invoice_id=invoice["id"],
            invoice_name=invoice["name"],
            odoo_status="SUCCEEDED",
            odoo_stage="DRAFT_INVOICE",
            error="",
        )
        audit(
            None,
            "odoo.draft_invoice_verified",
            order,
            after={
                "sales_order_id": oid,
                "invoice_id": invoice["id"],
                "database": adapter.database,
            },
        )
    except UnknownCreateResult as exc:
        save(odoo_status="UNKNOWN", error=str(exc)[:500])
    except Exception as exc:
        status = "UNKNOWN" if job.odoo_stage in ["CONFIRMING", "INVOICING"] else "ERROR"
        save(
            odoo_status=status,
            error=str(exc)[:500]
            if isinstance(exc, IntegrationError)
            else "Unexpected Odoo completion failure. Review the linked records.",
        )
    finally:
        if job.odoo_status in {"ERROR", "UNKNOWN"}:
            Alert.objects.update_or_create(
                key=f"odoo-completion:{job.order_id}",
                defaults={
                    "kind": "ODOO_COMPLETION",
                    "order": job.order,
                    "target_role": "Finance",
                    "message": job.error,
                    "resolved_at": None,
                },
            )
        elif job.odoo_status == "SUCCEEDED":
            Alert.objects.filter(key=f"odoo-completion:{job.order_id}").update(
                resolved_at=timezone.now()
            )
        FinanceCompletion.objects.filter(pk=pk, claim_token=token).update(claimed_at=None)
