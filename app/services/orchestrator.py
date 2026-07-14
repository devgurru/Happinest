"""
Orchestrator — the core turn-processing pipeline.
Backend controls all decisions. AI supports.

Flow for conversation_turn:
  1. Load session + latest memory
  2. Load recent messages
  3. Build prompt
  4. Call AI
  5. Validate AI response
  6. Apply memory patch (only if valid)
  7. Resolve stage decision via policy
  8. Save planner message
  9. Update session stage if advancing
  10. Log turn
  11. Return typed response

No fallback responses. Explicit error on failure.
"""
import copy
import re
import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import (
    ArtifactStatus, ArtifactType, EventType, MessageRole, MessageType,
    ResponseSource, StageDecisionType, StageId, SynthesisType,
)
from app.domain.memory_schema import (
    build_planner_notes_view, build_selected_chips,
)
from app.models.generated_artifact import GeneratedArtifact
from app.models.session_event_site_recommendation import SessionEventSiteRecommendation
from app.services.ai_gateway import AIGatewayError, call_llm
from app.services.correction_policy import (
    apply_stale_artifact_markers,
    detect_upstream_correction,
    resolve_correction_stage_decision,
)
from app.services.embedding_service import find_matching_event_sites
from app.services.memory_service import MemoryService
from app.services.observability import log_ai_turn
from app.services.prompt_builder import (
    build_brief_synthesis_prompt,
    build_conversation_turn_prompt,
    build_final_summary_prompt,
)
from app.services.response_validator import validate_ai_response, validate_synthesis_response
from app.services.session_service import SessionService
from app.services.stage_policy import StagePolicy
from app.services.patch_sanitizer import sanitize_memory_patch
from app.services.turn_interpreter import enrich_memory_patch, merge_patches
from app.services.turn_intent import TurnIntent, classify_turn_intent
from app.services.response_sanitizer import sanitize_ai_response
from app.services.ui_hints import build_ui_suggestions
from app.config import settings


def _make_error_response(
    request_id: uuid.UUID,
    session_id: uuid.UUID,
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
    request_id: uuid.UUID,
    session_id: uuid.UUID,
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


async def process_s1_names(
    db: AsyncSession,
    client_name: str,
    partner_name: str,
) -> dict:
    """
    S1 — System-handled. No AI call.
    Creates session, seeds identity memory, advances to S2.
    """
    request_id = uuid.uuid4()
    session, memory_v0 = await SessionService.create_session(db, client_name, partner_name)

    # Advance to S2
    await SessionService.update_stage(
        db, session,
        new_stage=StageId.S2_BASICS.value,
        decision_type=StageDecisionType.ADVANCE.value,
        request_id=request_id,
    )

    # Save the planner welcome message
    welcome = (
        f"Lovely to meet you both — {client_name} and {partner_name}! "
        f"I'm so excited to help you plan your wedding. Let's start by getting to know "
        f"a bit about what you have in mind. Where are you thinking of having the wedding?"
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
            "styleTags": profile.get("styleTags") or [],
            "vibeTags": profile.get("vibeTags") or [],
        })
    return options


