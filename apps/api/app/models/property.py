"""Property aggregate: canonical properties and their address aliases.

Identity hierarchy (never street address as permanent ID):
HCAD account ID -> parcel geometry -> normalized address.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from geoalchemy2 import Geometry
from geoalchemy2.elements import WKBElement
from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Property(Base):
    """Canonical Harris County property record."""

    __tablename__ = "properties"
    __table_args__ = (
        UniqueConstraint("hcad_account_id", name="uq_properties_hcad_account_id"),
        Index("ix_properties_address_hash", "address_hash"),
        Index(
            "ix_properties_normalized_address_trgm",
            "normalized_address",
            postgresql_using="gin",
            postgresql_ops={"normalized_address": "gin_trgm_ops"},
        ),
        Index("ix_properties_parcel_geometry", "parcel_geometry", postgresql_using="gist"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    hcad_account_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    address_line1: Mapped[str] = mapped_column(Text, nullable=False)
    unit: Mapped[str | None] = mapped_column(Text, nullable=True)
    city: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    postal_code: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_address: Mapped[str] = mapped_column(Text, nullable=False)
    address_hash: Mapped[str] = mapped_column(Text, nullable=False)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    parcel_geometry: Mapped[WKBElement | None] = mapped_column(
        Geometry(geometry_type="MULTIPOLYGON", srid=4326, spatial_index=False),
        nullable=True,
    )
    year_built: Mapped[int | None] = mapped_column(Integer, nullable=True)
    building_sqft: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lot_sqft: Mapped[float | None] = mapped_column(Float, nullable=True)
    property_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Building details from the HCAD Real_building_land import (migration 0002):
    # room counts from fixtures.txt (RMB/RMF/RMH), quality/remodel year from
    # building_res.txt (qa_cd/yr_remodel). Nullable — absence is never invented.
    bedrooms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bathrooms_full: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bathrooms_half: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quality_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    year_remodeled: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class PropertyAddress(Base):
    """Alias table: every raw/normalized address variant ever seen for a property."""

    __tablename__ = "property_addresses"
    __table_args__ = (
        Index("ix_property_addresses_property_id", "property_id"),
        Index(
            "ix_property_addresses_normalized_address_trgm",
            "normalized_address",
            postgresql_using="gin",
            postgresql_ops={"normalized_address": "gin_trgm_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    property_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("properties.id", name="fk_property_addresses_property_id"), nullable=False
    )
    raw_address: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_address: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_current: Mapped[bool] = mapped_column(
        Boolean, server_default=text("true"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
