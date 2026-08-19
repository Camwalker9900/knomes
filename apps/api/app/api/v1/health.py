"""Health endpoint: best-effort component checks with short timeouts, always HTTP 200."""

import httpx
import redis
from fastapi import APIRouter
from sqlalchemy import text

from app.core.config import settings
from app.core.db import engine

router = APIRouter(tags=["health"])

_OK = "ok"
_UNAVAILABLE = "unavailable"


def _check_database() -> str:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception:
        return _UNAVAILABLE
    return _OK


def _check_redis() -> str:
    try:
        client = redis.Redis.from_url(
            settings.redis_url, socket_connect_timeout=1.0, socket_timeout=1.0
        )
        try:
            client.ping()
        finally:
            client.close()
    except Exception:
        return _UNAVAILABLE
    return _OK


def _check_storage() -> str:
    try:
        response = httpx.get(f"{settings.s3_endpoint}/minio/health/live", timeout=1.0)
    except Exception:
        return _UNAVAILABLE
    return _OK if response.status_code < 400 else _UNAVAILABLE


@router.get("/health")
def health() -> dict[str, str]:
    """Overall status is "ok" only when the database is reachable; otherwise "degraded"."""
    database = _check_database()
    return {
        "status": _OK if database == _OK else "degraded",
        "database": database,
        "redis": _check_redis(),
        "storage": _check_storage(),
    }
