"""
Wedding AI Pipeline — Sequential async pipeline replacing LangGraph.

Flow for process_conversation_turn:
  1. Load session + process images
  2. Data Extraction (AI Call 1)  — extract_and_validate()
  3. Context Building             — build_turn_context() [pure Python]
  4. Response Planning (AI Call 2)— build_response_planner_prompt() + call_llm()
  5. Apply memory patch           — MemoryService.apply_patch()
  6. Resolve final stage          — StagePolicy.resolve_final_decision_with_memory()
  7. Auto-synthesis chains        — S4→S5 brief, S6 direction refresh on correction
  8. Persist + return response

Synthesis flows (process_synthesis_request) and S1 (process_s1_names) are unchanged.
"""

from __future__ import annotations

import copy
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.domain.enums import (
    ArtifactStatus, ArtifactType, EventType, MessageRole, MessageType,
    ResponseSource, StageDecisionType, StageId, SynthesisType,
)
from app.domain.memory_schema import (
    build_planner_notes_view, build_selected_chips, resolve_primary_vibe,
)
from app.models.event_site import EventSite
from app.models.generated_artifact import GeneratedArtifact
from app.models.session_event_site_recommendation import SessionEventSiteRecommendation

from app.services.ai.ai_gateway import AIGatewayError, call_llm
from app.services.ai.data_extractor import extract_and_validate
from app.services.ai.embedding_service import find_matching_event_sites
from app.services.ai.image_service import analyse_images
from app.services.ai.prompt_builder import (
    build_brief_synthesis_prompt,
    build_final_summary_prompt,
    build_response_planner_prompt,
)
from app.services.policy.context_builder import build_turn_context, merge_early_signals
from app.services.policy.correction_policy import (
    apply_stale_artifact_markers,
    detect_upstream_correction,
    resolve_correction_stage_decision,
)
from app.services.policy.planner_reply_policy import align_planner_reply
from app.services.policy.response_sanitizer import sanitize_ai_response
from app.services.policy.response_validator import validate_ai_response, validate_synthesis_response
from app.services.policy.stage_policy import StagePolicy
from app.services.session.memory_service import MemoryService
from app.services.session.session_service import SessionService
from app.services.ui.observability import log_ai_turn
from app.services.ui.ui_hints import build_ui_suggestions


# ─────────────────────────────────────────────────────────────────────────────
# Response helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_error_response(
    request_id: uuid.UUID | str,
    session_id: uuid.UUID | str,
    stage: str,
    memory: dict,
    error_code: str,
    message: str = "Something went wrong. Please try again.",
) -> dict:
    """Standard error response — memory is never mutated on error."""
    return {
        "requestId": str(request_id),
        "sessionId": str(session_id),
        "responseSource": ResponseSource.ERROR.value,
        "plannerReply": message,
        "memoryPatch": {},
        "updatedMemoryVersion": None,
        "stageDecision": {"type": StageDecisionType.STAY.value, "stage": stage},
        "staleSections": [],
        "openQuestions": [],
        "suggestions": [],
        "selectedChips": build_selected_chips(memory),
        "plannerNotesView": build_planner_notes_view(memory),
        "errorCode": error_code,
    }


def _response_dict(
    request_id: uuid.UUID | str,
    session_id: uuid.UUID | str,
    response_source: str,
    planner_reply: str,
    memory: dict,
    *,
    memory_patch: dict | None = None,
    updated_version: int | None = None,
    stage_decision: dict | None = None,
    stale_sections: list | None = None,
    open_questions: list | None = None,
    suggestions: list | None = None,
    artifact_content: dict | None = None,
    error_code: str | None = None,
) -> dict:
    return {
        "requestId": str(request_id),
        "sessionId": str(session_id),
        "responseSource": response_source,
        "plannerReply": planner_reply,
        "memoryPatch": memory_patch or {},
        "updatedMemoryVersion": updated_version,
        "stageDecision": stage_decision or {"type": "stay", "stage": "s2_basics"},
        "staleSections": stale_sections or [],
        "openQuestions": open_questions or [],
        "suggestions": suggestions or [],
        "selectedChips": build_selected_chips(memory),
        "plannerNotesView": build_planner_notes_view(memory),
        "artifactContent": artifact_content,
        "errorCode": error_code,
    }


def _is_direction_request(message: str) -> bool:
    msg = message.lower()
    return any(p in msg for p in (
        "show me direction", "show directions", "see direction",
        "directions", "design direction",
    ))


def _direction_options_from_sites(sites: list[dict]) -> list[dict]:
    """Build S6 directionOptions from embedding matches — no LLM required."""
    options = []
    for i, site in enumerate(sites[:3], start=1):
        profile = site.get("profile_json") or {}
        slug = site.get("slug") or str(site.get("id") or f"option-{i}")
        name = site.get("name") or slug
        reason = (
            profile.get("plannerInterpretation")
            or site.get("short_description")
            or "Strong fit for your brief and setting."
        )
        sim = site.get("similarity")
        options.append({
            "id": slug,
            "name": name,
            "rankOrder": i,
            "fitScore": round(float(sim), 3) if sim is not None else None,
            "reasonText": reason,
            "siteType": site.get("site_type"),
            "shortDescription": site.get("short_description"),
            "profileJson": profile,
            "heroImageUrl": site.get("hero_image_url"),
            "galleryJson": site.get("gallery_json") or [],
            "isActive": site.get("is_active"),
            "seedVersion": site.get("seed_version"),
            "styleTags": profile.get("styleTags") or [],
            "vibeTags": profile.get("vibeTags") or [],
        })
    return options


