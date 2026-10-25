"""Apply the reviewed identifier crosswalk without changing stock or external mappings."""

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.operations.models import Item


class Command(BaseCommand):
    help = "Apply reviewed barcodes and separate internal reference numbers by exact existing SKU."

    def add_arguments(self, parser):
        parser.add_argument("crosswalk")
        parser.add_argument("--apply", action="store_true")

    @transaction.atomic
    def handle(self, *args, **options):
        rows = json.loads(Path(options["crosswalk"]).read_text(encoding="utf8"))["products"]
        references = [r["internal_reference"] for r in rows]
        codes = [r["barcode"] for r in rows if r["barcode"]]
        if len(set(references)) != len(references) or len(set(codes)) != len(codes):
            raise CommandError("Duplicate internal reference or barcode in crosswalk.")
        matched = set()
        for row in rows:
            items = list(Item.objects.select_for_update().filter(sku__in=row["match_skus"]))
            if len(items) != 1:
                raise CommandError("Expected exactly one existing product: " + row["product"])
            item = items[0]
            if item.pk in matched:
                raise CommandError("Two entries match the same product.")
            matched.add(item.pk)
            barcode = row["barcode"]
            valid = (
                barcode.isdigit()
                and len(barcode) in [8, 12, 13, 14]
                and sum(int(n) * (3 if i % 2 == 0 else 1) for i, n in enumerate(barcode[-2::-1]))
                % 10
                == (-int(barcode[-1])) % 10
            )
            if barcode and (not valid or row["checksum_valid"] is not True):
                raise CommandError("Unverified barcode: " + row["product"])
            if (
                barcode
                and Item.objects.exclude(pk=item.pk)
                .filter(specifications__barcode=barcode)
                .exists()
            ):
                raise CommandError("Barcode already belongs to another product: " + barcode)
            if (
                Item.objects.exclude(pk=item.pk)
                .filter(specifications__internal_reference=row["internal_reference"])
                .exists()
            ):
                raise CommandError("Internal reference already belongs to another product.")
            details = {
                **item.specifications,
                "internal_reference": row["internal_reference"],
                "barcode": barcode,
                "barcode_status": row["barcode_status"],
                "barcode_source": row["source"] if barcode else "",
                "demo_retailer_item_codes": ", ".join(row["demo_retailer_codes"]),
            }
            if options["apply"]:
                item.specifications = details
                item.save(update_fields=["specifications"])
        self.stdout.write(
            f"{'Updated' if options['apply'] else 'Validated'} {len(matched)} products; SKUs, stock and external mappings retained."
        )
