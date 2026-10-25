"""Generate a fresh server environment; refuse to replace existing credentials."""

import argparse
import os
import re
import secrets
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--host", required=True)
parser.add_argument("--email", required=True)
args = parser.parse_args()
if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", args.host) or "." not in args.host:
    parser.error("Supply the actual DNS hostname")
if not re.fullmatch(r"[^\s@=]+@[^\s@=]+\.[^\s@=]+", args.email):
    parser.error("Supply a certificate contact email")
path = Path(__file__).resolve().parent.parent / ".env.production"
values = {
    "APP_ENV": "production",
    "TRIAL_MODE": "false",
    "SHOPIFY_FULFILLMENT_ENABLED": "false",
    "ODOO_COMPLETION_ENABLED": "false",
    "ODOO_MARKET_IMPORT_ENABLED": "false",
    "SHOPIFY_QUOTATION_BATCH_ENABLED": "false",
    "DJANGO_SECRET_KEY": secrets.token_urlsafe(64),
    "DJANGO_ALLOWED_HOSTS": f"{args.host},localhost,127.0.0.1,api",
    "DJANGO_CSRF_TRUSTED_ORIGINS": f"https://{args.host}",
    "POSTGRES_DB": "opsdemo",
    "POSTGRES_USER": "opsdemo",
    "POSTGRES_PASSWORD": secrets.token_urlsafe(32),
    "POSTGRES_HOST": "db",
    "POSTGRES_PORT": "5432",
    "REDIS_URL": "redis://redis:6379/0",
    "BUSINESS_TIMEZONE": "Africa/Cairo",
    "REQUIRE_STAFF_MFA": "false",
    "TRUST_PROXY_CLIENT_IP": "true",
    "PUBLIC_HOST": args.host,
    "ACME_EMAIL": args.email,
}
descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
    output.write("".join(f"{key}={value}\n" for key, value in values.items()))
print("Production environment created. Secrets were not printed.")