def _summarize_correction_for_reply(
    correction: dict,
    memory_before: dict,
    memory_after: dict,
) -> str:
    """
    Natural acknowledgment for reanchor turns.
    Rules:
    - Only ack fields where the BEFORE value was non-empty AND it actually changed.
    - Never ack identity (names) — the AI reply handles that warmly.
    - Never ack occasion fields that were empty before (first-time setting isn't a correction).
    """
    parts: list[str] = []
    for section in correction.get("correctedSections") or []:
        if section == "identity":
            # Silently handled — AI reply naturally greets them by new name
            continue
        elif section == "personality":
            before = (memory_before.get("personality") or {}).get("tags") or []
            after = (memory_after.get("personality") or {}).get("tags") or []
            # Only ack if there were existing tags AND they changed
            if before and before != after:
                parts.append(
                    f"personality updated to {', '.join(after) or 'unset'}"
                )
        elif section == "vibe":
            bv = memory_before.get("vibe") or {}
            av = memory_after.get("vibe") or {}
            b_primary = bv.get("primaryVibe") or ""
            a_primary = av.get("primaryVibe") or ""
            b_sec = bv.get("secondaryVibes") or []
            a_sec = av.get("secondaryVibes") or []
            if b_primary and b_primary != a_primary:
                parts.append(f"vibe updated to {a_primary or 'unset'}")
            elif b_sec and b_sec != a_sec:
                parts.append(f"vibe notes updated")
        elif section == "occasion":
            b = memory_before.get("occasion") or {}
            a = memory_after.get("occasion") or {}
            bits = []
            for key, label in (
                ("place", "venue"),
                ("datePreference", "date"),
                ("seasonPreference", "season"),
                ("settingPreference", "setting"),
            ):
                before_val = (b.get(key) or "").strip()
                after_val = (a.get(key) or "").strip()
                # Only ack if the field was SET before AND it changed
                if before_val and before_val != after_val and after_val:
                    bits.append(f"{label} changed to {after_val}")
            if bits:
                parts.append("; ".join(bits))
        elif section == "logistics":
            be = (memory_before.get("logistics") or {}).get("events") or []
            ae = (memory_after.get("logistics") or {}).get("events") or []
            if be and be != ae:
                parts.append(f"events updated to {', '.join(ae) or 'none'}")
    if not parts:
        return ""
    return "; ".join(parts).capitalize() + "."



def _build_direction_planner_reply(
    options: list[dict],
    place: str,
    *,
    correction_ack: str = "",
) -> str:
    """Introduce directions with the top embedding match called out."""
    if not options:
        return "Here are design directions for your celebration."
    top = options[0]
    top_name = top.get("name") or "your top match"
    reason = (top.get("reasonText") or top.get("shortDescription") or "").strip()
    if len(reason) > 160:
        reason = reason[:157] + "..."
    intro = (
        f"My top recommendation for you is {top_name}"
        + (f" — {reason}" if reason else "")
        + ". I've included two more directions below that also fit well."
    )
    if correction_ack:
        return f"{correction_ack} {intro} Which one feels closest — or tell me what to tweak?"
    return (
        f"Based on what I know about you and {place}, {intro.lower()} "
        f"Which one feels closest — or tell me what to tweak?"
    )


async def _persist_direction_recommendations(
    db: AsyncSession,
    session_id: uuid.UUID,
    options: list[dict],
    updated_version: int,
    request_id: uuid.UUID,
) -> None:
    batch_id = uuid.uuid4()
    for opt in options:
        result = await db.execute(
            select(EventSite.id).where(EventSite.slug == opt.get("id"))
        )
        site_id = result.scalar_one_or_none()
        if site_id:
            db.add(SessionEventSiteRecommendation(
                session_id=session_id,
                event_site_id=site_id,
                recommendation_batch_id=batch_id,
                rank_order=opt.get("rankOrder", 99),
                reason_text=opt.get("reasonText"),
                score=opt.get("fitScore"),
                generated_from_memory_version=updated_version,
                request_id=request_id,
            ))


# ─────────────────────────────────────────────────────────────────────────────
# Primary Workflows (S1, Synthesis)
# ─────────────────────────────────────────────────────────────────────────────

