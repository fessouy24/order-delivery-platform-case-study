"""Generate isolated trial settings and private, randomly generated account passwords."""

import json
import secrets
from pathlib import Path

root = Path(__file__).resolve().parent.parent
env_path = root / ".env.trial"
if not env_path.exists():
    values = {
        "APP_ENV": "development",
        "TRIAL_MODE": "true",
        "DJANGO_SECRET_KEY": secrets.token_urlsafe(64),
        "DJANGO_ALLOWED_HOSTS": "localhost,127.0.0.1,api",
        "DJANGO_CSRF_TRUSTED_ORIGINS": "http://localhost:8080,http://127.0.0.1:8080",
        "POSTGRES_DB": "opsdemo_trial",
        "POSTGRES_USER": "opsdemo_trial",
        "POSTGRES_PASSWORD": secrets.token_urlsafe(32),
        "POSTGRES_HOST": "db",
        "POSTGRES_PORT": "5432",
        "REDIS_URL": "redis://redis:6379/0",
        "BUSINESS_TIMEZONE": "Africa/Cairo",
        "REQUIRE_STAFF_MFA": "false",
        "TRUST_PROXY_CLIENT_IP": "false",
    }
    with env_path.open("x", encoding="utf-8", newline="\n") as output:
        output.write("".join(f"{key}={value}\n" for key, value in values.items()))
credentials_path = root / ".trial-accounts.json"
if not credentials_path.exists():
    roles = (
        "Admin",
        "Management",
        "Sales",
        "Supply Chain",
        "Warehouse",
        "Courier",
        "Finance",
        "Production",
    )
    credentials = {
        role: {
            "username": "trial_" + role.lower().replace(" ", "_"),
            "password": secrets.token_urlsafe(18),
        }
        for role in roles
    }
    with credentials_path.open("x", encoding="utf-8") as output:
        json.dump(credentials, output, indent=2)
credentials=json.loads(credentials_path.read_text(encoding='utf-8'))
if 'Production' not in credentials:
    credentials['Production']={'username':'trial_production','password':secrets.token_urlsafe(18)}
    credentials_path.write_text(json.dumps(credentials,indent=2),encoding='utf-8')
print("Trial settings and private account file ready. No credentials printed.")
