"""
Session model — root aggregate for one couple's planning journey.
One session = one couple's full planning conversation.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.domain.enums import StageId, SessionStatus


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Session(Base):
    __tablename__ = "sessions"

    # Primary key
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # Optional external-safe id for URLs
    public_id: Mapped[str | None] = mapped_column(Text, unique=True, nullable=True)

    # Planning state
    current_stage: Mapped[str] = mapped_column(
        String(50), nullable=False, default=StageId.S1_NAMES.value
    )
    memory_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=SessionStatus.ACTIVE.value
    )

    # Convenience projections from identity memory
    groom_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    bride_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    occasion_type: Mapped[str] = mapped_column(
        String(50), nullable=False, default="wedding"
    )

    # AI tracing
    last_provider_response_id: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )

    # Timestamps
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
        onupdate=func.now(), nullable=False
    )

    # Relationships
    messages: Mapped[list["SessionMessage"]] = relationship(
        "SessionMessage", back_populates="session", cascade="all, delete-orphan"
    )
    stage_history: Mapped[list["SessionStageHistory"]] = relationship(
        "SessionStageHistory", back_populates="session", cascade="all, delete-orphan"
    )
    memory_versions: Mapped[list["SessionMemoryVersion"]] = relationship(
        "SessionMemoryVersion", back_populates="session", cascade="all, delete-orphan"
    )
    memory_patches: Mapped[list["SessionMemoryPatch"]] = relationship(
        "SessionMemoryPatch", back_populates="session", cascade="all, delete-orphan"
    )
    generated_artifacts: Mapped[list["GeneratedArtifact"]] = relationship(
        "GeneratedArtifact", back_populates="session", cascade="all, delete-orphan"
    )
    event_site_recommendations: Mapped[list["SessionEventSiteRecommendation"]] = relationship(
        "SessionEventSiteRecommendation", back_populates="session", cascade="all, delete-orphan"
    )
    vendor_recommendations: Mapped[list["SessionVendorRecommendation"]] = relationship(
        "SessionVendorRecommendation", back_populates="session", cascade="all, delete-orphan"
    )
    ai_turn_logs: Mapped[list["AiTurnLog"]] = relationship(
        "AiTurnLog", back_populates="session", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Session id={self.id} stage={self.current_stage} status={self.status}>"


# Avoid circular imports — imported here so SQLAlchemy sees all relationships
from app.models.session_message import SessionMessage  # noqa: E402, F401
from app.models.session_stage_history import SessionStageHistory  # noqa: E402, F401
from app.models.session_memory_version import SessionMemoryVersion  # noqa: E402, F401
from app.models.session_memory_patch import SessionMemoryPatch  # noqa: E402, F401
from app.models.generated_artifact import GeneratedArtifact  # noqa: E402, F401
from app.models.session_event_site_recommendation import SessionEventSiteRecommendation  # noqa: E402, F401
from app.models.session_vendor_recommendation import SessionVendorRecommendation  # noqa: E402, F401
from app.models.ai_turn_log import AiTurnLog  # noqa: E402, F401
