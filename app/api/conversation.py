"""
Conversation turn API — processes one planner turn per request.
Routes to orchestrator based on event_type.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.database import get_db
from app.domain.enums import EventType
from app.schemas.planner import (
    PlannerNotesView, PlannerResponse, StageDecisionSchema, TurnRequest,
    normalize_suggestions, SelectedChipsView,
)
from app.services.orchestrator import process_conversation_turn, process_synthesis_request
from app.services.session_service import SessionService

router = APIRouter(prefix="/sessions", tags=["Conversation"])


@router.post("/{session_id}/turn", response_model=PlannerResponse)
async def planner_turn(
    session_id: uuid.UUID,
    payload: TurnRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Main conversation endpoint.
    event_type = conversation_turn  → requires payload.message
    event_type = synthesis_request  → requires payload.synthesisType
    draft_update events must NEVER be sent here (frontend-only).
    """
    session = await SessionService.get_session(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if payload.eventType == EventType.DRAFT_UPDATE.value:
        raise HTTPException(
            status_code=400,
            detail="draft_update events are frontend-local and must not reach the backend."
        )

    if payload.eventType == EventType.CONVERSATION_TURN.value:
        if not payload.message or not payload.message.strip():
            raise HTTPException(status_code=422, detail="message is required for conversation_turn")
        result = await process_conversation_turn(
            db,
            session_id=session_id,
            user_message=payload.message.strip(),
            stage=payload.stage,
        )

    elif payload.eventType == EventType.SYNTHESIS_REQUEST.value:
        result = await process_synthesis_request(
            db,
            session_id=session_id,
            synthesis_type=payload.synthesisType,
            stage=payload.stage,
        )

    else:
        raise HTTPException(status_code=400, detail=f"Unknown event_type: {payload.eventType}")

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
        plannerNotesView=PlannerNotesView(**result.get("plannerNotesView", {})),
        suggestions=normalize_suggestions(result.get("suggestions", [])),
        selectedChips=SelectedChipsView(**result["selectedChips"]) if result.get("selectedChips") else None,
        artifactContent=result.get("artifactContent"),
        errorCode=result.get("errorCode"),
    )