async def process_s1_names(
    db: AsyncSession,
    groom_name: str,
    bride_name: str,
) -> dict:
    """S1 — System-handled. No AI call. Creates session, seeds identity, advances to S2."""
    request_id = uuid.uuid4()
    session, memory_v0 = await SessionService.create_session(db, groom_name, bride_name)

    await SessionService.update_stage(
        db, session,
        new_stage=StageId.S2_BASICS.value,
        decision_type=StageDecisionType.ADVANCE.value,
        request_id=request_id,
    )

    welcome = (
        f"Lovely to meet you both, {groom_name} and {bride_name}! 💕 "
        f"What wedding destination are you dreaming of, and what time of year are you planning for?"
    )
    await SessionService.append_message(
        db,
        session_id=session.id,
        role=MessageRole.PLANNER.value,
        content=welcome,
        message_type=MessageType.CONVERSATION_TURN.value,
        stage=StageId.S1_NAMES.value,
        source=ResponseSource.SYSTEM.value,
        request_id=request_id,
    )

    memory = memory_v0.memory_json
    return {
        "requestId": str(request_id),
        "sessionId": str(session.id),
        "responseSource": ResponseSource.SYSTEM.value,
        "plannerReply": welcome,
        "memoryPatch": {"identity": memory.get("identity", {})},
        "updatedMemoryVersion": 0,
        "stageDecision": {"type": StageDecisionType.ADVANCE.value, "stage": StageId.S2_BASICS.value},
        "staleSections": [],
        "openQuestions": [],
        "suggestions": [],
        "selectedChips": build_selected_chips(memory),
        "plannerNotesView": build_planner_notes_view(memory),
        "errorCode": None,
    }


async def _execute_direction_from_embeddings(
    db: AsyncSession,
    session: Any,
    session_id: uuid.UUID,
    stage: str,
    request_id: uuid.UUID,
    *,
    save_planner_message: bool = True,
) -> dict:
    """Fast S6 path: embed canonical memory → top event sites → return top 3."""
    mem_version = await MemoryService.get_latest_memory(db, session_id)
    if not mem_version:
        raise ValueError(f"No memory for session {session_id}")
    memory = mem_version.memory_json
    version_no = mem_version.version_no

    try:
        candidates = await find_matching_event_sites(db, memory, top_k=6)
    except Exception as e:
        await log_ai_turn(
            db, request_id, session_id, stage,
            EventType.SYNTHESIS_REQUEST.value,
            ResponseSource.ERROR.value,
            prompt_family="direction_embedding",
            failure_code="EMBEDDING_FAILED",
            validation_status="rejected",
        )
        return _make_error_response(
            request_id, session_id, stage, memory, "EMBEDDING_FAILED",
            message=f"Could not match directions right now ({e}). Please try again.",
        )

    options = _direction_options_from_sites(candidates)
    if not options:
        return _make_error_response(
            request_id, session_id, stage, memory, "NO_DIRECTION_CANDIDATES",
            message="I couldn't find matching directions yet. Please try again shortly.",
        )

    response_source = ResponseSource.RULE.value
    place = (memory.get("occasion") or {}).get("place") or "your celebration"
    planner_reply = _build_direction_planner_reply(options, place)
    telemetry: dict = {}

    patch = {
        "direction": {
            "options": options,
            "status": "ready",
            "selectedDirectionId": "",
        }
    }
    new_mem = await MemoryService.apply_patch(db, session, patch, request_id=request_id)
    memory = new_mem.memory_json
    updated_version = new_mem.version_no

    artifact_content = {"directionOptions": options}
    await _persist_direction_recommendations(
        db, session_id, options, updated_version, request_id
    )
    db.add(GeneratedArtifact(
        session_id=session_id,
        artifact_type=ArtifactType.DIRECTION.value,
        status=ArtifactStatus.READY.value,
        content_json=artifact_content,
        generated_from_memory_version=updated_version,
        request_id=request_id,
    ))
    await db.flush()

    final_decision_type = StageDecisionType.ADVANCE.value
    final_stage = StageId.S6_DIRECTIONS.value
    if final_stage != session.current_stage:
        await SessionService.update_stage(
            db, session, new_stage=final_stage,
            decision_type=final_decision_type, request_id=request_id,
        )

    if save_planner_message:
        await SessionService.append_message(
            db, session_id=session_id,
            role=MessageRole.PLANNER.value,
            content=planner_reply,
            message_type=MessageType.SYNTHESIS_REQUEST.value,
            stage=final_stage,
            source=response_source,
            request_id=request_id,
            metadata={"artifactType": "direction", "artifactContent": artifact_content},
        )

    await log_ai_turn(
        db, request_id, session_id, stage,
        EventType.SYNTHESIS_REQUEST.value,
        response_source,
        prompt_family="direction_embedding",
        validation_status="accepted",
    )

    return _response_dict(
        request_id, session_id, response_source, planner_reply, memory,
        memory_patch=patch,
        updated_version=updated_version,
        stage_decision={"type": final_decision_type, "stage": final_stage},
        suggestions=[],
        artifact_content=artifact_content,
    )


