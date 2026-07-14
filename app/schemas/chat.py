from datetime import datetime
from pydantic import BaseModel, Field


# ──────────────────────────────────────────────
# Request schemas
# ──────────────────────────────────────────────

class ChatRequest(BaseModel):
    conversation_id: int = Field(..., description="ID of the active conversation")
    message: str = Field(..., min_length=1, max_length=4000, description="User message")


class CreateUserRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    email: str = Field(..., max_length=255)


class CreateConversationRequest(BaseModel):
    user_id: int
    title: str | None = Field(None, max_length=500)


# ──────────────────────────────────────────────
# Response schemas
# ──────────────────────────────────────────────

class ChatResponse(BaseModel):
    conversation_id: int
    response: str
    profile_updates: dict
    completion_percentage: float


class UserResponse(BaseModel):
    id: int
    name: str
    email: str
    created_at: datetime

    model_config = {"from_attributes": True}


class ConversationResponse(BaseModel):
    id: int
    user_id: int
    title: str | None
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class MessageResponse(BaseModel):
    id: int
    conversation_id: int
    role: str
    content: str
    created_at: datetime

    model_config = {"from_attributes": True}


class WeddingProfileResponse(BaseModel):
    id: int
    conversation_id: int
    profile_json: dict
    completion_percentage: float
    updated_at: datetime

    model_config = {"from_attributes": True}