def _summarize_correction_for_reply(
    correction: dict,
    memory_before: dict,
    memory_after: dict,
) -> str:
    """Explicit before→after acknowledgment for reanchor turns — only real changes."""
    parts: list[str] = []
    for section in correction.get("correctedSections") or []:
        if section == "personality":
            before = (memory_before.get("personality") or {}).get("tags") or []
            after = (memory_after.get("personality") or {}).get("tags") or []
            if before != after:
                parts.append(
                    f"personality: {', '.join(before) or 'unset'} → {', '.join(after) or 'unset'}"
                )
        elif section == "vibe":
            bv = memory_before.get("vibe") or {}
            av = memory_after.get("vibe") or {}
            b_primary = bv.get("primaryVibe") or "unset"
            a_primary = av.get("primaryVibe") or "unset"
            b_sec = bv.get("secondaryVibes") or []
            a_sec = av.get("secondaryVibes") or []
            if b_primary != a_primary:
                parts.append(f"vibe: {b_primary} → {a_primary}")
            elif b_sec != a_sec:
                parts.append(
                    f"vibe notes: {', '.join(b_sec) or 'none'} → {', '.join(a_sec) or 'none'}"
                )
        elif section == "occasion":
            b = memory_before.get("occasion") or {}
            a = memory_after.get("occasion") or {}
            bits = []
            for key, label in (
                ("place", "place"),
                ("datePreference", "date"),
                ("seasonPreference", "season"),
                ("settingPreference", "setting"),
            ):
                if (b.get(key) or "") != (a.get(key) or "") and a.get(key):
                    bits.append(f"{label} → {a.get(key)}")
            if bits:
                parts.append("occasion (" + ", ".join(bits) + ")")
        elif section == "logistics":
            be = (memory_before.get("logistics") or {}).get("events") or []
            ae = (memory_after.get("logistics") or {}).get("events") or []
            if be != ae:
                parts.append(f"events: {', '.join(be) or 'none'} → {', '.join(ae) or 'none'}")
        elif section == "identity":
            a = memory_after.get("identity") or {}
            parts.append(
                f"names → {a.get('displayName') or ((a.get('clientName') or '') + ' & ' + (a.get('partnerName') or ''))}"
            )
    if not parts:
        return ""
    return "Got it — I've updated " + "; ".join(parts) + "."


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
    from sqlalchemy import select
    from app.models.event_site import EventSite
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


async def _execute_direction_from_embeddings(
    db: AsyncSession,
    session,
    session_id: uuid.UUID,
    stage: str,
    request_id: uuid.UUID,
    *,
    save_planner_message: bool = True,
) -> dict:
    """
    Fast S6 path: embed canonical memory → top event sites → return top 3.
    LLM enrichment is optional; timeouts must not fail the turn.
    """
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

    # Embedding-only by default — do not block the turn on a long Gemma call.
    # Reasons come from curated event_site.profile_json.
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

    suggestions = [
        {"label": opt.get("name", ""), "category": "direction"}
        for opt in options[:3]
    ]

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
        model=telemetry.get("model"),
        latency_ms=telemetry.get("latency_ms"),
        input_tokens=telemetry.get("input_tokens"),
        output_tokens=telemetry.get("output_tokens"),
        validation_status="accepted",
    )

    return _response_dict(
        request_id, session_id, response_source, planner_reply, memory,
        memory_patch=patch,
        updated_version=updated_version,
        stage_decision={"type": final_decision_type, "stage": final_stage},
        suggestions=suggestions,
        artifact_content=artifact_content,
    )


