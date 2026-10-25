import json
import re
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.operations.models import OrderWorkbookSnapshot
from apps.operations.order_details_xlsx import HEADERS


class Command(BaseCommand):
    help = (
        "Store a verified workbook snapshot for read-only reporting. Creates no operational orders."
    )

    def add_arguments(self, parser):
        parser.add_argument("path")

    def handle(self, *args, **options):
        payload = json.loads(Path(options["path"]).read_text(encoding="utf-8"))
        data = payload["data"]
        if data.get("headers") != HEADERS or not re.fullmatch(
            r"[a-f0-9]{64}", payload.get("source_sha256", "")
        ):
            raise CommandError("Verified source hash and the ten expected columns are required.")
        months = data.get("months", [])
        if not months or any(
            not isinstance(m.get("name"), str)
            or any(not isinstance(r, list) or len(r) != 10 for r in m.get("rows", []))
            for m in months
        ):
            raise CommandError("Invalid monthly workbook rows.")
        row, created = OrderWorkbookSnapshot.objects.get_or_create(
            source_sha256=payload["source_sha256"],
            defaults={"source_name": payload["source_name"], "data": data},
        )
        self.stdout.write(
            f"{'Stored' if created else 'Already stored'} {sum(len(m['rows']) for m in row.data['months'])} historical rows in {len(row.data['months'])} months; no operational orders created."
        )
