"""Create synthetic records only inside the isolated local trial database."""

import json
import os
import sys
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.identity.permissions import ROLES
from apps.operations import inventory, orders
from apps.operations.common import business_today, work_calendar
from apps.operations.models import (
    Area,
    Customer,
    CustomerAddress,
    Item,
    Location,
    Order,
    Recipe,
    RecipeLine,
    WorkPolicy,
)


class Command(BaseCommand):
    help = "Seed isolated trial accounts from a private JSON document on standard input."

    @transaction.atomic
    def handle(self, *args, **options):
        if (
            not settings.DEBUG
            or os.environ.get("TRIAL_MODE") != "true"
            or settings.DATABASES["default"]["NAME"] != "opsdemo_trial"
            or any(os.environ.get(key) for key in ("SHOPIFY_SHOP_DOMAIN", "ODOO_BASE_URL"))
        ):
            raise CommandError(
                "Trial seeding is permitted only in the isolated development database."
            )
        credentials = json.load(sys.stdin)
        users = {}
        for role in ROLES:
            entry = credentials[role]
            if not entry["username"].startswith("trial_") or len(entry["password"]) < 16:
                raise CommandError("Invalid trial account configuration.")
            group, _ = Group.objects.get_or_create(name=role)
            user, created = get_user_model().objects.get_or_create(
                username=entry["username"],
                defaults={"first_name": "Trial", "last_name": role, "is_staff": role == "Admin"},
            )
            if created:
                user.set_password(entry["password"])
                user.save()
                user.groups.add(group)
            users[role] = user
        if Order.objects.exists():
            self.stdout.write("Existing trial records retained; passwords were not reset.")
            return
        WorkPolicy.objects.get_or_create(name="default", defaults={"weekdays": [0, 1, 2, 3, 5, 6]})
        location, _ = Location.objects.get_or_create(name="Trial warehouse")
        area, _ = Area.objects.get_or_create(name="Trial Cairo route")
        customer = Customer.objects.create(
            name="TRIAL — Example company",
            company=True,
            sales_owner=users["Sales"],
            phone="0000000000",
        )
        address = CustomerAddress.objects.create(
            customer=customer,
            branch_code="TRIAL-DS-01",
            area=area,
            full_address="TRIAL ONLY — Example delivery branch, Cairo",
            original_area=area.name,
        )
        stocked = Item.objects.create(
            sku="TRIAL-PACK", name="Trial stocked product", price=Decimal("100")
        )
        raw = Item.objects.create(sku="TRIAL-RAW", name="Trial raw material", kind=Item.Kind.RAW)
        fresh = Item.objects.create(
            sku="TRIAL-FRESH",
            name="Trial fresh product",
            price=Decimal("150"),
            manufactured=True,
            fresh=True,
            lead_workdays=1,
        )
        recipe = Recipe.objects.create(product=fresh)
        RecipeLine.objects.create(recipe=recipe, material=raw, quantity=Decimal("2"))
        for item in (stocked, raw):
            inventory.receive(
                item,
                location,
                Decimal("100"),
                "Trial opening stock",
                f"trial-opening:{item.sku}",
                users["Warehouse"],
            )
        day = work_calendar().on_or_after(business_today())
        for index, state in enumerate(
            ("SCHEDULED", "READY", "ASSIGNED", "OUT_FOR_DELIVERY", "DELIVERED", "FRESH"), 1
        ):
            product = fresh if state == "FRESH" else stocked
            order = orders.create_order(
                {
                    "command_key": f"trial-order:{index}",
                    "customer": customer,
                    "address": address,
                    "location": location,
                    "delivery_date": day,
                    "lines": [{"item": product, "quantity": Decimal("2")}],
                    "notes": "Synthetic trial order — no live customer or accounting effects.",
                },
                users["Sales"],
            )
            if state in {"READY", "ASSIGNED", "OUT_FOR_DELIVERY", "DELIVERED"}:
                order = orders.ready(order.pk, order.version, users["Warehouse"])
            if state in {"ASSIGNED", "OUT_FOR_DELIVERY", "DELIVERED"}:
                order = orders.assign(
                    order.pk, order.version, users["Supply Chain"], users["Courier"]
                )
            if state in {"OUT_FOR_DELIVERY", "DELIVERED"}:
                order = orders.dispatch(order.pk, order.version, users["Supply Chain"])
            if state == "DELIVERED":
                orders.deliver(order.pk, order.version, users["Courier"], order.expected_collection)
        self.stdout.write("Created seven role accounts, trial stock and six synthetic orders.")
