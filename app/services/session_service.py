"""
Session Service — create, load, and manage planning sessions.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import MessageRole, MessageType, ResponseSource, SessionStatus, StageDecisionType, StageId
from app.domain.memory_schema import fresh_memory
from app.models.session import Session
from app.models.session_memory_version import SessionMemoryVersion
from app.models.session_message import SessionMessage
from app.models.session_stage_history import SessionStageHistory


class SessionService:

    @staticmethod
    async def create_session(
        db: AsyncSession,
        client_name: str,
        partner_name: str,
    ) -> tuple[Session, SessionMemoryVersion]:
        """Create a new session + initial memory version (v0)."""
        display_name = f"{client_name} & {partner_name}" if partner_name else client_name

        session = Session(
            current_stage=StageId.S1_NAMES.value,
            memory_version=0,
            status=SessionStatus.ACTIVE.value,
            client_name=client_name,
            partner_name=partner_name,
            display_name=display_name,
            occasion_type="wedding",
            started_at=datetime.now(timezone.utc),
            last_activity_at=datetime.now(timezone.utc),
        )
        db.add(session)
        await db.flush()  # get session.id

        # Seed initial memory with identity already filled
        initial_memory = fresh_memory()
        initial_memory["identity"]["clientName"] = client_name
        initial_memory["identity"]["partnerName"] = partner_name
        initial_memory["identity"]["displayName"] = display_name

        memory_v0 = SessionMemoryVersion(
            session_id=session.id,
            version_no=0,
            memory_json=initial_memory,
            stale_sections=[],
            open_questions=[],
        )
        db.add(memory_v0)
        await db.flush()

        return session, memory_v0

    @staticmethod
    async def get_session(db: AsyncSession, session_id: uuid.UUID) -> Session | None:
        result = await db.execute(select(Session).where(Session.id == session_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def list_sessions(db: AsyncSession, limit: int = 50) -> list[Session]:
        result = await db.execute(
            select(Session)
            .order_by(Session.last_activity_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    @staticmethod
    async def update_stage(
        db: AsyncSession,
        session: Session,
        new_stage: str,
        decision_type: str,
        request_id: uuid.UUID | None = None,
        reason_code: str | None = None,
    ) -> None:
        """Record a stage transition and update session.current_stage."""
        history = SessionStageHistory(
            session_id=session.id,
            from_stage=session.current_stage,
            to_stage=new_stage,
            decision_type=decision_type,
            reason_code=reason_code,
            request_id=request_id,
        )
        db.add(history)
        session.current_stage = new_stage
        session.last_activity_at = datetime.now(timezone.utc)
        db.add(session)
        await db.flush()

    @staticmethod
    async def get_next_sequence_no(db: AsyncSession, session_id: uuid.UUID) -> int:
        result = await db.execute(
            select(func.coalesce(func.max(SessionMessage.sequence_no), -1))
            .where(SessionMessage.session_id == session_id)
        )
        val = result.scalar_one()
        return val + 1

    @staticmethod
    async def append_message(
        db: AsyncSession,
        session_id: uuid.UUID,
        role: str,
        content: str,
        message_type: str,
        stage: str,
        source: str,
        request_id: uuid.UUID | None = None,
        metadata: dict | None = None,
    ) -> SessionMessage:
        seq = await SessionService.get_next_sequence_no(db, session_id)
        msg = SessionMessage(
            session_id=session_id,
            sequence_no=seq,
            role=role,
            content_text=content,
            message_type=message_type,
            stage=stage,
            source=source,
            request_id=request_id,
            metadata_json=metadata or {},
        )
        db.add(msg)
        await db.flush()
        return msg

    @staticmethod
    async def get_messages_chronological(
        db: AsyncSession,
        session_id: uuid.UUID,
        limit: int = 100,
    ) -> list[SessionMessage]:
        """Return the most recent messages in chronological order."""
        result = await db.execute(
            select(SessionMessage)
            .where(SessionMessage.session_id == session_id)
            .order_by(SessionMessage.sequence_no.desc())
            .limit(limit)
        )
        messages = list(result.scalars().all())
        messages.reverse()
        return messages

    @staticmethod
    async def get_recent_messages(
        db: AsyncSession,
        session_id: uuid.UUID,
        limit: int = 20,
    ) -> list[SessionMessage]:
        return await SessionService.get_messages_chronological(db, session_id, limit=limit)
