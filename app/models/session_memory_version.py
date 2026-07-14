"""
SessionMemoryVersion — full canonical planner memory snapshots.
Each time memory is patched, a new version row is created.
Memory is append-only, never updated in place.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class SessionMemoryVersion(Base):
    __tablename__ = "session_memory_versions"
    __table_args__ = (
        UniqueConstraint("session_id", "version_no", name="uq_session_memory_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)

    # Full memory snapshot
    memory_json: Mapped[dict] = mapped_column(JSONB, nullable=False)

    # Convenience fields for quick access without parsing JSONB
    stale_sections: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list
    )
    open_questions: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    updated_by_request_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    session: Mapped["Session"] = relationship("Session", back_populates="memory_versions")

    def __repr__(self) -> str:
        return f"<SessionMemoryVersion session={self.session_id} v={self.version_no}>"