async def _execute_synthesis(
    db: AsyncSession,
    session,
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

    try:
        ai_result, telemetry = await call_llm(messages, stage, EventType.SYNTHESIS_REQUEST.value)
    except AIGatewayError as e:
        error_code = e.code
        telemetry = {"model": settings.OLLAMA_MODEL}

    if error_code or not ai_result:
        await log_ai_turn(
            db, request_id, session_id, stage,
            EventType.SYNTHESIS_REQUEST.value,
            ResponseSource.ERROR.value,
            prompt_family=prompt_family,
            failure_code=error_code or "UNKNOWN",
            validation_status="rejected",
        )
        return _make_error_response(request_id, session_id, stage, memory, error_code or "AI_CALL_FAILED")

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


async def process_conversation_turn(
    db: AsyncSession,
    session_id: uuid.UUID,
    user_message: str,
    stage: str | None = None,
) -> dict:
    """Main pipeline for conversation_turn event type."""
    request_id = uuid.uuid4()

    # 1. Load session
    session = await SessionService.get_session(db, session_id)
    if not session:
        raise ValueError(f"Session {session_id} not found")

    # Backend owns current stage — client must not drive stage transitions
    stage = stage or session.current_stage

    # 2. Load memory
    mem_version = await MemoryService.get_latest_memory(db, session_id)
    if not mem_version:
        raise ValueError(f"No memory for session {session_id}")
    memory = mem_version.memory_json
    memory_before = copy.deepcopy(memory)

    # Middle layer: classify intent before AI (corrections, target section, suggested patch)
    turn_intent = classify_turn_intent(user_message, stage, memory)

    # Special: at s5_brief, "show me directions" triggers direction synthesis
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

    # Client message saved after patch (below) so selectedChips snapshot is accurate

    # 4. Load recent history for prompt (exclude the message just saved)
    all_messages = await SessionService.get_recent_messages(db, session_id, limit=21)
    history_for_prompt = [
        {"role": m.role, "content": m.content_text}
        for m in all_messages[:-1]  # exclude the just-saved client message
    ]

    # 5. Build prompt
    messages = build_conversation_turn_prompt(
        stage=stage,
        memory=memory,
        recent_messages=history_for_prompt[-settings.MAX_HISTORY_MESSAGES:],
        user_message=user_message,
    )

    # 6. Call AI
    telemetry: dict = {}
    ai_result: dict | None = None
    error_code: str | None = None

    try:
        ai_result, telemetry = await call_llm(messages, stage, EventType.CONVERSATION_TURN.value)
    except AIGatewayError as e:
        error_code = e.code
        telemetry = {"model": settings.OLLAMA_MODEL, "http_status": e.http_status}

    if error_code or not ai_result:
        await log_ai_turn(
            db, request_id, session_id, stage,
            EventType.CONVERSATION_TURN.value,
            ResponseSource.ERROR.value,
            prompt_family="conversation_turn",
            model=telemetry.get("model"),
            http_status=telemetry.get("http_status"),
            latency_ms=telemetry.get("latency_ms"),
            validation_status="rejected",
            failure_code=error_code or "UNKNOWN",
        )
        return _make_error_response(request_id, session_id, stage, memory, error_code or "AI_CALL_FAILED")

    # 6b. Sanitize model output (fix invalid stage ids like s6_personality)
    ai_result = sanitize_ai_response(ai_result, stage, turn_intent)

    # 7. Validate AI response
    is_valid, val_error = validate_ai_response(ai_result, stage)
    if not is_valid:
        await log_ai_turn(
            db, request_id, session_id, stage,
            EventType.CONVERSATION_TURN.value,
            ResponseSource.ERROR.value,
            prompt_family="conversation_turn",
            model=telemetry.get("model"),
            latency_ms=telemetry.get("latency_ms"),
            validation_status="rejected",
            failure_code=val_error,
        )
        return _make_error_response(request_id, session_id, stage, memory, f"VALIDATION_FAILED:{val_error}")

    # 8. Enrich + apply memory patch (intent layer + interpreter + AI)
    ai_patch = ai_result.get("memoryPatch", {})
    if turn_intent.suggested_patch:
        ai_patch = merge_patches(turn_intent.suggested_patch, ai_patch)
    patch = enrich_memory_patch(
        stage, user_message, ai_patch, memory
    )
    patch = sanitize_memory_patch(
        patch, stage=stage, message=user_message, memory=memory
    )
    open_questions = ai_result.get("openQuestions", [])
    updated_version = mem_version.version_no
    stale_sections = list(ai_result.get("staleSections", []))

    if patch:
        new_mem_version = await MemoryService.apply_patch(
            db, session, patch,
            request_id=request_id,
            open_questions=open_questions,
            extra_stale=stale_sections,
        )
        memory = new_mem_version.memory_json
        updated_version = new_mem_version.version_no
        stale_sections = new_mem_version.stale_sections

        correction = detect_upstream_correction(patch, memory_before, memory, stage)
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
    else:
        correction = None

    # Backfill canonical vibe when UI chips exist but memory.vibe.primaryVibe is empty
    if stage == StageId.S4_VIBE.value:
        from app.domain.memory_schema import resolve_primary_vibe
        resolved = resolve_primary_vibe(memory)
        if resolved and not (memory.get("vibe") or {}).get("primaryVibe"):
            sync = await MemoryService.apply_patch(
                db, session,
                {"vibe": {"primaryVibe": resolved}},
                request_id=request_id,
            )
            memory = sync.memory_json
            updated_version = sync.version_no

    # Save client message with post-patch chip snapshot
    await SessionService.append_message(
        db,
        session_id=session_id,
        role=MessageRole.CLIENT.value,
        content=user_message,
        message_type=MessageType.CONVERSATION_TURN.value,
        stage=stage,
        source=None,
        request_id=request_id,
        metadata={"selectedChips": build_selected_chips(memory)},
    )

    # 9. Resolve stage decision — backend policy owns final movement
    sd = ai_result.get("stageDecision", {})
    ai_decision_type = sd.get("type", StageDecisionType.STAY.value)
    ai_to_stage = sd.get("stage", stage)

    if patch and not correction:
        correction = detect_upstream_correction(patch, memory_before, memory, stage)

    if correction:
        # Docs Example 3/5: reanchor on current stage + stale downstream
        final_decision_type, final_stage, _reason = resolve_correction_stage_decision(
            correction, stage
        )
        # Merge stale from correction (memory already has them if we applied markers)
        stale_sections = list(set(stale_sections) | set(correction.get("staleSections", [])))
    else:
        # Normal progression — StagePolicy may advance when memory is complete
        final_decision_type, final_stage, _reason = StagePolicy.resolve_final_decision_with_memory(
            ai_decision_type, ai_to_stage, stage, memory,
            open_questions=open_questions,
        )
        # Never let a low-confidence intent block advance
        if (
            turn_intent.is_correction
            and turn_intent.decision_type == StageDecisionType.REANCHOR.value
            and turn_intent.confidence in ("medium", "high")
        ):
            final_decision_type = StageDecisionType.REANCHOR.value
            final_stage = stage

    # 10. Advance / reanchor stage if needed
    if final_stage != stage:
        await SessionService.update_stage(
            db, session,
            new_stage=final_stage,
            decision_type=final_decision_type,
            request_id=request_id,
        )
    elif final_decision_type == StageDecisionType.REANCHOR.value and correction:
        # Record reanchor in history even when stage stays the same
        await SessionService.update_stage(
            db, session,
            new_stage=stage,
            decision_type=final_decision_type,
            request_id=request_id,
            reason_code="upstream_correction_reanchor",
        )

    # 10b. Auto-chain brief synthesis when S4 vibe completes → S5
    artifact_content = None
    if (
        stage == StageId.S4_VIBE.value
        and final_stage == StageId.S5_BRIEF.value
        and not correction
    ):
        brief_result = await _execute_synthesis(
            db, session, session_id, SynthesisType.BRIEF.value,
            StageId.S5_BRIEF.value, request_id, save_planner_message=False,
        )
        if not brief_result.get("errorCode"):
            return brief_result

    # 10c/10d. On S6 corrections → refresh directions (not brief). On S5 → brief only.
    if correction and stage == StageId.S6_DIRECTIONS.value and (
        correction.get("shouldRegenerateDirection")
        or correction.get("shouldRefreshDirectionsOnS6")
    ):
        dir_result = await _execute_synthesis(
            db, session, session_id, SynthesisType.DIRECTION.value,
            StageId.S6_DIRECTIONS.value, request_id, save_planner_message=False,
        )
        if not dir_result.get("errorCode"):
            ack = _summarize_correction_for_reply(correction, memory_before, memory)
            opts = (dir_result.get("artifactContent") or {}).get("directionOptions") or []
            place = (memory.get("occasion") or {}).get("place") or "your celebration"
            dir_result["plannerReply"] = _build_direction_planner_reply(
                opts, place, correction_ack=ack,
            )
            dir_result["staleSections"] = stale_sections
            dir_result["memoryPatch"] = {**(dir_result.get("memoryPatch") or {}), **(patch or {})}
            dir_result["stageDecision"] = {
                "type": StageDecisionType.REANCHOR.value,
                "stage": StageId.S6_DIRECTIONS.value,
            }
            await SessionService.append_message(
                db, session_id=session_id,
                role=MessageRole.PLANNER.value,
                content=dir_result["plannerReply"],
                message_type=MessageType.SYNTHESIS_REQUEST.value,
                stage=StageId.S6_DIRECTIONS.value,
                source=dir_result.get("responseSource", ResponseSource.RULE.value),
                request_id=request_id,
                metadata={
                    "selectedChips": build_selected_chips(memory),
                    "artifactType": "direction",
                    "artifactContent": dir_result.get("artifactContent"),
                    "correctionAck": True,
                },
            )
            return dir_result

    if correction and correction.get("shouldRegenerateBrief"):
        brief_result = await _execute_synthesis(
            db, session, session_id, SynthesisType.BRIEF.value,
            stage, request_id, save_planner_message=False,
        )
        if not brief_result.get("errorCode"):
            ack = _summarize_correction_for_reply(correction, memory_before, memory)
            brief_text = (brief_result.get("artifactContent") or {}).get("briefText") or ""
            # Prefer reflective brief + explicit change acknowledgment
            refreshed = brief_result.get("plannerReply") or ""
            if brief_text:
                refreshed = f"{ack}\n\n{brief_text}"
            else:
                refreshed = f"{ack} {refreshed}".strip()
            brief_result["plannerReply"] = refreshed
            brief_result["staleSections"] = stale_sections
            brief_result["memoryPatch"] = {**(brief_result.get("memoryPatch") or {}), **(patch or {})}
            brief_result["stageDecision"] = {
                "type": StageDecisionType.REANCHOR.value,
                "stage": stage,
            }
            # Persist the combined planner message
            await SessionService.append_message(
                db, session_id=session_id,
                role=MessageRole.PLANNER.value,
                content=refreshed,
                message_type=MessageType.SYNTHESIS_REQUEST.value,
                stage=stage,
                source=brief_result.get("responseSource", ResponseSource.OPENAI.value),
                request_id=request_id,
                metadata={
                    "selectedChips": build_selected_chips(memory),
                    "artifactType": "brief",
                    "artifactContent": brief_result.get("artifactContent"),
                    "correctionAck": True,
                },
            )
            return brief_result

    # 11. Backend-assembled UI hints (chips for current or next stage)
    suggestion_stage = final_stage if final_stage != stage else stage
    suggestions = build_ui_suggestions(
        stage,
        memory,
        ai_result.get("suggestions", []),
        for_stage=suggestion_stage,
    )
    suggestions = [
        s for s in suggestions
        if isinstance(s, dict)
        and s.get("label")
        and not re.search(r"guestcount|_guests$|_estimate", str(s.get("label", "")), re.I)
    ]

    # 12. Save planner reply
    planner_reply = ai_result.get("plannerReply", "")

    # S6 → S7: after picking a direction, ask about events (not aesthetics)
    direction_selected_this_turn = bool(
        (patch or {}).get("direction", {}).get("selectedDirectionId")
    )
    direction_selected = direction_selected_this_turn or bool(
        (memory.get("direction") or {}).get("selectedDirectionId")
    )
    if (
        final_stage == StageId.S7_EVENTS.value
        and direction_selected
        and stage in (StageId.S6_DIRECTIONS.value, StageId.S7_EVENTS.value)
    ):
        dname = (memory.get("direction") or {}).get("selectedDirectionName") or "that direction"
        events = memory.get("logistics", {}).get("events") or []
        if not events or direction_selected_this_turn:
            planner_reply = (
                f"Wonderful — {dname} is a great fit! "
                f"Which wedding functions would you like to include? "
                f"You can pick from the suggestions or tell me in your own words."
            )

    # S7: keep focus on events until confirmed
    if final_stage == StageId.S7_EVENTS.value or stage == StageId.S7_EVENTS.value:
        if not (memory.get("logistics") or {}).get("eventsConfirmed"):
            events = (memory.get("logistics") or {}).get("events") or []
            if events:
                planner_reply = (
                    f"So far I have {', '.join(events)} — "
                    f"would you like to add any other functions, or shall we lock this in?"
                )

    # Acknowledge early personality signals when entering S3
    if (
        final_stage == StageId.S3_PERSONALITY.value
        and stage == StageId.S2_BASICS.value
    ):
        early = memory.get("earlySignals") or {}
        early_p = early.get("personality") or []
        if early_p and not early.get("acknowledged"):
            planner_reply = (
                f"Noted earlier that you mentioned {', '.join(early_p)} — "
                f"want to keep those and add more, or refine them? "
                + planner_reply
            )

    if correction:
        ack = _summarize_correction_for_reply(correction, memory_before, memory)
        if ack:
            # After upstream correction, ask a question for the CURRENT stage
            stage_q = {
                StageId.S3_PERSONALITY.value: "Want to refine your personality tags further?",
                StageId.S4_VIBE.value: "Does the vibe still feel right, or want to adjust it?",
                StageId.S6_DIRECTIONS.value: "Take a look at the refreshed directions — which feels closest?",
                StageId.S7_EVENTS.value: "Which functions should we include?",
                StageId.S8_GUESTS.value: "What guest counts are you thinking for each event?",
                StageId.S9_BUDGET.value: "What budget range feels comfortable?",
                StageId.S10_VENDORS.value: "Any vendor priorities to add?",
            }.get(final_stage, "")
            planner_reply = f"{ack} {stage_q or planner_reply}".strip()
        else:
            planner_reply = planner_reply

    await SessionService.append_message(
        db,
        session_id=session_id,
        role=MessageRole.PLANNER.value,
        content=planner_reply,
        message_type=MessageType.CONVERSATION_TURN.value,
        stage=final_stage,
        source=ResponseSource.OPENAI.value,
        request_id=request_id,
        metadata={"selectedChips": build_selected_chips(memory)},
    )

    # 13. Log
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
        memory_patch=patch,
        updated_version=updated_version,
        stage_decision={"type": final_decision_type, "stage": final_stage},
        stale_sections=stale_sections,
        open_questions=open_questions,
        suggestions=suggestions,
        artifact_content=artifact_content,
    )


