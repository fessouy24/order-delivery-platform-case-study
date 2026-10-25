import io
from datetime import date, datetime
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest
from django.utils import timezone

from apps.operations.models import Customer, CustomerAddress, IntegrationJob, ShopifyReference
from apps.operations.order_details_xlsx import HEADERS, workbook_bytes
from tests.scenarios import stocked_product

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def test_native_workbook_has_status_date_order_and_safe_text():
    row = [
        "9001",
        "S0123",
        '=HYPERLINK("x")',
        "001234567",
        datetime(2026, 10, 14, 15),
        "Delivered",
        date(2026, 10, 15),
        "Courier",
        "Cairo",
        "A & B",
    ]
    with ZipFile(io.BytesIO(workbook_bytes({"Oct 2026": [row]}))) as z:
        sheet = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
        cells = sheet.findall("m:sheetData/m:row", NS)
        headers = ["".join(c.itertext()) for c in cells[0]]
        assert headers == HEADERS
        assert headers[5:8] == ["Status", "Order delivery date", "Courier"]
        assert sheet.find('.//m:c[@r="D2"]/m:is/m:t', NS).text == "001234567"
        assert sheet.find('.//m:c[@r="C2"]/m:is/m:t', NS).text.startswith("=HYPERLINK")
        assert not sheet.findall(".//m:f", NS)
        assert sheet.find('.//m:c[@r="G2"]/m:v', NS) is not None
        table = ET.fromstring(z.read("xl/tables/table1.xml"))
        assert table.attrib["ref"] == "A1:J2"
        assert table.find("m:tableStyleInfo", NS).attrib["name"] == "TableStyleLight1"
        assert sheet.find("m:conditionalFormatting", NS).attrib["sqref"] == "F2:F2"
        assert sheet.find("m:dataValidations/m:dataValidation", NS).attrib["sqref"] == "F2:F2"


@pytest.mark.django_db
def test_order_details_export_filters_roles_and_uses_source_creation(
    setup_order, api, user_factory
):
    owner, location, _, _, make = setup_order
    product = stocked_product(location)
    order = make([(product, 1)])
    other_owner = user_factory("Sales", "another-sales")
    customer = Customer.objects.create(name="Other customer", sales_owner=other_owner)
    address = CustomerAddress.objects.create(
        customer=customer, branch_code="Other DS", full_address="Other branch"
    )
    other = make([(product, 1)], customer=customer, address=address)
    other.created_by = other_owner
    other.save(update_fields=["created_by"])
    ShopifyReference.objects.create(
        order=order,
        store="sample.myshopify.com",
        external_id="gid://shopify/Order/1",
        order_number="#9001",
        order_created_at=timezone.make_aware(datetime(2026, 9, 15, 10)),
    )
    IntegrationJob.objects.filter(order=order).update(external_number="S0123")
    api.force_authenticate(owner)
    response = api.get("/api/v1/reports/orders/?all_dates=1&export=xlsx")
    assert response.status_code == 200
    assert response["Cache-Control"] == "private, no-store"
    with ZipFile(io.BytesIO(response.content)) as z:
        workbook = ET.fromstring(z.read("xl/workbook.xml"))
        assert workbook.find("m:sheets/m:sheet", NS).attrib["name"] == "Sep 2026"
        sheet = z.read("xl/worksheets/sheet1.xml").decode()
        assert "#9001" in sheet
        assert other.number not in sheet
        assert "<f>" not in sheet
        assert (
            sum(
                len(ET.fromstring(z.read(name)).findall("m:sheetData/m:row", NS)) - 1
                for name in z.namelist()
                if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
            )
            == 1
        )
    api.force_authenticate(user_factory("Courier"))
    assert api.get("/api/v1/reports/orders/?all_dates=1&export=xlsx").status_code == 403


@pytest.mark.django_db
def test_empty_order_details_export_is_valid(api, user_factory):
    api.force_authenticate(user_factory("Management"))
    response = api.get("/api/v1/reports/orders/?all_dates=1&export=xlsx")
    assert response.status_code == 200
    with ZipFile(io.BytesIO(response.content)) as z:
        table = ET.fromstring(z.read("xl/tables/table1.xml"))
        assert table.attrib["ref"] == "A1:J1"
