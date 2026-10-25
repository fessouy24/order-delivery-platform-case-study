"""Fail closed for missing production secrets; no implicit SQLite fallback."""

import os
from urllib.parse import parse_qs, unquote, urlsplit


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value or value.startswith("replace-with-"):
        raise ValueError(f"Configure {name} with a real value in the environment")
    return value


def csv(name: str, default: str = "") -> list[str]:
    return [value.strip() for value in os.environ.get(name, default).split(",") if value.strip()]


def application_environment() -> str:
    value = os.environ.get("APP_ENV", "development")
    if value not in {"development", "staging", "production"}:
        raise ValueError("APP_ENV must be development, staging or production")
    return value


def postgres_database() -> dict:
    """Accept managed Postgres URLs without ever falling back to another database."""
    url = os.environ.get("DATABASE_URL", "").strip()
    options = {"connect_timeout": 5}
    if url:
        parsed = urlsplit(url)
        if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname:
            raise ValueError("DATABASE_URL must identify a PostgreSQL server")
        name = unquote(parsed.path.lstrip("/"))
        if not name or not parsed.username or not parsed.password:
            raise ValueError("DATABASE_URL requires database, username and password")
        sslmode = parse_qs(parsed.query).get("sslmode", [None])[0]
        if sslmode:
            if sslmode not in {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}:
                raise ValueError("Invalid PostgreSQL sslmode")
            options["sslmode"] = sslmode
        values = {
            "NAME": name,
            "USER": unquote(parsed.username),
            "PASSWORD": unquote(parsed.password),
            "HOST": parsed.hostname,
            "PORT": str(parsed.port or 5432),
        }
    else:
        values = {
            "NAME": required("POSTGRES_DB"),
            "USER": required("POSTGRES_USER"),
            "PASSWORD": required("POSTGRES_PASSWORD"),
            "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
            "PORT": os.environ.get("POSTGRES_PORT", "5432"),
        }
    return {
        "ENGINE": "django.db.backends.postgresql",
        **values,
        "CONN_MAX_AGE": 60,
        "CONN_HEALTH_CHECKS": True,
        "OPTIONS": options,
    }
