"""building details: bedrooms, baths, quality, remodel year on properties

Populated by the HCAD Real_building_land import
(``python -m app.ingestion.hcad.building``): quality_code / year_remodeled
come from building_res.txt (columns ``qa_cd`` / ``yr_remodel``); bedrooms /
bathrooms_full / bathrooms_half come from fixtures.txt room rows
(``type`` = RMB / RMF / RMH). All columns are nullable — HCAD data is often
missing and absence must never be manufactured into a value.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS: tuple[sa.Column[object], ...] = (
    sa.Column("bedrooms", sa.Integer(), nullable=True),
    sa.Column("bathrooms_full", sa.Integer(), nullable=True),
    sa.Column("bathrooms_half", sa.Integer(), nullable=True),
    sa.Column("quality_code", sa.Text(), nullable=True),
    sa.Column("year_remodeled", sa.Integer(), nullable=True),
)


def upgrade() -> None:
    for column in _COLUMNS:
        op.add_column("properties", column)


def downgrade() -> None:
    for column in reversed(_COLUMNS):
        op.drop_column("properties", column.name)
