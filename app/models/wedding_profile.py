from datetime import datetime
from sqlalchemy import ForeignKey, DateTime, Float, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database.database import Base


DEFAULT_PROFILE: dict = {
    "couple": {
        "bride": None,
        "groom": None
    },
    "city": None,
    "venue": None,
    "wedding_date": None,
    "guest_count": None,
    "budget": None,
    "preferred_colors": [],
    "style": None,
    "events": [],
    "catering_preference": None,
    "photography_preference": None,
    "music_preference": None,
    "decor_theme": None,
    "cultural_traditions": [],
    "special_requirements": None,
}

# Fields considered required for completion calculation
REQUIRED_FIELDS = [
    "couple.bride",
    "couple.groom",
    "city",
    "venue",
    "wedding_date",
    "guest_count",
    "budget",
    "style",
    "catering_preference",
    "photography_preference",
    "music_preference",
    "decor_theme",
]


class WeddingProfile(Base):
    __tablename__ = "wedding_profiles"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), unique=True, index=True
    )
    profile_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=DEFAULT_PROFILE)
    completion_percentage: Mapped[float] = mapped_column(Float, default=0.0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    conversation: Mapped["Conversation"] = relationship(  # noqa: F821
        "Conversation", back_populates="wedding_profile"
    )

    def __repr__(self) -> str:
        return (
            f"<WeddingProfile id={self.id} "
            f"conversation_id={self.conversation_id} "
            f"completion={self.completion_percentage:.1f}%>"
        )
