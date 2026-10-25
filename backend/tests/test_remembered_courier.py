import json

import pytest
from django.contrib.auth.models import Group
from django.test import Client

pytestmark = pytest.mark.django_db


def login(user, remember=True):
    client = Client(enforce_csrf_checks=True)
    token = client.get("/api/v1/auth/csrf/").json()["csrf_token"]
    response = client.post(
        "/api/v1/auth/login/",
        data=json.dumps(
            {
                "username": user.username,
                "password": "test-long-password-5682",
                "remember_me": remember,
            }
        ),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=token,
    )
    assert response.status_code == 200, response.content
    return client, response


def test_remembered_courier_survives_a_new_client_and_logout_revokes(user_factory):
    user = user_factory("Courier")
    client, response = login(user)
    assert int(response.cookies["sessionid"]["max-age"]) == 30 * 86400
    fresh = Client()
    fresh.cookies["sessionid"] = response.cookies["sessionid"].value
    assert fresh.get("/api/v1/auth/me/").json()["id"] == str(user.pk)
    token = client.get("/api/v1/auth/csrf/").json()["csrf_token"]
    assert client.post("/api/v1/auth/logout/", HTTP_X_CSRFTOKEN=token).status_code == 200
    assert fresh.get("/api/v1/auth/me/").status_code == 403


@pytest.mark.parametrize("remember", [False, "true", 1])
def test_only_an_explicit_boolean_enables_remembering(user_factory, remember, settings):
    _, response = login(user_factory("Courier"), remember)
    assert int(response.cookies["sessionid"]["max-age"]) == settings.SESSION_COOKIE_AGE


@pytest.mark.parametrize(
    "role", ["Admin", "Finance", "Supply Chain", "Warehouse", "Management", "Sales"]
)
def test_desktop_roles_can_opt_into_remembered_sessions(user_factory, role, settings):
    settings.REQUIRE_STAFF_MFA = False
    client, response = login(user_factory(role))
    assert int(response.cookies["sessionid"]["max-age"]) == 30 * 86400
    fresh = Client()
    fresh.cookies["sessionid"] = response.cookies["sessionid"].value
    assert fresh.get("/api/v1/auth/me/").status_code == 200


@pytest.mark.parametrize("change", ["disabled", "password", "deleted_session"])
def test_revoked_remembered_session_requires_login(user_factory, change):
    user = user_factory("Courier")
    client, _ = login(user)
    if change == "disabled":
        user.is_active = False
        user.save()
    elif change == "password":
        user.set_password("replacement-test-only-96732")
        user.save()
    else:
        client.session.flush()
    assert client.get("/api/v1/auth/me/").status_code == 403


def test_courier_with_finance_access_can_remember(user_factory, settings):
    user = user_factory("Courier")
    user.groups.add(Group.objects.get_or_create(name="Finance")[0])
    _, response = login(user)
    assert int(response.cookies["sessionid"]["max-age"]) == 30 * 86400


def test_remember_choice_survives_optional_courier_mfa(user_factory):
    from django_otp.oath import totp
    from django_otp.plugins.otp_totp.models import TOTPDevice

    user = user_factory("Courier")
    device = TOTPDevice.objects.create(user=user, confirmed=True)
    client, response = login(user)
    assert response.json()["mfa_required"]
    assert client.get("/api/v1/auth/me/").status_code == 403
    token = client.get("/api/v1/auth/csrf/").json()["csrf_token"]
    code = str(totp(device.bin_key, device.step, device.t0, device.digits, device.drift)).zfill(6)
    response = client.post(
        "/api/v1/auth/mfa/verify/",
        data=json.dumps({"token": code}),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=token,
    )
    assert response.status_code == 200
    assert int(response.cookies["sessionid"]["max-age"]) == 30 * 86400


@pytest.mark.parametrize(
    "role", ["Admin", "Finance", "Warehouse", "Management", "Supply Chain", "Production"]
)
def test_desktop_roles_without_opt_in_keep_normal_expiry(user_factory, role, settings):
    settings.REQUIRE_STAFF_MFA = False
    _, response = login(user_factory(role), False)
    assert int(response.cookies["sessionid"]["max-age"]) == settings.SESSION_COOKIE_AGE
