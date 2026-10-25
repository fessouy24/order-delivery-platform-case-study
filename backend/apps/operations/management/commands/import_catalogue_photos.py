import json
import zipfile

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.operations.models import Item


class Command(BaseCommand):
    help = "Import reviewed product photos by exact SKU, leaving unmatched trial placeholders unchanged."

    def add_arguments(self, parser):
        parser.add_argument("archive")
        parser.add_argument("--create-products", action="store_true")

    @transaction.atomic
    def handle(self, *args, **options):
        count = 0
        with zipfile.ZipFile(options["archive"]) as archive:
            for entry in json.loads(archive.read("manifest.json")):
                matches = list(Item.objects.filter(sku__in=entry["skus"]))
                if len(matches) > 1:
                    raise CommandError("Ambiguous product SKUs: " + entry["name"])
                if not matches and options["create_products"]:
                    matches = [
                        Item.objects.create(
                            sku=entry["sku"],
                            name=entry["name"] + " — " + entry["specifications"]["size"],
                            product_name=entry["name"],
                            price=entry["price"],
                            active=entry["active"],
                        )
                    ]
                for item in matches:
                    if options["create_products"]:
                        item.specifications = entry["specifications"]
                        item.produce_on_order = entry["produce_on_order"]
                        item.save(update_fields=["specifications", "produce_on_order"])
                    item.photo.save(
                        f"catalogue-{item.pk}.jpg", ContentFile(archive.read(entry["file"]))
                    )
                    count += 1
                    self.stdout.write(f"{item.sku}: {item.name}")
        self.stdout.write(
            f"Updated {count} matching products. Unmatched products were not substituted."
        )
