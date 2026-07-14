from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.database import get_db
from app.schemas.chat import (
    ChatRequest, ChatResponse,
    CreateUserRequest, UserResponse,
    WeddingProfileResponse,
)
from app.services.conversation_service import ConversationService
from app.models.user import User
from app.models.wedding_profile import WeddingProfile
from sqlalchemy import select

router = APIRouter(tags=["Chat & Users"])


# ──────────────────────────────────────────────
# Users — list all
# ──────────────────────────────────────────────

@router.get("/users", response_model=list[UserResponse])
async def list_users(db: AsyncSession = Depends(get_db)):
    """Return all users, most recent first."""
    result = await db.execute(select(User).order_by(User.created_at.desc()))
    return result.scalars().all()


# ──────────────────────────────────────────────
# Users
# ──────────────────────────────────────────────

@router.post("/users", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: CreateUserRequest,
    db: AsyncSession = Depends(get_db),
):
    """Create a new user."""
    # Check if email already exists
    existing = await db.execute(select(User).where(User.email == payload.email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Email already registered")

    user = User(name=payload.name, email=payload.email)
    db.add(user)
    await db.flush()
    return user


@router.get("/users/{user_id}", response_model=UserResponse)
async def get_user(user_id: int, db: AsyncSession = Depends(get_db)):
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


# ──────────────────────────────────────────────
# Chat
# ──────────────────────────────────────────────

@router.post("/chat", response_model=ChatResponse)
async def send_message(
    payload: ChatRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Main chat endpoint.
    Accepts a user message, runs the full AI pipeline, returns the response.
    """
    conv = await ConversationService.get_conversation(db, payload.conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    result = await ConversationService.process_chat(
        db=db,
        conversation_id=payload.conversation_id,
        user_message=payload.message,
    )

    return ChatResponse(
        conversation_id=payload.conversation_id,
        response=result["response"],
        profile_updates=result["profile_updates"],
        completion_percentage=result["completion_percentage"],
    )


# ──────────────────────────────────────────────
# Profile
# ──────────────────────────────────────────────

@router.get("/profile/{conversation_id}", response_model=WeddingProfileResponse)
async def get_profile(
    conversation_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Get the wedding profile for a conversation."""
    result = await db.execute(
        select(WeddingProfile).where(WeddingProfile.conversation_id == conversation_id)
    )
    profile = result.scalar_one_or_none()
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    return profile
