"""Exercise real HTTP sign-in and role scoping without printing account passwords."""

import http.cookiejar
import json
from pathlib import Path
from urllib.request import HTTPCookieProcessor, Request, build_opener

base = "http://localhost:8080"
credentials = json.loads(
    (Path(__file__).resolve().parent.parent / ".trial-accounts.json").read_text()
)
opener = build_opener()
with opener.open(base + "/health/ready", timeout=15) as response:
    assert json.load(response)["status"] == "ready"
with opener.open(base, timeout=15) as response:
    assert b"<html" in response.read().lower()
for role, entry in credentials.items():
    opener = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
    with opener.open(base + "/api/v1/auth/csrf/", timeout=15) as response:
        token = json.load(response)["csrf_token"]
    request = Request(
        base + "/api/v1/auth/login/",
        data=json.dumps(entry).encode(),
        headers={
            "Content-Type": "application/json",
            "X-CSRFToken": token,
            "Origin": base,
        },
    )
    with opener.open(request, timeout=15) as response:
        profile = json.load(response)
    assert not profile.get("mfa_required", False)
    assert role in profile["roles"]
    with opener.open(base + "/api/v1/orders/", timeout=15) as response:
        orders = json.load(response)["results"]
    if role == "Courier":
        assert orders and all(order["courier"] == profile["id"] for order in orders)
    print(f"{role}: signed in; {len(orders)} permitted orders")
print("Real HTTP readiness, frontend, sign-in and courier scoping passed.")
