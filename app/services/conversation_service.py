from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.models.conversation import Conversation, ConversationStatus
from app.models.message import Message, MessageRole
from app.models.wedding_profile import WeddingProfile, DEFAULT_PROFILE
from app.services.llm_service import get_ai_response, extract_profile_updates
from app.services.profile_service import merge_profile, calculate_completion


class ConversationService:

    # ──────────────────────────────────────────────
    # Conversation CRUD
    # ──────────────────────────────────────────────

    @staticmethod
    async def create_conversation(db: AsyncSession, user_id: int, title: str | None = None) -> Conversation:
        conv = Conversation(user_id=user_id, title=title, status=ConversationStatus.active)
        db.add(conv)
        await db.flush()  # get conv.id before committing

        # Create a blank wedding profile for this conversation
        profile = WeddingProfile(
            conversation_id=conv.id,
            profile_json=dict(DEFAULT_PROFILE),
            completion_percentage=0.0,
        )
        db.add(profile)
        await db.flush()

        return conv

    @staticmethod
    async def get_conversation(db: AsyncSession, conversation_id: int) -> Conversation | None:
        result = await db.execute(
            select(Conversation)
            .options(selectinload(Conversation.wedding_profile))
            .where(Conversation.id == conversation_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_user_conversations(db: AsyncSession, user_id: int) -> list[Conversation]:
        result = await db.execute(
            select(Conversation)
            .where(Conversation.user_id == user_id)
            .order_by(Conversation.updated_at.desc())
        )
        return list(result.scalars().all())

    # ──────────────────────────────────────────────
    # Message History
    # ──────────────────────────────────────────────

    @staticmethod
    async def get_recent_messages(
        db: AsyncSession, conversation_id: int, limit: int = None
    ) -> list[Message]:
        limit = limit or settings.MAX_HISTORY_MESSAGES
        result = await db.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(limit)
        )
        messages = list(result.scalars().all())
        messages.reverse()  # chronological order
        return messages

    @staticmethod
    async def save_message(
        db: AsyncSession,
        conversation_id: int,
        role: MessageRole,
        content: str,
    ) -> Message:
        msg = Message(conversation_id=conversation_id, role=role, content=content)
        db.add(msg)
        await db.flush()
        return msg

    # ──────────────────────────────────────────────
    # Wedding Profile
    # ──────────────────────────────────────────────

    @staticmethod
    async def get_wedding_profile(db: AsyncSession, conversation_id: int) -> WeddingProfile | None:
        result = await db.execute(
            select(WeddingProfile).where(WeddingProfile.conversation_id == conversation_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def update_wedding_profile(
        db: AsyncSession,
        profile: WeddingProfile,
        updates: dict,
    ) -> WeddingProfile:
        if not updates:
            return profile

        merged = merge_profile(profile.profile_json, updates)
        completion = calculate_completion(merged)

        profile.profile_json = merged
        profile.completion_percentage = completion

        # SQLAlchemy won't detect JSONB mutation automatically — flag it dirty
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(profile, "profile_json")

        db.add(profile)
        await db.flush()
        return profile

    # ──────────────────────────────────────────────
    # Main Chat Pipeline
    # ──────────────────────────────────────────────

    @classmethod
    async def process_chat(
        cls,
        db: AsyncSession,
        conversation_id: int,
        user_message: str,
    ) -> dict:
        """
        Full pipeline:
        1. Save user message
        2. Load history + profile
        3. Get AI response
        4. Save assistant message
        5. Extract + merge profile updates
        6. Return response payload
        """
        # 1. Save user message
        await ConversationService.save_message(db, conversation_id, MessageRole.user, user_message)

        # 2. Load recent history (excluding the message we just saved — we'll add it manually below)
        history_rows = await ConversationService.get_recent_messages(
            db, conversation_id, limit=settings.MAX_HISTORY_MESSAGES + 1
        )
        # Build Ollama-format history (exclude the very last user message — it's the current one)
        history = [
            {"role": msg.role.value, "content": msg.content}
            for msg in history_rows[:-1]   # exclude the just-saved user message
        ]

        # 3. Load wedding profile
        profile = await ConversationService.get_wedding_profile(db, conversation_id)
        profile_json = profile.profile_json if profile else {}

        # 4. Get AI response
        assistant_text = await get_ai_response(user_message, history, profile_json)

        # 5. Save assistant message
        await ConversationService.save_message(
            db, conversation_id, MessageRole.assistant, assistant_text
        )

        # 6. Extract profile updates (separate LLM call)
        updates = await extract_profile_updates(user_message, assistant_text)

        # 7. Merge into profile
        if profile and updates:
            profile = await ConversationService.update_wedding_profile(db, profile, updates)

        return {
            "response": assistant_text,
            "profile_updates": updates,
            "completion_percentage": profile.completion_percentage if profile else 0.0,
        }
