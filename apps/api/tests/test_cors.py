"""The web app calls the API from the browser (localhost:3000 -> localhost:8000),
so responses must carry CORS headers for the configured web origin — without
them the search box's client-side fetch is blocked and the UI degrades."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app

WEB_ORIGIN = "http://localhost:3000"


def _client() -> TestClient:
    return TestClient(create_app())


def test_allowed_origin_gets_cors_header() -> None:
    response = _client().get("/health", headers={"Origin": WEB_ORIGIN})
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == WEB_ORIGIN


def test_preflight_allows_get_from_web_origin() -> None:
    response = _client().options(
        "/api/v1/properties/search",
        headers={
            "Origin": WEB_ORIGIN,
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == WEB_ORIGIN
    assert "GET" in response.headers.get("access-control-allow-methods", "")


def test_unknown_origin_gets_no_cors_header() -> None:
    response = _client().get("/health", headers={"Origin": "https://evil.example"})
    assert response.headers.get("access-control-allow-origin") is None
