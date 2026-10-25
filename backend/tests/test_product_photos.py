import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from apps.operations import orders
from tests.scenarios import stocked_product

pytestmark = pytest.mark.django_db


def photo():
    data = io.BytesIO()
    Image.new("RGB", (1200, 700), "white").save(data, "PNG")
    return SimpleUploadedFile("product.png", data.getvalue(), content_type="image/png")


def test_staff_photo_upload_is_resized_and_saved(
    setup_order, api, user_factory, settings, tmp_path
):
    settings.MEDIA_ROOT = tmp_path
    _, location, _, _, _ = setup_order
    item = stocked_product(location)
    api.force_authenticate(user_factory("Warehouse"))
    response = api.post(f"/api/v1/items/{item.pk}/photo/", {"photo": photo()}, format="multipart")
    assert response.status_code == 200
    item.refresh_from_db()
    assert response.data["photo_url"] == item.photo_url
    with item.photo.open("rb") as stored:
        with Image.open(stored) as image:
            assert image.format == "JPEG"
            assert image.width == 960
            assert image.height <= 960


def test_invalid_photo_and_nonwarehouse_upload_are_rejected(
    setup_order, api, user_factory, settings, tmp_path
):
    settings.MEDIA_ROOT = tmp_path
    _, location, _, _, _ = setup_order
    item = stocked_product(location)
    api.force_authenticate(user_factory("Sales", "photo-sales"))
    assert (
        api.post(
            f"/api/v1/items/{item.pk}/photo/", {"photo": photo()}, format="multipart"
        ).status_code
        == 403
    )
    api.force_authenticate(user_factory("Warehouse"))
    invalid = SimpleUploadedFile("fake.png", b"not an image", content_type="image/png")
    assert (
        api.post(
            f"/api/v1/items/{item.pk}/photo/", {"photo": invalid}, format="multipart"
        ).status_code
        == 400
    )


def test_courier_can_view_only_assigned_product_photos(
    setup_order, api, user_factory, settings, tmp_path
):
    settings.MEDIA_ROOT = tmp_path
    _, location, _, _, make = setup_order
    item = stocked_product(location)
    warehouse = user_factory("Warehouse")
    api.force_authenticate(warehouse)
    api.post(f"/api/v1/items/{item.pk}/photo/", {"photo": photo()}, format="multipart")
    order = make([(item, 1)])
    order = orders.ready(order.pk, order.version, warehouse)
    courier = user_factory("Courier", "assigned-photo-courier")
    orders.assign(order.pk, order.version, user_factory("Supply Chain"), courier)
    api.force_authenticate(courier)
    response = api.get(f"/api/v1/items/{item.pk}/photo/")
    assert response.status_code == 200
    assert response["Content-Type"] == "image/jpeg"
    assert "private" in response["Cache-Control"]
    api.force_authenticate(user_factory("Courier", "other-photo-courier"))
    assert api.get(f"/api/v1/items/{item.pk}/photo/").status_code == 403


def test_online_order_filters_match_csv_and_keep_sales_scope(setup_order, api, user_factory):
    _, location, customer, _, make = setup_order
    item = stocked_product(location)
    selected = make([(item, 1)])
    make([(item, 2)])
    api.force_authenticate(user_factory("Management"))
    assert api.get(f"/api/v1/orders/?q={selected.number}").data["count"] == 1
    response = api.get(
        f"/api/v1/reports/orders/?start=2026-10-04&end=2026-10-04&q={selected.number}"
    )
    assert selected.number.encode() in response.content
    assert response.content.count(b"OPS-") == 1
    api.force_authenticate(user_factory("Sales", "unrelated-sales"))
    assert api.get(f"/api/v1/orders/?q={customer.name}").data["count"] == 0


def test_all_dates_export_includes_future_orders_and_arabic_headers(setup_order, api, user_factory):
    from datetime import date

    _, location, _, _, make = setup_order
    order = make([(stocked_product(location), 1)], delivery_date=date(2027, 10, 4))
    api.force_authenticate(user_factory("Management"))
    response = api.get("/api/v1/reports/orders/?all_dates=1&lang=ar")
    assert response.status_code == 200
    text = response.content.decode("utf-8-sig")
    assert text.startswith("الطلب,المصدر,العميل")
    assert order.number in text
    assert "المطلوب تحصيله" in text
