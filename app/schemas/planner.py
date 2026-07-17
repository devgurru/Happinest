"""Pydantic schemas for all API request/response shapes."""
import uuid
from typing import Any

from pydantic import BaseModel, Field


# ── Shared ──────────────────────────────────────────────────────────────────

class StageDecisionSchema(BaseModel):
    type: str
    stage: str


class SuggestionSchema(BaseModel):
    label: str
    category: str | None = None


def normalize_suggestions(raw: list) -> list["SuggestionSchema"]:
    """Convert AI suggestion output (strings or dicts) to SuggestionSchema."""
    result = []
    for item in raw:
        if isinstance(item, str):
            result.append(SuggestionSchema(label=item, category=None))
        elif isinstance(item, dict):
            result.append(SuggestionSchema(
                label=item.get("label", ""),
                category=item.get("category"),
            ))
    return result

class PlannerNotesView(BaseModel):
    couple: str = ""
    occasion: str = ""
    feeling: str = ""
    direction: str = ""
    plan: str = ""


class SelectedChipsView(BaseModel):
    personality: list[str] = []
    vibe: list[str] = []
    events: list[str] = []
    directionId: str = ""
    directionName: str = ""


class PlannerResponse(BaseModel):
    requestId: str
    sessionId: str
    responseSource: str
    plannerReply: str
    memoryPatch: dict[str, Any] = {}
    updatedMemoryVersion: int | None = None
    stageDecision: StageDecisionSchema
    staleSections: list[str] = []
    openQuestions: list[Any] = []
    suggestions: list[SuggestionSchema] = []
    selectedChips: SelectedChipsView | None = None
    plannerNotesView: PlannerNotesView
    artifactContent: dict[str, Any] | None = None
    errorCode: str | None = None


# ── Session ──────────────────────────────────────────────────────────────────

class CreateSessionRequest(BaseModel):
    groomName: str = Field(..., min_length=1, max_length=100)
    brideName: str = Field("", max_length=100)


class SessionSummary(BaseModel):
    sessionId: str
    groomName: str | None
    brideName: str | None
    displayName: str | None
    currentStage: str
    memoryVersion: int
    status: str
    startedAt: str
    lastActivityAt: str

    @classmethod
    def from_orm(cls, s: Any) -> "SessionSummary":
        return cls(
            sessionId=str(s.id),
            groomName=s.groom_name,
            brideName=s.bride_name,
            displayName=s.display_name,
            currentStage=s.current_stage,
            memoryVersion=s.memory_version,
            status=s.status,
            startedAt=s.started_at.isoformat(),
            lastActivityAt=s.last_activity_at.isoformat(),
        )


class MessageOut(BaseModel):
    sequenceNo: int
    role: str
    contentText: str
    stage: str | None
    source: str | None
    selectedChips: SelectedChipsView | None = None
    artifactContent: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None
    createdAt: str

    @classmethod
    def from_orm(cls, m: Any) -> "MessageOut":
        meta = getattr(m, "metadata_json", None) or {}
        chips_raw = meta.get("selectedChips")
        selected = SelectedChipsView(**chips_raw) if chips_raw else None
        return cls(
            sequenceNo=m.sequence_no,
            role=m.role,
            contentText=m.content_text,
            stage=m.stage,
            source=m.source,
            selectedChips=selected,
            artifactContent=meta.get("artifactContent"),
            metadata=meta or None,
            createdAt=m.created_at.isoformat(),
        )


# ── Turn ─────────────────────────────────────────────────────────────────────

class TurnRequest(BaseModel):
    """Kept for documentation / SDK generation only.
    The actual /turn endpoint uses multipart/form-data — see conversation.py."""
    eventType: str = Field(..., description="conversation_turn | synthesis_request")
    message: str | None = None


# ── Reference ────────────────────────────────────────────────────────────────

class EventSiteOut(BaseModel):
    id: str
    slug: str
    name: str
    siteType: str
    shortDescription: str
    profileJson: dict[str, Any]
    isActive: bool

    @classmethod
    def from_orm(cls, s: Any) -> "EventSiteOut":
        return cls(
            id=str(s.id),
            slug=s.slug,
            name=s.name,
            siteType=s.site_type,
            shortDescription=s.short_description,
            profileJson=s.profile_json,
            isActive=s.is_active,
        )


class VendorOut(BaseModel):
    id: str
    slug: str
    name: str
    vendorType: str
    primaryCity: str
    primaryRegion: str
    shortDescription: str
    isPreferred: bool
    isActive: bool

    @classmethod
    def from_orm(cls, v: Any) -> "VendorOut":
        return cls(
            id=str(v.id),
            slug=v.slug,
            name=v.name,
            vendorType=v.vendor_type,
            primaryCity=v.primary_city,
            primaryRegion=v.primary_region,
            shortDescription=v.short_description,
            isPreferred=v.is_preferred,
            isActive=v.is_active,
        )
