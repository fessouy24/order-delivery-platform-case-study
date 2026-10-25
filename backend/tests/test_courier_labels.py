from types import SimpleNamespace as Obj

import pytest

from apps.operations.courier_labels import label


@pytest.mark.parametrize(
    "source,name,branch,reference,en,ar",
    [
        ("SHOPIFY", "Customer", "WEB", "#9002", "Website: 9002", "ويب سايت: 9002"),
        (
            "B2B",
            "Demo Retailer",
            "DS 043 — Demo District",
            "",
            "Demo Retailer: DS43",
            "متجر تجريبي: DS43",
        ),
        ("B2B", "Demo Retailer", "DS-1", "", "Demo Retailer: DS1", "متجر تجريبي: DS1"),
        (
            "B2B",
            "Example Store",
            "Demo District",
            "",
            "Example Store: Demo District",
            "Example Store: Demo District",
        ),
        (
            "SHOPIFY",
            "Customer",
            "WEB",
            "",
            "Website: Number not recorded",
            "ويب سايت: رقم الطلب غير مسجل",
        ),
    ],
)
def test_simple_courier_labels(source, name, branch, reference, en, ar):
    order = Obj(
        source=source,
        customer_name=name,
        address=Obj(branch_code=branch),
        shopify=Obj(order_number=reference),
        number="OPS-51A083B5A74A",
    )
    assert label(order) == en
    assert label(order, True) == ar
    assert "OPS-" not in label(order)
