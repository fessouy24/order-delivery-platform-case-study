"""Read-only connectivity check; never imports orders or schedules jobs."""

from django.core.management.base import BaseCommand, CommandError

from apps.operations.providers import IntegrationError, Shopify


class Command(BaseCommand):
    help = "Check Shopify authentication and order access without importing or changing records."

    def handle(self, *args, **options):
        try:
            client = Shopify()
            data = client.graphql(
                "query { shop { myshopifyDomain } currentAppInstallation { accessScopes { handle } } }",
                {},
            )
            if data["shop"]["myshopifyDomain"].lower() != client.store:
                raise CommandError(
                    "Shopify returned a different store. Check the configured domain."
                )
            self.stdout.write("PASS: Docker can authenticate with the configured Shopify store.")
            scopes = {row["handle"] for row in data["currentAppInstallation"]["accessScopes"]}
            self.stdout.write("Granted scopes: " + ", ".join(sorted(scopes)))
            orders = client.graphql(
                "query { orders(first:1,sortKey:CREATED_AT,reverse:true) { nodes { id } } }", {}
            )["orders"]["nodes"]
            self.stdout.write("PASS: Order read access is available.")
            if orders:
                try:
                    node = client.order(orders[0]["id"])
                except IntegrationError:
                    checks = {
                        "basic order fields": "id name updatedAt createdAt cancelledAt note tags currencyCode displayFinancialStatus displayFulfillmentStatus paymentGatewayNames customAttributes { key value }",
                        "order phone": "phone",
                        "customer identity/phone": "customer { id displayName phone }",
                        "shipping address/contact/location": "shippingAddress { name phone address1 address2 city province country zip latitude longitude }",
                        "prices and outstanding balance": "currentTotalPriceSet { shopMoney { amount currencyCode } } totalOutstandingSet { shopMoney { amount currencyCode } }",
                        "line items and variant SKUs": "lineItems(first:100) { nodes { id name sku quantity currentQuantity originalUnitPriceSet { shopMoney { amount currencyCode } } variant { id sku } } pageInfo { hasNextPage } }",
                    }
                    for label, fields in checks.items():
                        try:
                            client.graphql(
                                "query($id:ID!){order(id:$id){" + fields + "}}",
                                {"id": orders[0]["id"]},
                            )
                            self.stdout.write("PASS: " + label)
                        except IntegrationError:
                            self.stdout.write("BLOCKED: " + label)
                    raise CommandError(
                        "Authentication works, but the full order query needs access/schema review. No orders imported."
                    ) from None
                self.stdout.write("PASS: The app's order query is accepted.")
                self.stdout.write(
                    "Shipping address present: " + str(bool(node.get("shippingAddress")))
                )
                self.stdout.write(
                    "All line SKUs present: "
                    + str(
                        all(
                            line.get("sku") or (line.get("variant") or {}).get("sku")
                            for line in node["lineItems"]["nodes"]
                        )
                    )
                )
                self.stdout.write(
                    "Customer fields may be empty or redacted; validate mappings before import."
                )
            else:
                self.stdout.write(
                    "No accessible recent orders: full order-field check remains pending."
                )
            self.stdout.write("No orders imported, no Shopify changes made, and no jobs scheduled.")
        except IntegrationError as exc:
            raise CommandError(str(exc)) from None
        except (KeyError, TypeError, ValueError):
            raise CommandError(
                "Shopify returned an unexpected response. Review permissions/API version."
            ) from None
