"""Alembic environment for the Knomes API.

URL resolution order: explicit ``sqlalchemy.url`` in the Alembic config (set
programmatically by tests) > ``DATABASE_URL`` environment variable >
``app.core.config.settings.database_url``.
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, pool

from alembic import context

# Make the api package importable regardless of the current working directory.
API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

import app.models  # noqa: E402,F401  (imported for side effect: registers all tables)
from app.core.db import Base  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    configured = config.get_main_option("sqlalchemy.url")
    if configured:
        return configured
    env_url = os.environ.get("DATABASE_URL")
    if env_url:
        return env_url
    from app.core.config import settings

    return settings.database_url


def include_object(
    obj: Any,
    name: str | None,
    type_: str,
    reflected: bool,
    compare_to: Any,
) -> bool:
    """Keep autogenerate away from objects Alembic does not own.

    - PostGIS-managed tables (e.g. ``spatial_ref_sys``) exist in the database
      but not in our metadata; never propose dropping them.
    - geoalchemy2's automatic spatial indexes are named ``idx_<table>_<column>``;
      we manage the GIST index explicitly, so ignore that pattern entirely.
    """
    if type_ == "table" and reflected and name is not None and name not in target_metadata.tables:
        return False
    if type_ == "index" and name is not None and name.startswith("idx_") and name.endswith("_geom"):
        return False
    return True


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL to stdout)."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (connect and execute)."""
    connectable = create_engine(_database_url(), poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()

    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
