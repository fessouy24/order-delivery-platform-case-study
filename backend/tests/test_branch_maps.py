import pytest

from apps.operations.serializers import AddressSerializer


@pytest.mark.parametrize(
    "url",
    [
        "https://maps.app.goo.gl/abc",
        "https://www.google.com/maps/place/Cairo",
        "https://goo.gl/maps/abc",
    ],
)
def test_google_maps_links_accepted(url):
    assert AddressSerializer().validate_maps_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "https://evil.example/maps",
        "https://google.com.evil.example/maps",
        "https://google.com/search?q=test",
        "https://user:secret@google.com/maps",
    ],
)
def test_unrelated_or_unsafe_links_rejected(url):
    from rest_framework.exceptions import ValidationError

    with pytest.raises(ValidationError):
        AddressSerializer().validate_maps_url(url)


@pytest.mark.django_db
def test_branch_link_saved_and_used_for_navigation(api, setup_order, user_factory):
    _, _, _, address, _ = setup_order
    api.force_authenticate(user_factory("Supply Chain"))
    response = api.patch(
        f"/api/v1/addresses/{address.pk}/",
        {"maps_url": "https://maps.app.goo.gl/branch"},
        format="json",
    )
    assert response.status_code == 200, response.data
    address.refresh_from_db()
    assert address.navigation_url == "https://maps.app.goo.gl/branch"
    address.maps_url = ""
    address.latitude = 30
    address.longitude = 31
    assert "30%2C31" in address.navigation_url
