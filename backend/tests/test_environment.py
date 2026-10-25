import pytest

from config.environment import application_environment, required


@pytest.mark.parametrize("value", ["", "replace-with-your-secret"])
def test_missing_secret_is_rejected(monkeypatch, value):
    monkeypatch.setenv("EXAMPLE_SECRET", value)
    with pytest.raises(ValueError, match="EXAMPLE_SECRET"):
        required("EXAMPLE_SECRET")


def test_unknown_environment_cannot_enable_unsafe_defaults(monkeypatch):
    monkeypatch.setenv("APP_ENV", "prodution")
    with pytest.raises(ValueError):
        application_environment()
