import pytest

from config.environment import postgres_database


def test_managed_database_url_preserves_encoded_credentials_and_tls(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql://ops:p%40ss%3Aword@db.internal:5433/opsdemo?sslmode=require"
    )
    database = postgres_database()
    assert database["ENGINE"] == "django.db.backends.postgresql"
    assert database["PASSWORD"] == "p@ss:word"
    assert database["HOST"] == "db.internal"
    assert database["PORT"] == "5433"
    assert database["NAME"] == "opsdemo"
    assert database["OPTIONS"]["sslmode"] == "require"


@pytest.mark.parametrize(
    "url",
    ["sqlite:///data.db", "postgresql://db.internal/app", "postgresql://ops:pass@db/"],
)
def test_invalid_cloud_database_configuration_fails_closed(monkeypatch, url):
    monkeypatch.setenv("DATABASE_URL", url)
    with pytest.raises(ValueError):
        postgres_database()


def test_existing_compose_configuration_still_works(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    for key, value in {
        "POSTGRES_DB": "opsdemo",
        "POSTGRES_USER": "ops",
        "POSTGRES_PASSWORD": "test-only",
        "POSTGRES_HOST": "db",
    }.items():
        monkeypatch.setenv(key, value)
    assert postgres_database()["HOST"] == "db"
