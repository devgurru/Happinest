"""
Sessions API — create, list, load sessions and their transcripts/memory.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.database import get_db
from app.schemas.planner import (
    CreateSessionRequest, MessageOut, PlannerResponse,
    PlannerNotesView, SessionSummary, StageDecisionSchema,
    normalize_suggestions, SelectedChipsView,
)
from app.services.session.memory_service import MemoryService
from app.graph.wedding_graph import process_s1_names
from app.services.session.session_service import SessionService

router = APIRouter(prefix="/sessions", tags=["Sessions"])


@router.post("", response_model=PlannerResponse, status_code=201)
async def create_session(
    payload: CreateSessionRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    S1 — Create a new planning session (system-handled, no AI).
    Returns the welcome planner message and advances to S2.
    """
    result = await process_s1_names(
        db,
        groom_name=payload.groomName.strip(),
        bride_name=payload.brideName.strip(),
    )
    return _to_response(result)


@router.get("", response_model=list[SessionSummary])
async def list_sessions(db: AsyncSession = Depends(get_db)):
    """List all active sessions (most recent first)."""
    sessions = await SessionService.list_sessions(db)
    return [SessionSummary.from_orm(s) for s in sessions]


@router.get("/{session_id}", response_model=SessionSummary)
async def get_session(session_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    session = await SessionService.get_session(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return SessionSummary.from_orm(session)


@router.get("/{session_id}/messages", response_model=list[MessageOut])
async def get_messages(
    session_id: uuid.UUID,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    """Return the conversation transcript for a session."""
    session = await SessionService.get_session(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    messages = await SessionService.get_messages_chronological(db, session_id, limit=limit)
    return [MessageOut.from_orm(m) for m in messages]


@router.get("/{session_id}/memory")
async def get_memory(session_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Return the latest canonical planner memory for a session."""
    session = await SessionService.get_session(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    mem = await MemoryService.get_latest_memory(db, session_id)
    if not mem:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {
        "sessionId": str(session_id),
        "versionNo": mem.version_no,
        "staleSections": mem.stale_sections,
        "openQuestions": mem.open_questions,
        "memory": mem.memory_json,
    }


def _to_response(result: dict) -> PlannerResponse:
    return PlannerResponse(
        requestId=result["requestId"],
        sessionId=result["sessionId"],
        responseSource=result["responseSource"],
        plannerReply=result["plannerReply"],
        memoryPatch=result.get("memoryPatch", {}),
        updatedMemoryVersion=result.get("updatedMemoryVersion"),
        stageDecision=StageDecisionSchema(**result["stageDecision"]),
        staleSections=result.get("staleSections", []),
        openQuestions=result.get("openQuestions", []),
        suggestions=normalize_suggestions(result.get("suggestions", [])),
        selectedChips=SelectedChipsView(**result["selectedChips"]) if result.get("selectedChips") else None,
        plannerNotesView=PlannerNotesView(**result.get("plannerNotesView", {})),
        artifactContent=result.get("artifactContent"),
        errorCode=result.get("errorCode"),
    )
