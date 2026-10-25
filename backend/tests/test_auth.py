import json
import time

import pytest
from django.test import Client, override_settings
from django_otp.oath import totp
from django_otp.plugins.otp_totp.models import TOTPDevice

pytestmark = pytest.mark.django_db


@override_settings(REQUIRE_STAFF_MFA=False)
@pytest.mark.parametrize("enrolled", [False, True])
def test_admin_password_login_without_authenticator_requirement(user_factory, enrolled):
    user = user_factory("Admin")
    user.is_staff = True
    user.save()
    TOTPDevice.objects.create(user=user, name="Existing company device", confirmed=enrolled)
    client = Client(enforce_csrf_checks=True)

    def sign_in(password):
        token = client.get("/api/v1/auth/csrf/").json()["csrf_token"]
        return client.post(
            "/api/v1/auth/login/",
            data=json.dumps({"username": user.username, "password": password}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )

    assert sign_in("wrong-password").status_code == 400
    assert client.get("/api/v1/users/").status_code == 403
    response = sign_in("test-long-password-5682")
    assert response.status_code == 200
    assert response.json()["roles"] == ["Admin"]
    assert not response.json().get("mfa_required", False)
    assert client.get("/api/v1/users/").status_code == 200


def test_login_requires_csrf(user_factory):
    user = user_factory()
    client = Client(enforce_csrf_checks=True)
    response = client.post(
        "/api/v1/auth/login/",
        data=json.dumps({"username": user.username, "password": "test-long-password-5682"}),
        content_type="application/json",
    )
    assert response.status_code == 403


def test_login_logout_and_role_profile(user_factory):
    user = user_factory()
    client = Client(enforce_csrf_checks=True)
    token = client.get("/api/v1/auth/csrf/").json()["csrf_token"]
    response = client.post(
        "/api/v1/auth/login/",
        data=json.dumps({"username": user.username, "password": "test-long-password-5682"}),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=token,
    )
    assert response.status_code == 200
    assert response.json()["roles"] == ["Sales"]
    assert client.get("/api/v1/auth/me/").status_code == 200
    token = client.get("/api/v1/auth/csrf/").json()["csrf_token"]
    assert client.post("/api/v1/auth/logout/", HTTP_X_CSRFTOKEN=token).status_code == 200
    assert client.get("/api/v1/auth/me/").status_code == 403


def test_repeated_login_is_throttled(user_factory, client):
    user = user_factory()
    payload = {"username": user.username, "password": "incorrect"}
    for _ in range(8):
        assert (
            client.post("/api/v1/auth/login/", payload, content_type="application/json").status_code
            == 400
        )
    assert (
        client.post("/api/v1/auth/login/", payload, content_type="application/json").status_code
        == 429
    )


@override_settings(REQUIRE_STAFF_MFA=True)
def test_privileged_login_requires_mfa_and_rejects_token_replay(user_factory, client):
    user = user_factory("Admin")
    response = client.post(
        "/api/v1/auth/login/",
        {"username": user.username, "password": "test-long-password-5682"},
        content_type="application/json",
    )
    assert response.json()["enrollment_required"] is True
    assert client.get("/api/v1/auth/me/").status_code == 403
    assert client.post("/api/v1/auth/mfa/enroll/").status_code == 200
    device = TOTPDevice.objects.get(user=user)
    token = str(totp(device.bin_key, device.step, device.t0, device.digits, device.drift)).zfill(6)
    assert (
        client.post(
            "/api/v1/auth/mfa/verify/", {"token": token}, content_type="application/json"
        ).status_code
        == 200
    )
    assert client.get("/api/v1/auth/me/").status_code == 200
    client.post("/api/v1/auth/logout/")
    client.post(
        "/api/v1/auth/login/",
        {"username": user.username, "password": "test-long-password-5682"},
        content_type="application/json",
    )
    assert (
        client.post(
            "/api/v1/auth/mfa/verify/", {"token": token}, content_type="application/json"
        ).status_code
        == 400
    )


def test_expired_pending_login_cannot_enroll(user_factory, client):
    user = user_factory()
    session = client.session
    session["pending_auth"] = {"id": str(user.pk), "time": time.time() - 301}
    session.save()
    assert client.post("/api/v1/auth/mfa/enroll/").status_code == 403
