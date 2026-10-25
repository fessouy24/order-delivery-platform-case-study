from unittest.mock import patch


def test_liveness_does_not_require_dependencies(client):
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_readiness_fails_closed_without_exposing_secrets(client):
    with patch("apps.core.views.connection.cursor", side_effect=RuntimeError("SECRET")):
        response = client.get("/health/ready")
    assert response.status_code == 503
    assert "SECRET" not in response.content.decode()
