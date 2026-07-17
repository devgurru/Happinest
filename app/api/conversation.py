"""
Conversation turn API — processes one planner turn per request.

Accepts multipart/form-data so the frontend can send raw image files directly
without any base64 conversion. The backend handles that internally.

Form fields:
  eventType  (str, required)  — "conversation_turn" | "synthesis_request"
  message    (str, optional)  — required for conversation_turn unless images supplied
  images     (file, optional) — up to 3 image files (JPEG/PNG/WEBP/GIF)
                                sent as repeated form fields: images=<file1>, images=<file2>

Why multipart/form-data instead of JSON + base64?
  • Frontend sends raw bytes — no conversion needed (just FormData.append("images", file))
  • No ~33% payload inflation from base64 encoding
  • Standard HTTP file-upload pattern; works natively with <input type="file">
  • Images are read into memory, converted to base64 HERE, then discarded after processing
"""
import base64
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.database import get_db
from app.domain.enums import EventType
from app.schemas.planner import (
    PlannerNotesView, PlannerResponse, StageDecisionSchema,
    normalize_suggestions, SelectedChipsView,
)
from app.services.orchestrator import process_conversation_turn, process_synthesis_request
from app.services.session_service import SessionService

router = APIRouter(prefix="/sessions", tags=["Conversation"])

# Allowed MIME types for uploaded images
_ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp", "image/gif"}
# 10 MB per-file cap
_MAX_FILE_BYTES = 10 * 1024 * 1024


@router.post("/{session_id}/turn", response_model=PlannerResponse)
async def planner_turn(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    # ── text fields (Form) ──────────────────────────────────────────────────
    eventType: Annotated[str, Form(description="conversation_turn | synthesis_request")] = ...,
    message: Annotated[str | None, Form(description="User message text")] = None,
    # ── optional file uploads ───────────────────────────────────────────────
    images: Annotated[
        list[UploadFile],
        File(description="Up to 3 image files (JPEG/PNG/WEBP/GIF). Processed in-memory, never stored."),
    ] = [],
):
    """
    Main conversation endpoint.

    Send as **multipart/form-data** (not JSON):
    - `eventType`  — "conversation_turn" | "synthesis_request"
    - `message`    — user text (required for conversation_turn unless images supplied)
    - `images`     — up to 3 image files (optional, repeat the field per file)

    Example fetch:
    ```js
    const form = new FormData();
    form.append("eventType", "conversation_turn");
    form.append("message",   "I love this venue style!");
    form.append("images",    fileInput.files[0]);
    fetch(`/api/v2/sessions/${id}/turn`, { method: "POST", body: form });
    ```

    Images are processed in-memory by the vision model and immediately discarded.
    They are NEVER written to disk or stored in the database.
    """
    session = await SessionService.get_session(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if eventType == EventType.DRAFT_UPDATE.value:
        raise HTTPException(
            status_code=400,
            detail="draft_update events are frontend-local and must not reach the backend.",
        )

    # ── Validate & read uploaded images ────────────────────────────────────
    if len(images) > 3:
        raise HTTPException(status_code=422, detail="Maximum 3 images per turn are allowed.")

    image_b64_list: list[str] = []
    for upload in images:
        # Validate MIME type
        content_type = (upload.content_type or "").lower()
        if content_type not in _ALLOWED_MIME:
            raise HTTPException(
                status_code=422,
                detail=f"Unsupported image type '{content_type}'. Allowed: JPEG, PNG, WEBP, GIF.",
            )
        # Read bytes (FastAPI streams the upload)
        raw_bytes = await upload.read()
        if len(raw_bytes) > _MAX_FILE_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"Image '{upload.filename}' exceeds the 10 MB limit.",
            )
        # Convert to base64 data URI — this is the ONLY place conversion happens
        b64 = base64.b64encode(raw_bytes).decode("utf-8")
        image_b64_list.append(f"data:{content_type};base64,{b64}")
        # raw_bytes goes out of scope here; GC handles cleanup

    # ── Route by event type ─────────────────────────────────────────────────
    if eventType == EventType.CONVERSATION_TURN.value:
        # Text is optional when images are present
        msg = (message or "").strip()
        if not image_b64_list and not msg:
            raise HTTPException(
                status_code=422,
                detail="message is required for conversation_turn (unless images are provided).",
            )
        result = await process_conversation_turn(
            db,
            session_id=session_id,
            user_message=msg,
            images=image_b64_list,
        )

    elif eventType == EventType.SYNTHESIS_REQUEST.value:
        result = await process_synthesis_request(
            db,
            session_id=session_id,
        )

    else:
        raise HTTPException(status_code=400, detail=f"Unknown event_type: {eventType}")

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
