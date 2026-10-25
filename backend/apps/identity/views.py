import hashlib
import hmac
import json
import time

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model, login, logout
from django.core.cache import cache
from django.db import transaction
from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_GET, require_POST
from django_otp import login as otp_login
from django_otp.plugins.otp_totp.models import TOTPDevice
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .permissions import roles


def auth_audit(user, action):
    from apps.operations.common import audit

    audit(user, action, user)


def profile(user):
    return {
        "id": str(user.pk),
        "username": user.username,
        "name": user.get_full_name(),
        "roles": sorted(roles(user)),
        "mfa_verified": bool(getattr(user, "otp_device", None)),
    }


def body(request):
    try:
        data = json.loads(request.body)
        if not isinstance(data, dict):
            raise ValueError
        return data
    except (ValueError, UnicodeDecodeError):
        return None


def throttle(request, username):
    # Atomic add/incr on shared Redis in the application; fail closed if unavailable.
    address = request.META.get("REMOTE_ADDR", "unknown")
    if settings.TRUST_PROXY_CLIENT_IP:
        # Enable only when the API can be reached exclusively through our header-overwriting proxy.
        address = request.META.get("HTTP_X_REAL_IP", address)
    for value, limit in (
        ("ip:" + address, 30),
        ("account:" + username.casefold(), 8),
    ):
        key = (
            "login:"
            + hmac.new(settings.SECRET_KEY.encode(), value.encode(), hashlib.sha256).hexdigest()
        )
        if cache.add(key, 1, timeout=300):
            count = 1
        else:
            try:
                count = cache.incr(key)
            except ValueError:
                return False
        if count > limit:
            return False
    return True


@require_GET
def csrf(request):
    return JsonResponse({"csrf_token": get_token(request)})


@require_POST
@csrf_protect
def sign_in(request):
    data = body(request)
    if (
        data is None
        or not isinstance(data.get("username"), str)
        or not isinstance(data.get("password"), str)
    ):
        return JsonResponse({"detail": "Username and password are required."}, status=400)
    username = data["username"][:150]
    try:
        if not throttle(request, username):
            return JsonResponse(
                {"detail": "Too many sign-in attempts. Try again later."}, status=429
            )
    except Exception:
        return JsonResponse({"detail": "Sign-in is temporarily unavailable."}, status=503)
    user = authenticate(request, username=username, password=data["password"])
    if user is None:
        return JsonResponse({"detail": "Invalid credentials."}, status=400)
    devices = TOTPDevice.objects.filter(user=user, confirmed=True)
    privileged = user.is_staff or "Admin" in roles(user)
    if settings.REQUIRE_STAFF_MFA if privileged else devices.exists():
        request.session.flush()
        request.session["pending_auth"] = {
            "id": str(user.pk),
            "time": time.time(),
            "remember_me": data.get("remember_me") is True,
        }
        return JsonResponse({"mfa_required": True, "enrollment_required": not devices.exists()})
    login(request, user)
    session_expiry(request, user, data.get("remember_me") is True)
    auth_audit(user, "authentication.signed_in")
    return JsonResponse(profile(user))


def session_expiry(request, user, remember):
    # Remembering is an explicit opt-in; authentication and MFA remain unchanged.
    request.session.set_expiry(30 * 24 * 60 * 60 if remember else settings.SESSION_COOKIE_AGE)


def pending_user(request):
    pending = request.session.get("pending_auth", {})
    if time.time() - pending.get("time", 0) > 300:
        return None
    return get_user_model().objects.filter(pk=pending.get("id"), is_active=True).first()


@require_POST
@csrf_protect
def enroll(request):
    user = pending_user(request)
    if user is None or TOTPDevice.objects.filter(user=user, confirmed=True).exists():
        return JsonResponse({"detail": "Start a fresh sign-in before enrollment."}, status=403)
    with transaction.atomic():
        get_user_model().objects.select_for_update().get(pk=user.pk)
        device, _ = TOTPDevice.objects.get_or_create(user=user, name="OPSDEMO", confirmed=False)
    return JsonResponse({"configuration_url": device.config_url})


@require_POST
@csrf_protect
def verify(request):
    data = body(request)
    user = pending_user(request)
    if user is None or data is None:
        return JsonResponse({"detail": "Start a fresh sign-in."}, status=403)
    if not isinstance(data.get("token"), str) or len(data["token"]) != 6:
        return JsonResponse({"detail": "Enter the six-digit authenticator code."}, status=400)
    with transaction.atomic():
        devices = TOTPDevice.objects.select_for_update().filter(user=user)
        device = next((device for device in devices if device.verify_token(data["token"])), None)
        if device is None:
            return JsonResponse({"detail": "Invalid code or too many attempts."}, status=400)
        device.confirmed = True
        device.save(update_fields=["confirmed"])
        remember = request.session.get("pending_auth", {}).get("remember_me") is True
        login(request, user)
        otp_login(request, device)
        session_expiry(request, user, remember)
        request.session.pop("pending_auth", None)
        auth_audit(user, "authentication.mfa_verified")
    return JsonResponse(profile(user))


@require_POST
@csrf_protect
def sign_out(request):
    if request.user.is_authenticated:
        auth_audit(request.user, "authentication.signed_out")
    logout(request)
    return JsonResponse({"status": "signed_out"})


@api_view(["GET"])
def me(request):
    return Response(profile(request.user))
