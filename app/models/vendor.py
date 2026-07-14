"""Vendor — curated vendor catalog for logistics recommendations."""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Vendor(Base):
    __tablename__ = "vendors"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    vendor_type: Mapped[str] = mapped_column(
        String(50), nullable=False, index=True
    )  # venue | decor | catering | photography | entertainment | planner | multi_service
    primary_city: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    primary_region: Mapped[str] = mapped_column(String(100), nullable=False)
    short_description: Mapped[str] = mapped_column(Text, nullable=False)

    # Rich nested profile (contact, services, portfolio, availability, businessMeta)
    profile_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # Review aggregates
    rating_summary_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    is_preferred: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    seed_version: Mapped[str | None] = mapped_column(String(20), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    recommendations: Mapped[list["SessionVendorRecommendation"]] = relationship(
        "SessionVendorRecommendation", back_populates="vendor"
    )

    def __repr__(self) -> str:
        return f"<Vendor slug={self.slug} type={self.vendor_type} city={self.primary_city}>"
