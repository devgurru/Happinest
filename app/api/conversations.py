from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.database import get_db
from app.models.user import User
from app.models.conversation import Conversation
from app.schemas.chat import (
    CreateConversationRequest, ConversationResponse,
    MessageResponse,
)
from app.services.conversation_service import ConversationService
from sqlalchemy import select

router = APIRouter(prefix="/conversations", tags=["Conversations"])


@router.post("/", response_model=ConversationResponse, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    payload: CreateConversationRequest,
    db: AsyncSession = Depends(get_db),
):
    """Create a new conversation for a user."""
    user = await db.get(User, payload.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    conv = await ConversationService.create_conversation(db, payload.user_id, payload.title)
    return conv


# Static route MUST come before /{conversation_id} wildcard routes
@router.get("/user/{user_id}", response_model=list[ConversationResponse])
async def get_user_conversations(
    user_id: int,
    db: AsyncSession = Depends(get_db),
):
    """List all conversations for a user, most recent first."""
    result = await db.execute(
        select(Conversation)
        .where(Conversation.user_id == user_id)
        .order_by(Conversation.updated_at.desc())
    )
    return result.scalars().all()


@router.get("/{conversation_id}", response_model=ConversationResponse)
async def get_conversation(
    conversation_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Retrieve a conversation by ID."""
    conv = await ConversationService.get_conversation(db, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv


@router.get("/{conversation_id}/messages", response_model=list[MessageResponse])
async def get_messages(
    conversation_id: int,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    """Retrieve recent messages for a conversation."""
    conv = await ConversationService.get_conversation(db, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    messages = await ConversationService.get_recent_messages(db, conversation_id, limit=limit)
    return messages
