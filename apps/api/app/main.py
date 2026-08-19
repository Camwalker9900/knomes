"""FastAPI application factory."""

from fastapi import FastAPI

from app.api.v1 import health, properties
from app.core.logging import configure_logging


def create_app() -> FastAPI:
    """Build the Knomes API application with logging and all routers configured."""
    configure_logging()
    app = FastAPI(title="Knomes API", version="0.1.0")
    app.include_router(health.router)
    app.include_router(properties.router)
    return app


app = create_app()
