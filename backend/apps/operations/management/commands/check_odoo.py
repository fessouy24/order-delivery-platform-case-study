"""Read-only Odoo authentication, company identity and quotation-access check."""

import xmlrpc.client

from django.core.management.base import BaseCommand, CommandError

from apps.operations.providers import IntegrationError, Odoo, RPCTransport


class Command(BaseCommand):
    help = "Read Odoo company identity/access without creating quotations or changing records."

    def handle(self, *args, **options):
        try:
            client = Odoo()
            if client.version in {"17", "18"}:
                version = xmlrpc.client.ServerProxy(
                    client.url + "/xmlrpc/2/common", transport=RPCTransport()
                ).version()
                if str(version.get("server_serie")).split(".")[0] != client.version:
                    raise CommandError(
                        "Configured Odoo version differs from the server. Review it before connecting."
                    )
                self.stdout.write("Server version confirmed: " + client.version)
            companies = client.call(
                "res.company", "search_read", domain=[], fields=["name"], limit=20
            )
            if not isinstance(companies, list):
                raise CommandError("Unexpected Odoo company response.")
            self.stdout.write("PASS: Authentication and company read access.")
            self.stdout.write(
                "Accessible companies: " + ", ".join(str(row.get("name", "")) for row in companies)
            )
            client.find("OPSDEMO:READ_ONLY_CONNECTION_CHECK")
            self.stdout.write("PASS: Quotation search/read access.")
            client.call(
                "product.product", "search_read", domain=[], fields=["id", "default_code"], limit=1
            )
            self.stdout.write("PASS: Product/SKU read access.")
            client.call("res.partner", "search_read", domain=[], fields=["id"], limit=1)
            self.stdout.write("PASS: Customer read access.")
            if client.version in {"17", "18"}:
                can_create = client.call(
                    "sale.order", "check_access_rights", operation="create", raise_exception=False
                )
                self.stdout.write("Quotation create model permission: " + str(bool(can_create)))
                self.stdout.write(
                    "Record rules, defaults and quotation totals still need a controlled draft test."
                )
            self.stdout.write(
                "No quotation, customer, product, invoice or stock record was created or changed."
            )
        except IntegrationError as exc:
            raise CommandError(str(exc)) from None
        except (OSError, xmlrpc.client.Error, ValueError, TypeError, KeyError):
            raise CommandError(
                "Odoo version/connection response could not be verified. No records were created."
            ) from None
