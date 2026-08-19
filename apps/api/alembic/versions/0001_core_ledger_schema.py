"""core ledger schema

Revision ID: 0001
Revises:
Create Date: 2026-08-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from geoalchemy2.types import Geometry
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES_IN_DROP_ORDER = (
    "audit_log",
    "event_relationships",
    "record_property_matches",
    "transaction_cycles",
    "resolution_candidates",
    "finding_resolutions",
    "findings",
    "claims",
    "ledger_events",
    "evidence",
    "professionals",
    "submissions",
    "source_sync_runs",
    "source_records",
    "property_addresses",
    "properties",
)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")

    _create_properties()
    _create_property_addresses()
    _create_source_records()
    _create_source_sync_runs()
    _create_submissions()
    _create_professionals()
    _create_evidence()
    _create_ledger_events()
    _create_claims()
    _create_findings()
    _create_finding_resolutions()
    _create_resolution_candidates()
    _create_transaction_cycles()
    _create_record_property_matches()
    _create_event_relationships()
    _create_audit_log()


def downgrade() -> None:
    # Extensions are intentionally left installed.
    for table in _TABLES_IN_DROP_ORDER:
        op.drop_table(table)


def _create_properties() -> None:
    op.create_table(
        "properties",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("hcad_account_id", sa.Text(), nullable=True),
        sa.Column("address_line1", sa.Text(), nullable=False),
        sa.Column("unit", sa.Text(), nullable=True),
        sa.Column("city", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("postal_code", sa.Text(), nullable=False),
        sa.Column("normalized_address", sa.Text(), nullable=False),
        sa.Column("address_hash", sa.Text(), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column(
            "parcel_geometry",
            Geometry(geometry_type="MULTIPOLYGON", srid=4326, spatial_index=False),
            nullable=True,
        ),
        sa.Column("year_built", sa.Integer(), nullable=True),
        sa.Column("building_sqft", sa.Integer(), nullable=True),
        sa.Column("lot_sqft", sa.Float(), nullable=True),
        sa.Column("property_type", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_properties"),
        sa.UniqueConstraint("hcad_account_id", name="uq_properties_hcad_account_id"),
    )
    op.create_index("ix_properties_address_hash", "properties", ["address_hash"])
    op.create_index(
        "ix_properties_normalized_address_trgm",
        "properties",
        ["normalized_address"],
        postgresql_using="gin",
        postgresql_ops={"normalized_address": "gin_trgm_ops"},
    )
    op.create_index(
        "ix_properties_parcel_geometry",
        "properties",
        ["parcel_geometry"],
        postgresql_using="gist",
    )


def _create_property_addresses() -> None:
    op.create_table(
        "property_addresses",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("property_id", sa.Uuid(), nullable=False),
        sa.Column("raw_address", sa.Text(), nullable=False),
        sa.Column("normalized_address", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_current", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_property_addresses"),
        sa.ForeignKeyConstraint(
            ["property_id"], ["properties.id"], name="fk_property_addresses_property_id"
        ),
    )
    op.create_index("ix_property_addresses_property_id", "property_addresses", ["property_id"])
    op.create_index(
        "ix_property_addresses_normalized_address_trgm",
        "property_addresses",
        ["normalized_address"],
        postgresql_using="gin",
        postgresql_ops={"normalized_address": "gin_trgm_ops"},
    )


def _create_source_records() -> None:
    op.create_table(
        "source_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_name", sa.Text(), nullable=False),
        sa.Column("source_record_id", sa.Text(), nullable=False),
        sa.Column("property_id", sa.Uuid(), nullable=True),
        sa.Column("record_type", sa.Text(), nullable=False),
        sa.Column("raw_payload", JSONB(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("source_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "retrieved_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("parser_version", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_source_records"),
        sa.ForeignKeyConstraint(
            ["property_id"], ["properties.id"], name="fk_source_records_property_id"
        ),
        sa.UniqueConstraint(
            "source_name",
            "source_record_id",
            "content_hash",
            name="uq_source_records_source_record_content",
        ),
    )
    op.create_index(
        "ix_source_records_source_name_retrieved_at",
        "source_records",
        ["source_name", "retrieved_at"],
    )
    op.create_index("ix_source_records_property_id", "source_records", ["property_id"])


def _create_source_sync_runs() -> None:
    op.create_table(
        "source_sync_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_name", sa.Text(), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.Text(), server_default=sa.text("'RUNNING'"), nullable=False),
        sa.Column("snapshot_path", sa.Text(), nullable=True),
        sa.Column("snapshot_checksum", sa.Text(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("parser_version", sa.Text(), nullable=True),
        sa.Column("records_parsed", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("records_matched", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("records_unmatched", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("records_rejected", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "source_records_created", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("events_created", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_source_sync_runs"),
    )
    op.create_index(
        "ix_source_sync_runs_source_name_started_at",
        "source_sync_runs",
        ["source_name", "started_at"],
    )


def _create_submissions() -> None:
    op.create_table(
        "submissions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("property_id", sa.Uuid(), nullable=False),
        sa.Column("submitted_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("submitter_role", sa.Text(), nullable=False),
        sa.Column("transaction_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submission_type", sa.Text(), nullable=False),
        sa.Column("statement", sa.Text(), nullable=True),
        sa.Column(
            "status", sa.Text(), server_default=sa.text("'PENDING_REVIEW'"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by", sa.Uuid(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_submissions"),
        sa.ForeignKeyConstraint(
            ["property_id"], ["properties.id"], name="fk_submissions_property_id"
        ),
    )
    op.create_index("ix_submissions_property_id", "submissions", ["property_id"])


def _create_professionals() -> None:
    op.create_table(
        "professionals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("company", sa.Text(), nullable=True),
        sa.Column("profession_type", sa.Text(), nullable=False),
        sa.Column("license_number", sa.Text(), nullable=True),
        sa.Column("license_state", sa.Text(), nullable=True),
        sa.Column(
            "verification_status",
            sa.Text(),
            server_default=sa.text("'UNVERIFIED'"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_professionals"),
    )


def _create_evidence() -> None:
    op.create_table(
        "evidence",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("property_id", sa.Uuid(), nullable=False),
        sa.Column("submission_id", sa.Uuid(), nullable=True),
        sa.Column("evidence_type", sa.Text(), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("original_filename", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.Text(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("document_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("issuer_name", sa.Text(), nullable=True),
        sa.Column("issuer_license", sa.Text(), nullable=True),
        sa.Column("extracted_text_key", sa.Text(), nullable=True),
        sa.Column(
            "review_status", sa.Text(), server_default=sa.text("'PENDING_REVIEW'"), nullable=False
        ),
        sa.Column("visibility", sa.Text(), server_default=sa.text("'PRIVATE'"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_evidence"),
        sa.ForeignKeyConstraint(
            ["property_id"], ["properties.id"], name="fk_evidence_property_id"
        ),
        sa.ForeignKeyConstraint(
            ["submission_id"], ["submissions.id"], name="fk_evidence_submission_id"
        ),
    )
    op.create_index("ix_evidence_property_id", "evidence", ["property_id"])
    op.create_index("ix_evidence_submission_id", "evidence", ["submission_id"])


def _create_ledger_events() -> None:
    op.create_table(
        "ledger_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("property_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("event_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("source_record_id", sa.Uuid(), nullable=True),
        sa.Column("submission_id", sa.Uuid(), nullable=True),
        sa.Column("verification_level", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("visibility", sa.Text(), server_default=sa.text("'PUBLIC'"), nullable=False),
        sa.Column("retracted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retraction_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ledger_events"),
        sa.ForeignKeyConstraint(
            ["property_id"], ["properties.id"], name="fk_ledger_events_property_id"
        ),
        sa.ForeignKeyConstraint(
            ["source_record_id"],
            ["source_records.id"],
            name="fk_ledger_events_source_record_id",
        ),
        sa.ForeignKeyConstraint(
            ["submission_id"], ["submissions.id"], name="fk_ledger_events_submission_id"
        ),
    )
    op.create_index(
        "ix_ledger_events_property_id_event_date",
        "ledger_events",
        ["property_id", "event_date"],
    )
    op.create_index("ix_ledger_events_source_record_id", "ledger_events", ["source_record_id"])
    op.create_index(
        "uq_ledger_dedup",
        "ledger_events",
        ["property_id", "event_type", "event_date", "source_record_id"],
        unique=True,
        postgresql_where=sa.text("source_record_id IS NOT NULL"),
    )


def _create_claims() -> None:
    op.create_table(
        "claims",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("property_id", sa.Uuid(), nullable=False),
        sa.Column("submission_id", sa.Uuid(), nullable=True),
        sa.Column("claimant_type", sa.Text(), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("claim_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "review_status", sa.Text(), server_default=sa.text("'PENDING_REVIEW'"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_claims"),
        sa.ForeignKeyConstraint(["property_id"], ["properties.id"], name="fk_claims_property_id"),
        sa.ForeignKeyConstraint(
            ["submission_id"], ["submissions.id"], name="fk_claims_submission_id"
        ),
    )
    op.create_index("ix_claims_property_id", "claims", ["property_id"])


def _create_findings() -> None:
    op.create_table(
        "findings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("property_id", sa.Uuid(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("subcategory", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("severity", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), server_default=sa.text("'OPEN'"), nullable=False),
        sa.Column("first_observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("latest_observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_from_claim_id", sa.Uuid(), nullable=True),
        sa.Column("created_from_event_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_findings"),
        sa.ForeignKeyConstraint(
            ["property_id"], ["properties.id"], name="fk_findings_property_id"
        ),
        sa.ForeignKeyConstraint(
            ["created_from_claim_id"], ["claims.id"], name="fk_findings_created_from_claim_id"
        ),
        sa.ForeignKeyConstraint(
            ["created_from_event_id"],
            ["ledger_events.id"],
            name="fk_findings_created_from_event_id",
        ),
    )
    op.create_index("ix_findings_property_id_status", "findings", ["property_id", "status"])


def _create_finding_resolutions() -> None:
    op.create_table(
        "finding_resolutions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("finding_id", sa.Uuid(), nullable=False),
        sa.Column("resolution_type", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("verification_level", sa.Text(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("evidence_id", sa.Uuid(), nullable=True),
        sa.Column("event_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_finding_resolutions"),
        sa.ForeignKeyConstraint(
            ["finding_id"], ["findings.id"], name="fk_finding_resolutions_finding_id"
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id"], ["evidence.id"], name="fk_finding_resolutions_evidence_id"
        ),
        sa.ForeignKeyConstraint(
            ["event_id"], ["ledger_events.id"], name="fk_finding_resolutions_event_id"
        ),
    )
    op.create_index("ix_finding_resolutions_finding_id", "finding_resolutions", ["finding_id"])


def _create_resolution_candidates() -> None:
    op.create_table(
        "resolution_candidates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("finding_id", sa.Uuid(), nullable=False),
        sa.Column("candidate_event_id", sa.Uuid(), nullable=False),
        sa.Column("match_score", sa.Float(), nullable=False),
        sa.Column(
            "status", sa.Text(), server_default=sa.text("'REVIEW_REQUIRED'"), nullable=False
        ),
        sa.Column("rule", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_resolution_candidates"),
        sa.ForeignKeyConstraint(
            ["finding_id"], ["findings.id"], name="fk_resolution_candidates_finding_id"
        ),
        sa.ForeignKeyConstraint(
            ["candidate_event_id"],
            ["ledger_events.id"],
            name="fk_resolution_candidates_candidate_event_id",
        ),
    )
    op.create_index(
        "ix_resolution_candidates_finding_id", "resolution_candidates", ["finding_id"]
    )
    op.create_index(
        "ix_resolution_candidates_candidate_event_id",
        "resolution_candidates",
        ["candidate_event_id"],
    )


def _create_transaction_cycles() -> None:
    op.create_table(
        "transaction_cycles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("property_id", sa.Uuid(), nullable=False),
        sa.Column("listing_id", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("under_contract_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("terminated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.Text(), server_default=sa.text("'UNKNOWN'"), nullable=False),
        sa.Column(
            "termination_reason", sa.Text(), server_default=sa.text("'UNKNOWN'"), nullable=False
        ),
        sa.Column(
            "reason_verification_level",
            sa.Text(),
            server_default=sa.text("'UNVERIFIED'"),
            nullable=False,
        ),
        sa.Column("evidence_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_transaction_cycles"),
        sa.ForeignKeyConstraint(
            ["property_id"], ["properties.id"], name="fk_transaction_cycles_property_id"
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id"], ["evidence.id"], name="fk_transaction_cycles_evidence_id"
        ),
    )
    op.create_index("ix_transaction_cycles_property_id", "transaction_cycles", ["property_id"])


def _create_record_property_matches() -> None:
    op.create_table(
        "record_property_matches",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_record_id", sa.Uuid(), nullable=False),
        sa.Column("property_id", sa.Uuid(), nullable=True),
        sa.Column("match_method", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("review_status", sa.Text(), nullable=False),
        sa.Column("match_reason", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_record_property_matches"),
        sa.ForeignKeyConstraint(
            ["source_record_id"],
            ["source_records.id"],
            name="fk_record_property_matches_source_record_id",
        ),
        sa.ForeignKeyConstraint(
            ["property_id"], ["properties.id"], name="fk_record_property_matches_property_id"
        ),
    )
    op.create_index(
        "ix_record_property_matches_source_record_id",
        "record_property_matches",
        ["source_record_id"],
    )
    op.create_index(
        "ix_record_property_matches_property_id", "record_property_matches", ["property_id"]
    )
    op.create_index(
        "ix_record_property_matches_review_status", "record_property_matches", ["review_status"]
    )


def _create_event_relationships() -> None:
    op.create_table(
        "event_relationships",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("from_event_id", sa.Uuid(), nullable=False),
        sa.Column("to_event_id", sa.Uuid(), nullable=False),
        sa.Column("relationship_type", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_event_relationships"),
        sa.ForeignKeyConstraint(
            ["from_event_id"], ["ledger_events.id"], name="fk_event_relationships_from_event_id"
        ),
        sa.ForeignKeyConstraint(
            ["to_event_id"], ["ledger_events.id"], name="fk_event_relationships_to_event_id"
        ),
        sa.UniqueConstraint(
            "from_event_id",
            "to_event_id",
            "relationship_type",
            name="uq_event_relationships_from_to_type",
        ),
    )
    op.create_index(
        "ix_event_relationships_from_event_id", "event_relationships", ["from_event_id"]
    )
    op.create_index("ix_event_relationships_to_event_id", "event_relationships", ["to_event_id"])


def _create_audit_log() -> None:
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("actor_type", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("entity_type", sa.Text(), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("previous_value", JSONB(), nullable=True),
        sa.Column("new_value", JSONB(), nullable=True),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_audit_log"),
    )
    op.create_index("ix_audit_log_entity", "audit_log", ["entity_type", "entity_id"])
