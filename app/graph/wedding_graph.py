"""
Wedding AI Pipeline — Sequential async pipeline for conversation turns.

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

Refactored: response helpers → response_builder.py, direction flow → direction_service.py,
synthesis flow → synthesis_service.py.
"""

from __future__ import annotations

import copy
import re
import uuid
from typing import Any


from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.domain.enums import (
    EventType, MessageRole, MessageType,
    ResponseSource, StageDecisionType, StageId, SynthesisType,
)
from app.domain.memory_schema import (
    build_planner_notes_view, build_selected_chips, resolve_primary_vibe,
)

from app.services.ai.ai_gateway import AIGatewayError, call_llm
from app.services.ai.data_extractor import extract_and_validate
from app.services.ai.image_service import analyse_images
from app.services.ai.prompt_builder import build_response_planner_prompt
from app.services.policy.context_builder import build_turn_context, merge_early_signals
from app.services.policy.correction_policy import (
    apply_stale_artifact_markers,
    detect_upstream_correction,
    resolve_correction_stage_decision,
)
from app.services.policy.planner_reply_policy import align_planner_reply
from app.utils.ai_response_validators import sanitize_ai_response, validate_ai_response
from app.services.policy.stage_policy import StagePolicy
from app.services.session.memory_service import MemoryService
from app.services.session.session_service import SessionService
from app.services.ui.observability import log_ai_turn
from app.services.ui.ui_hints import build_ui_suggestions

# ─────────────────────────────────────────────────────────────────────────────
# Imports from extracted modules
# ─────────────────────────────────────────────────────────────────────────────
from app.graph.response_builder import (
    make_error_response,
    response_dict,
)
from app.graph.direction_service import (
    is_direction_request,
    build_direction_planner_reply,
)
from app.graph.synthesis_service import (
    execute_synthesis,
    seed_tentative_guest_counts,
    summarize_correction_for_reply,
    # Re-export for backward compatibility
    process_synthesis_request,
)

# Backward-compatible aliases (internal callers used underscore-prefixed names)
_extract_brief_artifact_if_present = None  # No longer needed — use response_builder directly
_make_error_response = make_error_response
_response_dict = response_dict
_is_direction_request = is_direction_request
_build_direction_planner_reply = build_direction_planner_reply
_execute_synthesis = execute_synthesis
_summarize_correction_for_reply = summarize_correction_for_reply


# ─────────────────────────────────────────────────────────────────────────────
# Primary Workflows
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

    # S5 / S6 direction shortcut (user explicitly asks for directions or alternative directions)
    if (stage == StageId.S5_BRIEF.value or stage == StageId.S6_DIRECTIONS.value) and is_direction_request(user_message):
        await SessionService.append_message(
            db, session_id=session_id,
            role=MessageRole.CLIENT.value, content=user_message,
            message_type=MessageType.SYNTHESIS_REQUEST.value,
            stage=stage, source=None, request_id=request_id,
            metadata={"selectedChips": build_selected_chips(memory)},
        )
        return await execute_synthesis(
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
                db, session, extraction_patch,
                request_id=request_id,
                is_correction=(extraction.meta_intent == "correction"),
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

    # Seed tentative guest counts if entering/on S8 and guestCounts is empty
    if stage == StageId.S8_GUESTS.value or (ctx and ctx.stage_decision and ctx.stage_decision.get("stage") == StageId.S8_GUESTS.value):
        memory_seeded = seed_tentative_guest_counts(memory)
        if memory_seeded != memory:
            new_mem = await MemoryService.apply_patch(db, session, memory_seeded.get("logistics") or {}, request_id=request_id)
            memory = new_mem.memory_json
            updated_version = new_mem.version_no

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
        return make_error_response(
            request_id, session_id, stage, memory,
            error_code or "AI_CALL_FAILED",
        )

    # ── Sanitize & enforce meta-intent constraints ─────────────────────────────
    ai_result = sanitize_ai_response(ai_result, stage)
    meta_intent = ctx.meta_intent

    if meta_intent == "gibberish":
        ai_result["memoryPatch"] = {}
        if stage == StageId.S5_BRIEF.value:
            ai_result["stageDecision"] = {
                "type": StageDecisionType.STAY.value,
                "stage": stage,
            }
            if not (ai_result.get("plannerReply") or "").strip():
                ai_result["plannerReply"] = (
                    "Everything is saved in your wedding vision brief right below! Whenever you're ready, tap 'Show me directions' to explore design concepts."
                )
        else:
            ai_result["stageDecision"] = {
                "type": StageDecisionType.REQUEST_CLARIFICATION.value,
                "stage": stage,
            }
            if not (ai_result.get("plannerReply") or "").strip():
                import random
                _fallbacks = [
                    "I didn't quite catch that! Could you please clarify your preference?",
                    "Hmm, I'm not sure I understood that correctly! Could you rephrase your thoughts?",
                    "I want to make sure I capture your exact vision — could you tell me a bit more?",
                ]
                ai_result["plannerReply"] = random.choice(_fallbacks)
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
        return make_error_response(
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
            is_correction=(meta_intent == "correction"),
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
    elif correction:
        final_decision_type, final_stage, _reason = resolve_correction_stage_decision(
            correction, stage
        )
        stale_sections = list(set(stale_sections) | set(correction.get("staleSections", [])))
        if final_decision_type != StageDecisionType.JUMP.value and meta_intent == "correction" and not StagePolicy.is_stage_complete(stage, memory):
            final_decision_type = StageDecisionType.REANCHOR.value
            final_stage = stage
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
        brief_res = await execute_synthesis(
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
        dir_res = await execute_synthesis(
            db, session, session_id, SynthesisType.DIRECTION.value,
            StageId.S6_DIRECTIONS.value, request_id, save_planner_message=False,
        )
        if not dir_res.get("errorCode"):
            ack = summarize_correction_for_reply(correction, memory_before, memory)
            opts = (dir_res.get("artifactContent") or {}).get("directionOptions") or []
            place = (memory.get("occasion") or {}).get("place") or "your celebration"
            dir_res["plannerReply"] = build_direction_planner_reply(opts, place, correction_ack=ack)
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
        brief_res = await execute_synthesis(
            db, session, session_id, SynthesisType.BRIEF.value,
            stage, request_id, save_planner_message=False,
        )
        if not brief_res.get("errorCode"):
            ack = summarize_correction_for_reply(correction, memory_before, memory)
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
        StageId.S2_BASICS.value,
        StageId.S3_PERSONALITY.value,
        StageId.S4_VIBE.value,
        StageId.S7_EVENTS.value,
    })
    effective_stage = final_stage if final_stage != stage else stage
    if effective_stage not in _CHIP_STAGES or meta_intent == "gibberish":
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
            if isinstance(s, str) and s.strip()
            and not re.search(r"guestcount|_guests$|_estimate", s, re.I)
        ]



    correction_ack = ""
    if correction:
        correction_ack = summarize_correction_for_reply(correction, memory_before, memory)

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

    return response_dict(
        request_id, session_id, ResponseSource.OPENAI.value, planner_reply, memory,
        memory_patch=combined_patch,
        updated_version=updated_version,
        stage_decision={"type": final_decision_type, "stage": final_stage},
        stale_sections=stale_sections,
        open_questions=open_questions,
        suggestions=suggestions,
    )
