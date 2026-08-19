"""Shared pytest fixtures: PostgreSQL test database, migrations, sessions, API client.

The test database (``TEST_DATABASE_URL`` env var, defaulting to a local
``knomes_test``) is created on demand and migrated to head once per session.
Each test that uses the ``db`` fixture runs inside a transaction that is rolled
back afterwards. If the PostgreSQL server is unreachable, database-dependent
tests are skipped (not failed).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

API_DIR = Path(__file__).resolve().parents[1]
DEFAULT_TEST_DATABASE_URL = "postgresql+psycopg://knomes:knomes@localhost:5433/knomes_test"
POSTGRES_UNAVAILABLE = "postgres unavailable"


def _test_database_url() -> str:
    return os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL)


def _ensure_test_database(url: str) -> bool:
    """Create the test database if missing. Returns False if the server is unreachable."""
    test_url = make_url(url)
    database = test_url.database
    if database is None:
        return False
    admin_engine = create_engine(
        test_url.set(database="postgres"),
        isolation_level="AUTOCOMMIT",
        poolclass=sa.pool.NullPool,
        connect_args={"connect_timeout": 3},
    )
    try:
        with admin_engine.connect() as conn:
            exists = conn.execute(
                sa.text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": database},
            ).scalar()
            if not exists:
                quoted = database.replace('"', '""')
                conn.execute(sa.text(f'CREATE DATABASE "{quoted}"'))
        return True
    except OperationalError:
        return False
    finally:
        admin_engine.dispose()


def _make_alembic_config(url: str) -> Config:
    cfg = Config(str(API_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


@pytest.fixture(scope="session")
def db_url() -> str:
    """Test database URL; skips dependents when the PostgreSQL server is unreachable."""
    url = _test_database_url()
    if not _ensure_test_database(url):
        pytest.skip(POSTGRES_UNAVAILABLE)
    return url


@pytest.fixture(scope="session")
def alembic_config(db_url: str) -> Config:
    return _make_alembic_config(db_url)


@pytest.fixture(scope="session")
def db_engine(db_url: str, alembic_config: Config) -> Iterator[Engine]:
    """Engine bound to the test database, migrated to head."""
    command.upgrade(alembic_config, "head")
    engine = create_engine(db_url, pool_pre_ping=True)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def session_factory(db_engine: Engine) -> sessionmaker[Session]:
    """Session factory bound to the migrated test database."""
    return sessionmaker(bind=db_engine, expire_on_commit=False)


@pytest.fixture()
def db(db_engine: Engine) -> Iterator[Session]:
    """A session inside an outer transaction that is always rolled back.

    ``join_transaction_mode="create_savepoint"`` lets code under test call
    ``session.commit()`` without escaping the enclosing test transaction.
    """
    connection = db_engine.connect()
    transaction = connection.begin()
    session = Session(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture()
def client(db: Session) -> Iterator[TestClient]:
    """FastAPI TestClient whose get_session dependency yields the test session."""
    from fastapi.testclient import TestClient

    from app.core.db import get_session
    from app.main import create_app

    app = create_app()

    def _get_test_session() -> Iterator[Session]:
        yield db

    app.dependency_overrides[get_session] = _get_test_session
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()
