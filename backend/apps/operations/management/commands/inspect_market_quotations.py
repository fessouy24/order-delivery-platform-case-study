"""Read-only discovery for the Odoo-to-market-order integration."""

import json

from django.core.management.base import BaseCommand, CommandError

from apps.operations.providers import IntegrationError, Odoo


class Command(BaseCommand):
    def handle(self, *args, **options):
        try:
            client = Odoo()
            fields = client.call("sale.order", "fields_get", attributes=["string", "type"])
            candidates = {
                name: value
                for name, value in fields.items()
                if any(
                    word in name.lower()
                    for word in ["delivery", "commitment", "market", "channel", "team", "tag"]
                )
            }
            quotes = client.call(
                "sale.order",
                "search_read",
                domain=[["state", "in", ["draft", "sent"]]],
                fields=[
                    "id",
                    "name",
                    "partner_id",
                    "partner_shipping_id",
                    "company_id",
                    "user_id",
                    "team_id",
                    "client_order_ref",
                    "origin",
                    "commitment_date",
                    "amount_total",
                    "currency_id",
                    "order_line",
                    "write_date",
                ],
                order="id desc",
                limit=20,
            )
            self.stdout.write(
                json.dumps(
                    {
                        "database": client.database,
                        "possible_routing_fields": candidates,
                        "recent_quotations": quotes,
                    },
                    ensure_ascii=True,
                )
            )
        except IntegrationError as exc:
            raise CommandError(str(exc)) from None
