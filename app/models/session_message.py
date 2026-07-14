"""SessionMessage — append-only transcript of planner-client exchange."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class SessionMessage(Base):
    __tablename__ = "session_messages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    sequence_no: Mapped[int] = mapped_column(
        BigInteger, nullable=False
    )  # Monotonically increasing per session

    role: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # client | planner | system
    message_type: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # conversation_turn | synthesis_request | system_recompute | error
    stage: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )  # Stage at time of message
    content_text: Mapped[str] = mapped_column(
        Text, nullable=False
    )
    source: Mapped[str | None] = mapped_column(
        String(20), nullable=True
    )  # openai | system | rule | error

    request_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )  # Correlation key

    metadata_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default="{}", default=dict
    )  # selectedChips snapshot, artifact refs, etc.

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    session: Mapped["Session"] = relationship("Session", back_populates="messages")

    def __repr__(self) -> str:
        return f"<SessionMessage seq={self.sequence_no} role={self.role} stage={self.stage}>"