def _is_direction_request(message: str) -> bool:
    msg = message.lower()
    return any(p in msg for p in (
        "show me direction", "show directions", "see direction",
        "directions", "design direction",
    ))


async def process_synthesis_request(
    db: AsyncSession,
    session_id: uuid.UUID,
    synthesis_type: str | None = None,
    stage: str | None = None,
) -> dict:
    """Pipeline for synthesis_request: brief, direction, or summary."""
    request_id = uuid.uuid4()

    session = await SessionService.get_session(db, session_id)
    if not session:
        raise ValueError(f"Session {session_id} not found")

    stage = stage or session.current_stage
    mem_version = await MemoryService.get_latest_memory(db, session_id)
    if not mem_version:
        raise ValueError(f"No memory for session {session_id}")
    memory = mem_version.memory_json

    # Sync committed vibe chips into canonical memory before inferring synthesis
    if stage == StageId.S4_VIBE.value:
        from app.domain.memory_schema import resolve_primary_vibe
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
        from app.domain.memory_schema import resolve_primary_vibe
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

    # S4 explicit brief request → run brief and land on s5_brief
    run_stage = stage
    if stage == StageId.S4_VIBE.value and synthesis_type == SynthesisType.BRIEF.value:
        run_stage = StageId.S5_BRIEF.value

    return await _execute_synthesis(
        db, session, session_id, synthesis_type, run_stage, request_id,
    )
