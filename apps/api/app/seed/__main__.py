"""Seed the configured database with the synthetic demo dataset: ``python -m app.seed``."""

import logging

from app.core.db import SessionLocal
from app.core.logging import configure_logging
from app.seed.synthetic import seed

logger = logging.getLogger("app.seed")


def main() -> None:
    """Run the idempotent synthetic seed against the configured DATABASE_URL."""
    configure_logging()
    with SessionLocal() as session:
        seed(session)
    logger.info(
        "synthetic seed complete",
        extra={"source_name": "synthetic_fixture", "properties": 3},
    )


if __name__ == "__main__":
    main()
