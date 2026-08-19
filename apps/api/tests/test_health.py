"""GET /health contract: HTTP 200 always, component statuses, overall reflects DB."""

from fastapi.testclient import TestClient

from app.main import create_app


def test_health_shape_and_semantics() -> None:
    client = TestClient(create_app())
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()

    assert set(body) == {"status", "database", "redis", "storage"}
    assert body["status"] in {"ok", "degraded"}
    for component in ("database", "redis", "storage"):
        assert body[component] in {"ok", "unavailable"}

    # Overall status is "ok" exactly when the database is reachable.
    expected = "ok" if body["database"] == "ok" else "degraded"
    assert body["status"] == expected
