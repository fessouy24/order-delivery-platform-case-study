import json
from pathlib import Path

import pytest
from django.core.exceptions import ValidationError

from apps.operations.branch_locations import ds_code, import_demo_retailer_locations
from apps.operations.models import Customer, CustomerAddress


@pytest.mark.parametrize("code", ["DS1", "DS-01", "ds_ 1", " DS 1 "])
def test_branch_code_variants(code):
    assert ds_code(code) == "DS1"


@pytest.mark.django_db
def test_import_is_scoped_repeatable_and_preserves_existing_locations():
    data = json.loads(
        (Path(__file__).parents[1] / "apps/operations/data/demo_retailer_locations.json").read_text(
            encoding="utf8"
        )
    )
    customer = Customer.objects.create(name="Demo Retailer", company=True)
    other = Customer.objects.create(name="Other market", company=True)
    existing = CustomerAddress.objects.create(
        customer=customer,
        branch_code="DS-01",
        full_address="Keep address",
        maps_url="https://maps.app.goo.gl/existing",
    )
    unrelated = CustomerAddress.objects.create(
        customer=other, branch_code="DS1", full_address="Other"
    )
    assert import_demo_retailer_locations(customer, data["records"])["created"] == 2
    assert import_demo_retailer_locations(customer, data["records"])["created"] == 0
    existing.refresh_from_db()
    unrelated.refresh_from_db()
    assert existing.maps_url.endswith("/existing") and existing.full_address == "Keep address"
    assert unrelated.maps_url == ""
    assert customer.addresses.count() == 3
    with pytest.raises(ValidationError):
        import_demo_retailer_locations(other, data["records"])


@pytest.mark.django_db
def test_staff_can_add_store_beyond_directory(api, user_factory):
    customer = Customer.objects.create(name="Demo Retailer", company=True)
    api.force_authenticate(user_factory("Supply Chain"))
    response = api.post(
        "/api/v1/addresses/",
        {
            "customer": str(customer.pk),
            "branch_code": "DS109",
            "full_address": "New store address",
            "maps_url": "https://maps.app.goo.gl/new",
        },
        format="json",
    )
    assert response.status_code == 201, response.data
    assert customer.addresses.get().navigation_url.endswith("/new")
