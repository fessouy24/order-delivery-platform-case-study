"""Render one real order locally without importing or changing it."""

from html import escape
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.operations.providers import IntegrationError, Shopify


class Command(BaseCommand):
    help = "Save a read-only local HTML preview of one recent Shopify order."

    def add_arguments(self, parser):
        parser.add_argument("--output", required=True)

    def handle(self, *args, **options):
        try:
            client = Shopify()
            data = client.graphql(
                """query { orders(first:1,sortKey:CREATED_AT,reverse:true) { nodes {
                name createdAt displayFinancialStatus displayFulfillmentStatus currencyCode
                currentTotalPriceSet { shopMoney { amount currencyCode } }
                totalOutstandingSet { shopMoney { amount currencyCode } }
                shippingAddress { name phone address1 address2 city province country zip }
                customAttributes { key value }
                lineItems(first:100) { nodes { name sku quantity currentQuantity
                    originalUnitPriceSet { shopMoney { amount currencyCode } } }
                    pageInfo { hasNextPage } }
            } } }""",
                {},
            )
            nodes = data["orders"]["nodes"]
            if not nodes:
                raise CommandError("No accessible recent orders were found.")
            order = nodes[0]

            def e(value):
                return escape(str(value or "—"))

            address = order.get("shippingAddress") or {}
            details = (
                "<br>".join(
                    e(address[key])
                    for key in (
                        "name",
                        "address1",
                        "address2",
                        "city",
                        "province",
                        "zip",
                        "country",
                        "phone",
                    )
                    if address.get(key)
                )
                or "No shipping address returned"
            )
            rows = "".join(
                "<tr><td>"
                + e(line["name"])
                + "<small>"
                + e(line.get("sku"))
                + "</small></td><td>"
                + e(line["currentQuantity"])
                + "</td><td>"
                + e(line["originalUnitPriceSet"]["shopMoney"]["amount"])
                + " "
                + e(order["currencyCode"])
                + "</td></tr>"
                for line in order["lineItems"]["nodes"]
            )
            attrs = "".join(
                "<li>" + e(row["key"]) + ": " + e(row["value"]) + "</li>"
                for row in order["customAttributes"]
            )
            total = order["currentTotalPriceSet"]["shopMoney"]
            outstanding = order["totalOutstandingSet"]["shopMoney"]
            html = """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>OPSDEMO · Shopify order preview</title>
            <style>*{box-sizing:border-box}body{margin:0;background:#f5f5f5;color:#232323;font:16px/1.5 'Segoe UI',Arial,sans-serif}header{background:#111;color:white;padding:22px 32px;border-bottom:3px solid #db1215}main{max-width:1050px;margin:28px auto;padding:0 24px}h1{margin:8px 0}h2{font-size:19px}small{display:block;color:#545454}.notice{padding:16px 20px;border:1px solid #ddd;background:#fff;border-radius:10px}section{background:white;border:1px solid #ebebeb;border-radius:12px;padding:24px;margin:20px 0}.tags{display:flex;gap:12px;flex-wrap:wrap}.tag{background:#f2f2f2;padding:8px 12px;border-radius:6px}.table{overflow:auto}table{width:100%;border-collapse:collapse}th,td{padding:14px 10px;text-align:left;border-bottom:1px solid #ebebeb}th{background:#f5f5f5}.totals{display:flex;gap:36px;flex-wrap:wrap}.totals strong{font-size:24px}.columns{display:grid;grid-template-columns:1fr 1fr;gap:20px}.columns section{margin-top:0}@media(max-width:650px){.columns{grid-template-columns:1fr}main{padding:0 16px}header{padding:18px}th,td{padding:10px 6px}}</style>
            <header><strong>OPSDEMO</strong> · Shopify order preview</header><main><p class="notice"><strong>Real Shopify order · Read-only snapshot</strong><br>No order was imported or changed. This preview is separate from the operational app. It does not refresh automatically.</p>"""
            html += (
                "<section><small>SHOPIFY / "
                + e(client.store)
                + "</small><h1>Order "
                + e(order["name"])
                + "</h1><p>Created "
                + e(order["createdAt"])
                + "</p><div class='tags'><span class='tag'>Payment: "
                + e(order["displayFinancialStatus"])
                + "</span><span class='tag'>Fulfillment: "
                + e(order["displayFulfillmentStatus"])
                + "</span></div></section>"
            )
            html += (
                "<section><h2>Products / المنتجات</h2><div class='table'><table><thead><tr><th>Product / SKU</th><th>Current quantity</th><th>Original unit price</th></tr></thead><tbody>"
                + rows
                + "</tbody></table></div>"
            )
            if order["lineItems"]["pageInfo"]["hasNextPage"]:
                html += "<p>Only the first 100 lines are shown.</p>"
            html += (
                "<div class='totals'><p><small>Current Shopify total</small><strong>"
                + e(total["amount"])
                + " "
                + e(total["currencyCode"])
                + "</strong></p><p><small>Outstanding</small><strong>"
                + e(outstanding["amount"])
                + " "
                + e(outstanding["currencyCode"])
                + "</strong></p></div><small>Total may include Shopify taxes, shipping and discounts; original unit prices may differ from final charges. Product photos need additional product access.</small></section>"
            )
            html += (
                "<div class='columns'><section><h2>Delivery details / بيانات التسليم</h2><p>"
                + details
                + "</p></section><section><h2>Order attributes</h2>"
                + (
                    "<ul>" + attrs + "</ul>"
                    if attrs
                    else "<p>No custom attributes returned. A delivery day has not been inferred.</p>"
                )
                + "</section></div></main></html>"
            )
            Path(options["output"]).write_text(html, encoding="utf-8")
            self.stdout.write(
                "Saved one real order preview. No imports, mutations or jobs performed."
            )
        except IntegrationError as exc:
            raise CommandError(str(exc)) from None
        except (KeyError, TypeError, ValueError):
            raise CommandError("Unexpected Shopify response; preview was not completed.") from None
