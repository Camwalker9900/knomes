"""Migration round-trip tests: upgrade head -> downgrade base -> upgrade head."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

EXPECTED_TABLES = frozenset(
    {
        "properties",
        "property_addresses",
        "source_records",
        "source_sync_runs",
        "record_property_matches",
        "ledger_events",
        "event_relationships",
        "claims",
        "findings",
        "finding_resolutions",
        "resolution_candidates",
        "transaction_cycles",
        "evidence",
        "submissions",
        "professionals",
        "audit_log",
    }
)

EXPECTED_EXTENSIONS = frozenset({"postgis", "pg_trgm", "unaccent"})


def _table_names(db_url: str) -> set[str]:
    engine = sa.create_engine(db_url, poolclass=sa.pool.NullPool)
    try:
        return set(sa.inspect(engine).get_table_names())
    finally:
        engine.dispose()


def _extension_names(db_url: str) -> set[str]:
    engine = sa.create_engine(db_url, poolclass=sa.pool.NullPool)
    try:
        with engine.connect() as conn:
            rows = conn.execute(sa.text("SELECT extname FROM pg_extension"))
            return {row[0] for row in rows}
    finally:
        engine.dispose()


def test_upgrade_downgrade_upgrade_cycle(db_url: str, alembic_config: Config) -> None:
    command.upgrade(alembic_config, "head")
    assert EXPECTED_TABLES <= _table_names(db_url)

    command.downgrade(alembic_config, "base")
    remaining = _table_names(db_url)
    assert EXPECTED_TABLES.isdisjoint(remaining), (
        f"tables not dropped on downgrade: {sorted(EXPECTED_TABLES & remaining)}"
    )

    command.upgrade(alembic_config, "head")
    assert EXPECTED_TABLES <= _table_names(db_url)


def test_all_expected_tables_exist(db_url: str, alembic_config: Config) -> None:
    command.upgrade(alembic_config, "head")
    tables = _table_names(db_url)
    missing = EXPECTED_TABLES - tables
    assert not missing, f"missing tables after upgrade: {sorted(missing)}"
    assert "alembic_version" in tables


def test_required_extensions_installed(db_url: str, alembic_config: Config) -> None:
    command.upgrade(alembic_config, "head")
    installed = _extension_names(db_url)
    missing = EXPECTED_EXTENSIONS - installed
    assert not missing, f"missing extensions: {sorted(missing)}"
