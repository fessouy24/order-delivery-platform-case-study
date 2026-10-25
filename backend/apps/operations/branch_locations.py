import re

from django.core.exceptions import ValidationError
from django.db import transaction

from .models import CustomerAddress


def ds_code(value):
    match = re.fullmatch(r"DS[\s_-]*(\d+)", value.strip(), re.I)
    return f"DS{int(match[1])}" if match else None


@transaction.atomic
def import_demo_retailer_locations(customer, records):
    if not customer.company or not any(
        word in customer.name.lower() for word in ("demo retailer", "متجر تجريبي")
    ):
        raise ValidationError("Select the Demo Retailer company customer only.")
    existing = {}
    for address in CustomerAddress.objects.select_for_update().filter(customer=customer):
        code = ds_code(address.branch_code)
        if code:
            if code in existing:
                raise ValidationError(
                    f"Several addresses match {code}; resolve the duplicate first."
                )
            existing[code] = address
    created = updated = kept = 0
    seen = set()
    for row in records:
        code = ds_code(row["branch_code"])
        if not code or code in seen:
            raise ValidationError("Invalid or duplicate branch code in the source.")
        seen.add(code)
        address = existing.get(code)
        if address is None:
            CustomerAddress.objects.create(
                customer=customer,
                **{
                    k: row[k]
                    for k in (
                        "branch_code",
                        "full_address",
                        "original_area",
                        "maps_url",
                        "latitude",
                        "longitude",
                    )
                },
            )
            created += 1
        elif not address.maps_url and address.latitude is None and address.longitude is None:
            address.maps_url, address.latitude, address.longitude = (
                row["maps_url"],
                row["latitude"],
                row["longitude"],
            )
            address.save(update_fields=["maps_url", "latitude", "longitude"])
            updated += 1
        else:
            kept += 1
    return {"created": created, "filled": updated, "existing_locations_kept": kept}
