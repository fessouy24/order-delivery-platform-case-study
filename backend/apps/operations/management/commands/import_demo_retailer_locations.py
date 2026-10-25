import json
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from apps.operations.branch_locations import import_demo_retailer_locations
from apps.operations.models import Customer


class Command(BaseCommand):
    help = "Load the partial Demo Retailer directory without overwriting existing locations."

    def add_arguments(self, parser):
        parser.add_argument(
            "--customer", required=True, help="Local Demo Retailer customer UUID, not Odoo ID."
        )
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        dataset = json.loads(
            (Path(__file__).resolve().parents[2] / "data/demo_retailer_locations.json").read_text(
                encoding="utf8"
            )
        )
        try:
            customer = Customer.objects.get(pk=options["customer"])
        except (Customer.DoesNotExist, ValidationError, ValueError) as exc:
            raise CommandError("Select an existing local Demo Retailer customer UUID.") from exc
        self.stdout.write(
            json.dumps(
                {
                    "source_stores": len(dataset["records"]),
                    "partial": True,
                    "missing_codes": dataset["missing_codes_in_range"],
                    "issues": dataset["issues"],
                }
            )
        )
        if options["apply"]:
            try:
                self.stdout.write(
                    json.dumps(import_demo_retailer_locations(customer, dataset["records"]))
                )
            except ValidationError as exc:
                raise CommandError(str(exc)) from exc
