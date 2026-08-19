"""FastAPI application factory."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import health, properties
from app.core.config import settings
from app.core.logging import configure_logging


def create_app() -> FastAPI:
    """Build the Knomes API application with logging and all routers configured."""
    configure_logging()
    app = FastAPI(title="Knomes API", version="0.1.0")
    # The browser calls the API cross-origin (web :3000 -> api :8000); the
    # public API is read-only GETs, so no credentials are allowed.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
        allow_methods=["GET"],
        allow_headers=["*"],
    )
    app.include_router(health.router)
    app.include_router(properties.router)
    return app


app = create_app()