async def _execute_synthesis(
    db: AsyncSession,
    session: Any,
    session_id: uuid.UUID,
    synthesis_type: str,
    stage: str,
    request_id: uuid.UUID,
    *,
    save_planner_message: bool = True,
) -> dict:
    """Internal synthesis runner — used by synthesis_request and auto-chained flows."""
    if synthesis_type == SynthesisType.DIRECTION.value:
        return await _execute_direction_from_embeddings(
            db, session, session_id, stage, request_id,
            save_planner_message=save_planner_message,
        )

    mem_version = await MemoryService.get_latest_memory(db, session_id)
    if not mem_version:
        raise ValueError(f"No memory for session {session_id}")
    memory = mem_version.memory_json
    version_no = mem_version.version_no

    if synthesis_type == SynthesisType.BRIEF.value:
        messages = build_brief_synthesis_prompt(memory, version_no + 1)
        prompt_family = "brief_synthesis"
    elif synthesis_type == SynthesisType.SUMMARY.value:
        messages = build_final_summary_prompt(memory, version_no + 1)
        prompt_family = "final_summary"
    else:
        return _make_error_response(
            request_id, session_id, stage, memory, f"UNKNOWN_SYNTHESIS_TYPE:{synthesis_type}"
        )

    ai_result: dict | None = None
    telemetry: dict = {}
    error_code: str | None = None
    error_message: str | None = None

    try:
        ai_result, telemetry = await call_llm(messages, stage, EventType.SYNTHESIS_REQUEST.value)
    except AIGatewayError as e:
        error_code = e.code
        error_message = e.message
        telemetry = {"model": settings.active_chat_model, "provider": settings.llm_provider}

    if error_code or not ai_result:
        await log_ai_turn(
            db, request_id, session_id, stage,
            EventType.SYNTHESIS_REQUEST.value,
            ResponseSource.ERROR.value,
            prompt_family=prompt_family,
            failure_code=error_code or "UNKNOWN",
            validation_status="rejected",
        )
        return _make_error_response(
            request_id, session_id, stage, memory, error_code or "AI_CALL_FAILED",
            message=error_message or "Something went wrong. Please try again.",
        )

    is_valid, val_error = validate_synthesis_response(ai_result, synthesis_type)
    if not is_valid:
        await log_ai_turn(
            db, request_id, session_id, stage,
            EventType.SYNTHESIS_REQUEST.value,
            ResponseSource.ERROR.value,
            prompt_family=prompt_family,
            latency_ms=telemetry.get("latency_ms"),
            validation_status="rejected",
            failure_code=val_error,
        )
        return _make_error_response(request_id, session_id, stage, memory, f"VALIDATION_FAILED:{val_error}")

    patch = ai_result.get("memoryPatch", {})
    updated_version = version_no
    if patch:
        new_mem = await MemoryService.apply_patch(db, session, patch, request_id=request_id)
        memory = new_mem.memory_json
        updated_version = new_mem.version_no

    artifact_content: dict = {}
    if synthesis_type == SynthesisType.BRIEF.value:
        artifact_content = {
            "briefText": ai_result.get("briefText", ""),
            "briefQuote": ai_result.get("briefQuote", ""),
        }
        artifact_type = ArtifactType.BRIEF.value
    else:
        artifact_content = {"summaryText": ai_result.get("summaryText", "")}
        artifact_type = ArtifactType.SUMMARY.value

    db.add(GeneratedArtifact(
        session_id=session_id,
        artifact_type=artifact_type,
        status=ArtifactStatus.READY.value,
        content_json=artifact_content,
        generated_from_memory_version=updated_version,
        request_id=request_id,
    ))
    await db.flush()

    if synthesis_type == SynthesisType.BRIEF.value:
        final_decision_type = StageDecisionType.STAY.value
        final_stage = StageId.S5_BRIEF.value
    else:
        final_decision_type = StageDecisionType.STAY.value
        final_stage = stage

    if final_stage != session.current_stage:
        await SessionService.update_stage(
            db, session, new_stage=final_stage,
            decision_type=final_decision_type, request_id=request_id,
        )

    suggestions: list = []
    if final_stage == StageId.S7_EVENTS.value:
        suggestions = build_ui_suggestions(stage, memory, [], for_stage=StageId.S7_EVENTS.value)

    planner_reply = ai_result.get("plannerReply", "")
    if save_planner_message:
        await SessionService.append_message(
            db, session_id=session_id,
            role=MessageRole.PLANNER.value,
            content=planner_reply,
            message_type=MessageType.SYNTHESIS_REQUEST.value,
            stage=final_stage,
            source=ResponseSource.OPENAI.value,
            request_id=request_id,
            metadata={"artifactType": synthesis_type, "artifactContent": artifact_content},
        )

    await log_ai_turn(
        db, request_id, session_id, stage,
        EventType.SYNTHESIS_REQUEST.value,
        ResponseSource.OPENAI.value,
        prompt_family=prompt_family,
        model=telemetry.get("model"),
        latency_ms=telemetry.get("latency_ms"),
        input_tokens=telemetry.get("input_tokens"),
        output_tokens=telemetry.get("output_tokens"),
        validation_status="accepted",
    )

    return _response_dict(
        request_id, session_id, ResponseSource.OPENAI.value, planner_reply, memory,
        memory_patch=patch,
        updated_version=updated_version,
        stage_decision={"type": final_decision_type, "stage": final_stage},
        stale_sections=ai_result.get("staleSections", []),
        open_questions=ai_result.get("openQuestions", []),
        suggestions=suggestions,
        artifact_content=artifact_content,
    )


