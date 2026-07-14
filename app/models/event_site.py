"""
EventSite — curated design direction profiles for identity and direction phases.
Not physical venues — these are aesthetic/identity archetypes used by the planner
to shape suggestions. ~15 active records at launch.
"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

try:
    from pgvector.sqlalchemy import Vector
    _HAS_PGVECTOR = True
except ImportError:
    _HAS_PGVECTOR = False


class EventSite(Base):
    __tablename__ = "event_sites"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    site_type: Mapped[str] = mapped_column(
        String(50), nullable=False, index=True
    )  # e.g. regal, minimal, tropical, bohemian, heritage
    short_description: Mapped[str] = mapped_column(Text, nullable=False)

    # Rich nested profile — style tags, persona signals, vibe, cultural signals etc.
    profile_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # Media
    hero_image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    gallery_json: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    # Semantic embedding for similarity search (768-dim for nomic-embed-text)
    # Stored as vector type if pgvector is available
    if _HAS_PGVECTOR:
        embedding: Mapped[list[float] | None] = mapped_column(Vector(768), nullable=True)
    else:
        embedding = None  # type: ignore[assignment]

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    seed_version: Mapped[str | None] = mapped_column(String(20), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    recommendations: Mapped[list["SessionEventSiteRecommendation"]] = relationship(
        "SessionEventSiteRecommendation", back_populates="event_site"
    )

    def __repr__(self) -> str:
        return f"<EventSite slug={self.slug} type={self.site_type}>"
