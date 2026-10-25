import io
import json
from datetime import datetime
from zipfile import ZipFile

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.operations.models import IntegrationJob, Order, OrderWorkbookSnapshot, ShopifyReference
from apps.operations.order_details_xlsx import HEADERS
from tests.scenarios import stocked_product

pytestmark = pytest.mark.django_db


def payload():
    return {
        "source_name": "Original.xlsx",
        "source_sha256": "a" * 64,
        "data": {
            "headers": HEADERS,
            "months": [
                {
                    "name": "Oct 2026",
                    "rows": [
                        [
                            "#9002",
                            "S001",
                            "Customer",
                            "00123456",
                            "2026-10-01T09:00:00",
                            "Delivered",
                            None,
                            "Azazy",
                            "Cairo",
                            "Original note",
                        ]
                    ],
                },
                {
                    "name": "Sep 2026",
                    "rows": [
                        [
                            "#3900",
                            "S002",
                            "Other",
                            "00987654",
                            "2026-09-01T09:00:00",
                            "n",
                            None,
                            "Saad",
                            "Giza",
                            None,
                        ]
                    ],
                },
            ],
        },
    }


def test_history_import_is_idempotent_and_not_actionable(tmp_path, api, user_factory):
    path = tmp_path / "history.json"
    path.write_text(json.dumps(payload()), encoding="utf-8")
    before = (Order.objects.count(), IntegrationJob.objects.count())
    call_command("import_order_workbook_history", str(path), stdout=io.StringIO())
    call_command("import_order_workbook_history", str(path), stdout=io.StringIO())
    assert OrderWorkbookSnapshot.objects.count() == 1
    assert before == (Order.objects.count(), IntegrationJob.objects.count())
    api.force_authenticate(user_factory("Management"))
    r = api.get("/api/v1/reports/orders/table/?dataset=history").json()
    assert r["count"] == 2 and r["headers"] == HEADERS
    assert r["results"][0]["values"][3] == "00123456"
    assert r["results"][0]["values"][6] is None
    assert r["colors"]["Delivered"] == ["2A914C", "FFFFFF"]
    assert r["summary"] == {"Delivered": 1, "n": 1}


def test_history_filters_export_and_no_write_endpoint(api, user_factory):
    p = payload()
    OrderWorkbookSnapshot.objects.create(**p)
    api.force_authenticate(user_factory("Supply Chain"))
    url = "/api/v1/reports/orders/table/?dataset=history&month=Oct%202026&status=Delivered&report_courier=Azazy&q=00123456"
    result = api.get(url).json()
    assert result["count"] == 1
    assert (
        api.get("/api/v1/reports/orders/table/?dataset=history&start=2026-10-01").json()["count"]
        == 0
    )
    export = api.get(url + "&export=xlsx")
    with ZipFile(io.BytesIO(export.content)) as z:
        sheet = z.read("xl/worksheets/sheet1.xml").decode()
        assert "00123456" in sheet and "00987654" not in sheet
        assert '<c r="E2" s="4"><v>' in sheet
    assert api.post("/api/v1/reports/orders/table/", {}, format="json").status_code == 405


@pytest.mark.parametrize("role", ["Sales", "Finance", "Warehouse", "Courier", "Production"])
def test_history_is_not_exposed_to_other_departments(api, user_factory, role):
    OrderWorkbookSnapshot.objects.create(**payload())
    api.force_authenticate(user_factory(role))
    assert api.get("/api/v1/reports/orders/table/?dataset=history").status_code == 403


def test_live_report_uses_actual_source_creation_and_scoped_orders(setup_order, api, user_factory):
    owner, location, _, _, make = setup_order
    order = make([(stocked_product(location), 1)])
    order.customer_phone = "00123456"
    order.save(update_fields=["customer_phone"])
    ShopifyReference.objects.create(
        order=order,
        store="test.myshopify.com",
        external_id="1",
        order_number="#9002",
        order_created_at=timezone.make_aware(datetime(2026, 9, 15, 10)),
    )
    api.force_authenticate(owner)
    r = api.get("/api/v1/reports/orders/table/?month=Sep%202026&q=00123456").json()
    assert r["count"] == 1 and r["headers"][5:8] == ["Status", "Order delivery date", "Courier"]
    assert r["results"][0]["values"][0] == "#9002"
    assert r["results"][0]["values"][4].startswith("2026-09-15")
    assert api.get("/api/v1/reports/orders/table/?q=9002").json()["count"] == 1
    api.force_authenticate(user_factory("Sales", "unrelated-sales"))
    assert api.get("/api/v1/reports/orders/table/").json()["count"] == 0
    api.force_authenticate(user_factory("Courier"))
    assert api.get("/api/v1/reports/orders/table/").status_code == 403


def test_bad_report_parameters_and_empty_history(api, user_factory):
    api.force_authenticate(user_factory("Management"))
    assert api.get("/api/v1/reports/orders/table/?dataset=history").json()["count"] == 0
    for query in [
        "page=0",
        "page=oops",
        "dataset=unknown",
        "start=not-a-date",
        "start=2026-10-22&end=2026-01-01",
    ]:
        assert api.get("/api/v1/reports/orders/table/?" + query).status_code == 400


def test_history_keeps_na_dates_and_blank_customers(api, user_factory):
    p = payload()
    v = p["data"]["months"][0]["rows"][0]
    v[2] = None
    v[3] = None
    v[6] = "N/A"
    p["data"]["months"][1]["rows"][0][6] = "2026-10-21"
    OrderWorkbookSnapshot.objects.create(**p)
    api.force_authenticate(user_factory("Management"))
    url = "/api/v1/reports/orders/table/?dataset=history"
    assert api.get(url).json()["results"][0]["values"][2:4] == [None, None]
    assert api.get(url + "&start=2026-10-20").json()["count"] == 1
    response = api.get(url + "&export=xlsx")
    assert response.status_code == 200
    with ZipFile(io.BytesIO(response.content)) as z:
        assert "N/A" in z.read("xl/worksheets/sheet1.xml").decode()