async def process_synthesis_request(
    db: AsyncSession,
    session_id: uuid.UUID,
    synthesis_type: str | None = None,
) -> dict:
    """Pipeline for synthesis_request: brief, direction, or summary."""
    request_id = uuid.uuid4()

    session = await SessionService.get_session(db, session_id)
    if not session:
        raise ValueError(f"Session {session_id} not found")

    stage = session.current_stage
    mem_version = await MemoryService.get_latest_memory(db, session_id)
    if not mem_version:
        raise ValueError(f"No memory for session {session_id}")
    memory = mem_version.memory_json

    if stage == StageId.S4_VIBE.value:
        resolved = resolve_primary_vibe(memory)
        if resolved and not (memory.get("vibe") or {}).get("primaryVibe"):
            sync = await MemoryService.apply_patch(
                db, session,
                {"vibe": {"primaryVibe": resolved}},
                request_id=request_id,
            )
            memory = sync.memory_json

    synthesis_type = synthesis_type or StagePolicy.infer_synthesis_type(stage, memory)
    if not synthesis_type:
        if stage == StageId.S4_VIBE.value and not resolve_primary_vibe(memory):
            return _make_error_response(
                request_id, session_id, stage, memory,
                "VIBE_INCOMPLETE",
                "Confirm your primary vibe with a conversation_turn first — "
                "then the brief generates automatically (or call synthesis_request again).",
            )
        return _make_error_response(
            request_id, session_id, stage, memory,
            f"CANNOT_INFER_SYNTHESIS_TYPE:{stage}",
            "Synthesis is available on s5_brief (brief/directions), s6_directions, "
            "and s11_summary — or on s4_vibe once vibe is confirmed.",
        )

    run_stage = stage
    if stage == StageId.S4_VIBE.value and synthesis_type == SynthesisType.BRIEF.value:
        run_stage = StageId.S5_BRIEF.value

    return await _execute_synthesis(
        db, session, session_id, synthesis_type, run_stage, request_id,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Main Conversation Pipeline
# ─────────────────────────────────────────────────────────────────────────────

async def process_conversation_turn(
    db: AsyncSession,
    session_id: uuid.UUID,
    user_message: str,
    images: list[str] | None = None,
) -> dict:
    """
    Main sequential pipeline for conversation_turn events.

    Phase 1: Load session + process images
    Phase 2: Data Extraction (AI Call 1)
    Phase 3: Context Building (pure Python)
    Phase 4: Response Planning (AI Call 2)
    Phase 5: Apply memory + resolve stage
    Phase 6: Return response
    """
    request_id = uuid.uuid4()
    images = images or []

    # ── Phase 1: Load Session ──────────────────────────────────────────────────
    session = await SessionService.get_session(db, session_id)
    if not session:
        raise ValueError(f"Session {session_id} not found")

    stage = session.current_stage
    mem_version = await MemoryService.get_latest_memory(db, session_id)
    if not mem_version:
        raise ValueError(f"No memory for session {session_id}")

    memory = mem_version.memory_json
    memory_before = copy.deepcopy(memory)
    version_no = mem_version.version_no

    # Process images (if any)
    image_context = ""
    if images:
        vis_patch, image_context, _vis_telemetry = await analyse_images(images, stage, memory)
        if vis_patch:
            vis_mem = await MemoryService.apply_patch(db, session, vis_patch, request_id=request_id)
            memory = vis_mem.memory_json
            memory_before = copy.deepcopy(memory)
            version_no = vis_mem.version_no

    # Load recent messages for prompt history
    all_messages = await SessionService.get_recent_messages(db, session_id, limit=21)
    recent_messages = [
        {"role": m.role, "content": m.content_text}
        for m in all_messages
    ]

    # S5 direction shortcut (user explicitly asks for directions)
    if stage == StageId.S5_BRIEF.value and _is_direction_request(user_message):
        await SessionService.append_message(
            db, session_id=session_id,
            role=MessageRole.CLIENT.value, content=user_message,
            message_type=MessageType.SYNTHESIS_REQUEST.value,
            stage=stage, source=None, request_id=request_id,
            metadata={"selectedChips": build_selected_chips(memory)},
        )
        return await _execute_synthesis(
            db, session, session_id, SynthesisType.DIRECTION.value,
            stage, request_id, save_planner_message=True,
        )

    # ── Phase 2: Data Extraction (AI Call 1) ──────────────────────────────────
    extraction = await extract_and_validate(stage, memory, user_message)

    # ── Phase 2.5: Apply Extraction Patch to DB IMMEDIATELY ──────────────────
    # Committing validated data BEFORE context building ensures the context
    # builder operates on real committed state, not a tentative scratch merge.
    # This fixes: agent staying on stage even after data was just extracted.
    extraction_patch: dict = extraction.validated_patch or {}
    if extraction_patch and not extraction.is_meta():
        # Compute displayName before applying — so it's always stored correctly
        if "identity" in extraction_patch:
            _ip = dict(extraction_patch["identity"])
            _groom = (_ip.get("groomName") or "").strip()
            _bride = (_ip.get("brideName") or "").strip()
            _display_names = [n for n in [_groom, _bride] if n]
            if _display_names:
                _ip["displayName"] = " & ".join(_display_names)
            extraction_patch = {**extraction_patch, "identity": _ip}

        try:
            ex_mem = await MemoryService.apply_patch(
                db, session, extraction_patch, request_id=request_id,
            )
            memory = ex_mem.memory_json
            version_no = ex_mem.version_no
        except Exception as _ep:
            import logging as _log
            _log.getLogger(__name__).warning("Extraction patch apply failed: %s", _ep)
            extraction_patch = {}

        # Handle identity / name updates immediately (update session record)
        if "identity" in extraction_patch:
            _ip = extraction_patch["identity"]
            await SessionService.update_names(
                db, session,
                groom_name=_ip.get("groomName") or None,
                bride_name=_ip.get("brideName") or None,
            )


    # ── Phase 3: Context Building (pure Python) ───────────────────────────────
    # ctx now operates on the DB-committed memory (real state, not tentative)
    ctx = build_turn_context(stage, memory, extraction)

    # ── Phase 4: Response Planning (AI Call 2) ────────────────────────────────
    messages = build_response_planner_prompt(
        stage=stage,
        memory=memory,
        recent_messages=recent_messages,
        ctx=ctx,
        user_message=user_message,
        image_context=image_context,
    )

    ai_result: dict | None = None
    telemetry: dict = {}
    error_code: str | None = None
    error_message: str | None = None

    try:
        ai_result, telemetry = await call_llm(messages, stage, EventType.CONVERSATION_TURN.value)
    except AIGatewayError as e:
        error_code = e.code
        error_message = e.message
        telemetry = {
            "model": settings.active_chat_model,
            "provider": settings.llm_provider,
            "http_status": e.http_status,
        }

    if error_code or not ai_result:
        await log_ai_turn(
            db, request_id, session_id, stage,
            EventType.CONVERSATION_TURN.value,
            ResponseSource.ERROR.value,
            prompt_family="conversation_turn",
            validation_status="rejected",
            failure_code=error_code or "UNKNOWN",
        )
        return _make_error_response(
            request_id, session_id, stage, memory,
            error_code or "AI_CALL_FAILED",
        )

    # ── Sanitize & enforce meta-intent constraints ─────────────────────────────
    ai_result = sanitize_ai_response(ai_result, stage)
    meta_intent = ctx.meta_intent

    if meta_intent == "gibberish":
        ai_result["memoryPatch"] = {}
        ai_result["stageDecision"] = {
            "type": StageDecisionType.REQUEST_CLARIFICATION.value,
            "stage": stage,
        }
        if not (ai_result.get("plannerReply") or "").strip():
            ai_result["plannerReply"] = (
                "I didn't quite catch that! As your wedding planner, I'm here to assist you with all your wedding arrangements. "
                "Could you please share your preference for this stage?"
            )
    elif meta_intent in ("help", "more_suggestions"):
        ai_result["memoryPatch"] = {}
        ai_result["stageDecision"] = {
            "type": StageDecisionType.STAY.value,
            "stage": stage,
        }
        if not (ai_result.get("plannerReply") or "").strip():
            ai_result["plannerReply"] = (
                "I'm Happinest, your personal AI wedding planner! I'm here to help you design and organize your dream wedding. "
                "How can I assist you with your plans?"
            )

    else:
        # Override AI's stageDecision with context builder's authoritative decision.
        # ctx.stage_decision was computed on real committed memory after extraction —
        # this fixes: AI saying "stay" even though stage data is already complete.
        ai_result["stageDecision"] = ctx.stage_decision

    is_valid, val_error = validate_ai_response(ai_result, stage)
    if not is_valid:
        await log_ai_turn(
            db, request_id, session_id, stage,
            EventType.CONVERSATION_TURN.value,
            ResponseSource.ERROR.value,
            prompt_family="conversation_turn",
            latency_ms=telemetry.get("latency_ms"),
            validation_status="rejected",
            failure_code=val_error,
        )
        return _make_error_response(
            request_id, session_id, stage, memory, f"VALIDATION_FAILED:{val_error}"
        )

    # ── Phase 5: Apply Additional Memory Patch ───────────────────────────────
    # Extraction patch was already committed (Phase 2.5).
    # Now apply ADDITIONAL data: earlySignals from extraction + any non-extraction
    # AI patches (e.g. eventsConfirmed set by AI, extra personality signals).
    ai_patch = ai_result.get("memoryPatch") or {}

    additional_patch: dict = {}

    # Carry over AI patches for fields NOT already committed by extraction
    from app.utils.validators import is_past_date
    for _k, _v in ai_patch.items():
        if _k not in extraction_patch and _k != "earlySignals":
            if _k == "occasion" and isinstance(_v, dict):
                _v_copy = dict(_v)
                _dp = (_v_copy.get("datePreference") or "").strip()
                if _dp and is_past_date(_dp):
                    _v_copy.pop("datePreference", None)
                if _v_copy:
                    additional_patch[_k] = _v_copy
            else:
                additional_patch[_k] = _v


    # Merge earlySignals: extraction early signals + any AI early signals
    if meta_intent not in ("help", "more_suggestions", "gibberish"):
        early_to_patch = ctx.early_signals_to_patch
        ai_early = ai_patch.get("earlySignals") or {}
        combined_early = merge_early_signals(
            memory.get("earlySignals") or {},
            merge_early_signals(ai_early, early_to_patch),
        )
        if any(v for v in combined_early.values() if v):
            additional_patch["earlySignals"] = combined_early

    # Combined patch for correction detection (full turn change)
    combined_patch: dict = {**extraction_patch, **additional_patch}

    updated_version = version_no
    correction = None
    stale_sections: list = list(ai_result.get("staleSections") or [])
    open_questions: list = ai_result.get("openQuestions") or []

    if additional_patch:
        new_mem_version = await MemoryService.apply_patch(
            db, session, additional_patch,
            request_id=request_id,
            open_questions=open_questions,
            extra_stale=stale_sections,
        )
        memory = new_mem_version.memory_json
        updated_version = new_mem_version.version_no
        stale_sections = new_mem_version.stale_sections

    if combined_patch:
        correction = detect_upstream_correction(combined_patch, memory_before, memory, stage)
        if correction:
            stale_patch = apply_stale_artifact_markers({}, correction["staleSections"])
            if stale_patch:
                new_mem_version = await MemoryService.apply_patch(
                    db, session, stale_patch, request_id=request_id,
                    extra_stale=correction["staleSections"],
                )
                memory = new_mem_version.memory_json
                updated_version = new_mem_version.version_no
                stale_sections = new_mem_version.stale_sections

    # Identity from AI patch (extraction already handled identity in Phase 2.5)
    if "identity" in additional_patch and "identity" not in extraction_patch:
        _aip = additional_patch["identity"]
        await SessionService.update_names(
            db, session,
            groom_name=_aip.get("groomName") or None,
            bride_name=_aip.get("brideName") or None,
        )

    # S4 vibe sync
    if stage == StageId.S4_VIBE.value:
        resolved = resolve_primary_vibe(memory)
        if resolved and not (memory.get("vibe") or {}).get("primaryVibe"):
            sync = await MemoryService.apply_patch(
                db, session,
                {"vibe": {"primaryVibe": resolved}},
                request_id=request_id,
            )
            memory = sync.memory_json
            updated_version = sync.version_no

    # Persist client message
    _client_meta: dict = {"selectedChips": build_selected_chips(memory)}
    if images:
        _client_meta["imageCount"] = len(images)
    await SessionService.append_message(
        db, session_id=session_id,
        role=MessageRole.CLIENT.value,
        content=user_message,
        message_type=MessageType.CONVERSATION_TURN.value,
        stage=stage, source=None,
        request_id=request_id,
        metadata=_client_meta,
    )

    # ── Resolve Final Stage ────────────────────────────────────────────────────
    sd = ai_result.get("stageDecision") or ctx.stage_decision
    ai_decision_type = sd.get("type", StageDecisionType.STAY.value)
    ai_to_stage = sd.get("stage", stage)

    if extraction.is_meta():
        # Meta turns (help / gibberish / more_suggestions) MUST STAY on current stage
        final_decision_type = (
            StageDecisionType.REQUEST_CLARIFICATION.value
            if meta_intent == "gibberish"
            else StageDecisionType.STAY.value
        )
        final_stage = stage
        _reason = f"meta_turn_{meta_intent}"
    elif StagePolicy.is_stage_complete(stage, memory) and not open_questions:
        try:
            next_s = StageId(stage).next_stage()
            final_stage = next_s.value if next_s else stage
            final_decision_type = StageDecisionType.ADVANCE.value
            _reason = "stage_complete_advance"
        except ValueError:
            final_stage = stage
            final_decision_type = StageDecisionType.STAY.value
            _reason = "last_stage_stay"
    elif correction:
        final_decision_type, final_stage, _reason = resolve_correction_stage_decision(
            correction, stage
        )
        stale_sections = list(set(stale_sections) | set(correction.get("staleSections", [])))
        if meta_intent == "correction" and not StagePolicy.is_stage_complete(stage, memory):
            final_decision_type = StageDecisionType.REANCHOR.value
            final_stage = stage
    else:
        final_decision_type, final_stage, _reason = StagePolicy.resolve_final_decision_with_memory(
            ai_decision_type, ai_to_stage, stage, memory,
            open_questions=open_questions,
        )
        if meta_intent == "correction" and not StagePolicy.is_stage_complete(stage, memory):
            final_decision_type = StageDecisionType.REANCHOR.value
            final_stage = stage

    if final_stage != stage:
        await SessionService.update_stage(
            db, session,
            new_stage=final_stage,
            decision_type=final_decision_type,
            request_id=request_id,
        )
    elif final_decision_type == StageDecisionType.REANCHOR.value and correction:
        await SessionService.update_stage(
            db, session,
            new_stage=stage,
            decision_type=final_decision_type,
            request_id=request_id,
            reason_code="upstream_correction_reanchor",
        )

    # ── Auto-synthesis chains ──────────────────────────────────────────────────
    synthesis_result = None

    # S4→S5: Auto-brief synthesis when vibe is complete
    if (
        stage == StageId.S4_VIBE.value
        and final_stage == StageId.S5_BRIEF.value
        and not correction
        and StagePolicy.is_stage_complete(StageId.S3_PERSONALITY.value, memory)
        and StagePolicy.is_stage_complete(StageId.S4_VIBE.value, memory)
    ):
        brief_res = await _execute_synthesis(
            db, session, session_id, SynthesisType.BRIEF.value,
            StageId.S5_BRIEF.value, request_id, save_planner_message=False,
        )
        if not brief_res.get("errorCode"):
            synthesis_result = brief_res

    # S6: Direction refresh on correction
    elif correction and stage == StageId.S6_DIRECTIONS.value and (
        correction.get("shouldRegenerateDirection")
        or correction.get("shouldRefreshDirectionsOnS6")
    ):
        dir_res = await _execute_synthesis(
            db, session, session_id, SynthesisType.DIRECTION.value,
            StageId.S6_DIRECTIONS.value, request_id, save_planner_message=False,
        )
        if not dir_res.get("errorCode"):
            ack = _summarize_correction_for_reply(correction, memory_before, memory)
            opts = (dir_res.get("artifactContent") or {}).get("directionOptions") or []
            place = (memory.get("occasion") or {}).get("place") or "your celebration"
            dir_res["plannerReply"] = _build_direction_planner_reply(opts, place, correction_ack=ack)
            dir_res["staleSections"] = stale_sections
            dir_res["memoryPatch"] = {**(dir_res.get("memoryPatch") or {}), **(combined_patch or {})}
            dir_res["stageDecision"] = {
                "type": StageDecisionType.REANCHOR.value,
                "stage": StageId.S6_DIRECTIONS.value,
            }
            await SessionService.append_message(
                db, session_id=session_id,
                role=MessageRole.PLANNER.value,
                content=dir_res["plannerReply"],
                message_type=MessageType.SYNTHESIS_REQUEST.value,
                stage=StageId.S6_DIRECTIONS.value,
                source=dir_res.get("responseSource", ResponseSource.RULE.value),
                request_id=request_id,
                metadata={
                    "selectedChips": build_selected_chips(memory),
                    "artifactType": "direction",
                    "artifactContent": dir_res.get("artifactContent"),
                    "correctionAck": True,
                },
            )
            synthesis_result = dir_res

    # Brief refresh on correction (S5/S6)
    elif correction and correction.get("shouldRegenerateBrief"):
        brief_res = await _execute_synthesis(
            db, session, session_id, SynthesisType.BRIEF.value,
            stage, request_id, save_planner_message=False,
        )
        if not brief_res.get("errorCode"):
            ack = _summarize_correction_for_reply(correction, memory_before, memory)
            brief_text = (brief_res.get("artifactContent") or {}).get("briefText") or ""
            refreshed = (
                f"{ack}\n\n{brief_text}" if brief_text
                else f"{ack} {brief_res.get('plannerReply', '')}".strip()
            )
            brief_res["plannerReply"] = refreshed
            brief_res["staleSections"] = stale_sections
            brief_res["memoryPatch"] = {**(brief_res.get("memoryPatch") or {}), **(combined_patch or {})}
            brief_res["stageDecision"] = {"type": StageDecisionType.REANCHOR.value, "stage": stage}
            await SessionService.append_message(
                db, session_id=session_id,
                role=MessageRole.PLANNER.value,
                content=refreshed,
                message_type=MessageType.SYNTHESIS_REQUEST.value,
                stage=stage,
                source=brief_res.get("responseSource", ResponseSource.OPENAI.value),
                request_id=request_id,
                metadata={
                    "selectedChips": build_selected_chips(memory),
                    "artifactType": "brief",
                    "artifactContent": brief_res.get("artifactContent"),
                    "correctionAck": True,
                },
            )
            synthesis_result = brief_res

    if synthesis_result:
        return synthesis_result

    # ── Build & Return Response ────────────────────────────────────────────────
    # Chips are ONLY meaningful on stages that have selectable options.
    # S1, S2, S5, S6, S8, S9, S11 → empty list (agent asks directly, no chips)
    _CHIP_STAGES = frozenset({
        StageId.S3_PERSONALITY.value,
        StageId.S4_VIBE.value,
        StageId.S7_EVENTS.value,
        StageId.S10_VENDORS.value,
    })
    effective_stage = final_stage if final_stage != stage else stage
    if effective_stage not in _CHIP_STAGES:
        suggestions = []
    else:
        suggestion_stage = effective_stage
        suggestions = build_ui_suggestions(
            stage, memory, ai_result.get("suggestions", []),
            for_stage=suggestion_stage,
            prefer_custom=(meta_intent == "more_suggestions"),
        )
        suggestions = [
            s for s in suggestions
            if isinstance(s, dict)
            and s.get("label")
            and not re.search(r"guestcount|_guests$|_estimate", str(s.get("label", "")), re.I)
        ]


    correction_ack = ""
    if correction:
        correction_ack = _summarize_correction_for_reply(correction, memory_before, memory)

    planner_reply = align_planner_reply(
        ai_reply=ai_result.get("plannerReply", "") or "",
        from_stage=stage,
        to_stage=final_stage,
        decision_type=final_decision_type,
        memory=memory,
        correction=correction,
        correction_ack=correction_ack,
    )

    await SessionService.append_message(
        db, session_id=session_id,
        role=MessageRole.PLANNER.value,
        content=planner_reply,
        message_type=MessageType.CONVERSATION_TURN.value,
        stage=final_stage,
        source=ResponseSource.OPENAI.value,
        request_id=request_id,
        metadata={"selectedChips": build_selected_chips(memory)},
    )

    await log_ai_turn(
        db, request_id, session_id, stage,
        EventType.CONVERSATION_TURN.value,
        ResponseSource.OPENAI.value,
        prompt_family="conversation_turn",
        model=telemetry.get("model"),
        http_status=telemetry.get("http_status"),
        latency_ms=telemetry.get("latency_ms"),
        input_tokens=telemetry.get("input_tokens"),
        output_tokens=telemetry.get("output_tokens"),
        validation_status="accepted",
    )

    return _response_dict(
        request_id, session_id, ResponseSource.OPENAI.value, planner_reply, memory,
        memory_patch=combined_patch,
        updated_version=updated_version,
        stage_decision={"type": final_decision_type, "stage": final_stage},
        stale_sections=stale_sections,
        open_questions=open_questions,
        suggestions=suggestions,
    )
