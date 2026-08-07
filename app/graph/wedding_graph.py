"""
Wedding AI Pipeline — Sequential async pipeline for conversation turns.

Simplified flow for process_conversation_turn:
  1. Load session + process images
  2. Data Extraction (AI Call 1)
  3. Apply extraction patch
  4. Auto-confirm early signals for budget (s8→s9 transition)
  5. Response Planning (AI Call 2) — agent decides stageDecision
  6. Backend gate: validate AI's stageDecision with is_stage_complete()
  7. Auto-synthesis chains (S4→S5 brief)
  8. Persist + return response
"""
from __future__ import annotations

import re
import uuid

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
from app.services.policy.planner_reply_policy import align_planner_reply
from app.services.policy.stage_policy import StagePolicy, check_budget_feasibility
from app.services.session.memory_service import MemoryService
from app.services.session.session_service import SessionService
from app.services.ui.observability import log_ai_turn
from app.services.ui.ui_hints import build_ui_suggestions
from app.utils.ai_response_validators import sanitize_ai_response, validate_ai_response

# Imports from extracted modules
from app.graph.response_builder import make_error_response, response_dict
from app.graph.direction_service import is_direction_request
from app.graph.synthesis_service import (
    execute_synthesis, seed_tentative_guest_counts,
    process_synthesis_request,
)

# Backward-compatible aliases
_make_error_response = make_error_response
_response_dict = response_dict
_process_synthesis_request = process_synthesis_request


# ─────────────────────────────────────────────────────────────────────────────
# S1 — System-handled, no AI call
# ─────────────────────────────────────────────────────────────────────────────

async def process_s1_names(
    db: AsyncSession,
    groom_name: str,
    bride_name: str,
) -> dict:
    """S1 — Creates session, seeds identity, advances to S2."""
    request_id = uuid.uuid4()
    session, memory_v0 = await SessionService.create_session(db, groom_name, bride_name)

    await SessionService.update_stage(
        db, session,
        new_stage=StageId.S2_BASICS.value,
        decision_type=StageDecisionType.ADVANCE.value,
        request_id=request_id,
    )

    welcome = (
        f"Lovely to meet you both, {groom_name} and {bride_name}!"
        f"What wedding destination are you dreaming of, and what time of year are you planning for?"
    )
    await SessionService.append_message(
        db, session_id=session.id,
        role=MessageRole.PLANNER.value, content=welcome,
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

def _merge_early_signals(existing: dict, new: dict) -> dict:
    """Merge two earlySignals dicts. Lists deduped, dicts merged."""
    merged = dict(existing)
    for key in ("personality", "vibe", "events"):
        new_list = new.get(key) or []
        if new_list:
            existing_list = merged.get(key) or []
            merged[key] = list(dict.fromkeys(existing_list + new_list))
    for key in ("budget", "vendors"):
        new_dict = new.get(key) or {}
        if new_dict:
            merged[key] = {**(merged.get(key) or {}), **new_dict}
    new_gc = new.get("guestCount")
    if new_gc and isinstance(new_gc, (int, float)) and new_gc > 0:
        existing_gc = merged.get("guestCount")
        if not existing_gc or new_gc > existing_gc:
            merged["guestCount"] = int(new_gc)
    return merged


async def process_conversation_turn(
    db: AsyncSession,
    session_id: uuid.UUID,
    user_message: str,
    images: list[str] | None = None,
) -> dict:
    """
    Main pipeline for conversation turns.

    Phase 1: Load session + process images
    Phase 2: Data Extraction (AI Call 1)
    Phase 3: Apply extraction patch + early signals
    Phase 4: Response Planning (AI Call 2) — agent decides everything
    Phase 5: Backend gate + resolve stage
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
    version_no = mem_version.version_no

    # Process images (if any)
    image_context = ""
    if images:
        vis_patch, image_context, _vis_telemetry = await analyse_images(images, stage, memory)
        if vis_patch:
            vis_mem = await MemoryService.apply_patch(db, session, vis_patch, request_id=request_id)
            memory = vis_mem.memory_json
            version_no = vis_mem.version_no

    # Load recent messages
    all_messages = await SessionService.get_recent_messages(db, session_id, limit=21)
    recent_messages = [{"role": m.role, "content": m.content_text} for m in all_messages]

    # S5 / S6 direction shortcut
    if (stage in (StageId.S5_BRIEF.value, StageId.S6_DIRECTIONS.value)) and is_direction_request(user_message):
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

    # ── Phase 3: Apply Extraction Patch ──────────────────────────────────────
    extraction_patch: dict = extraction.validated_patch or {}
    if extraction_patch and not extraction.is_meta():
        # Compute displayName
        if "identity" in extraction_patch:
            _ip = dict(extraction_patch["identity"])
            _groom = (_ip.get("groomName") or "").strip()
            _bride = (_ip.get("brideName") or "").strip()
            _names = [n for n in [_groom, _bride] if n]
            if _names:
                _ip["displayName"] = " & ".join(_names)
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

        # Update session names if identity changed
        if "identity" in extraction_patch:
            _ip = extraction_patch["identity"]
            await SessionService.update_names(
                db, session,
                groom_name=_ip.get("groomName") or None,
                bride_name=_ip.get("brideName") or None,
            )

    # Merge early signals into memory
    if not extraction.is_meta() and extraction.has_early_signals():
        combined_early = _merge_early_signals(
            memory.get("earlySignals") or {},
            extraction.early_signals,
        )
        if any(v for v in combined_early.values() if v):
            es_mem = await MemoryService.apply_patch(
                db, session, {"earlySignals": combined_early}, request_id=request_id
            )
            memory = es_mem.memory_json
            version_no = es_mem.version_no

    # Auto-confirm early signals budget when entering s9
    is_s8_advancing = (
        stage == StageId.S8_GUESTS.value
        and StagePolicy.is_stage_complete(StageId.S8_GUESTS.value, memory)
    )
    if stage == StageId.S9_BUDGET.value or is_s8_advancing:
        logistics_budget = memory.get("logistics", {}).get("budget") or {}
        early_budget = memory.get("earlySignals", {}).get("budget") or {}
        if not logistics_budget.get("range") and early_budget.get("range"):
            auto_patch = {"logistics": {"budget": early_budget}}
            new_mem = await MemoryService.apply_patch(db, session, auto_patch, request_id=request_id)
            memory = new_mem.memory_json
            version_no = new_mem.version_no

    # Seed tentative guest counts if entering S8
    if stage == StageId.S8_GUESTS.value:
        memory_seeded = seed_tentative_guest_counts(memory)
        if memory_seeded != memory:
            new_mem = await MemoryService.apply_patch(
                db, session, {"logistics": memory_seeded.get("logistics") or {}}, request_id=request_id
            )
            memory = new_mem.memory_json
            version_no = new_mem.version_no

    # S4 vibe sync
    if stage == StageId.S4_VIBE.value:
        resolved = resolve_primary_vibe(memory)
        if resolved and not (memory.get("vibe") or {}).get("primaryVibe"):
            sync = await MemoryService.apply_patch(
                db, session, {"vibe": {"primaryVibe": resolved}}, request_id=request_id,
            )
            memory = sync.memory_json
            version_no = sync.version_no

    # ── Compute budget feasibility for prompt ─────────────────────────────────
    budget_feasibility = "(not applicable)"
    if stage in (StageId.S8_GUESTS.value, StageId.S9_BUDGET.value) or is_s8_advancing:
        is_feasible, est_cost_str, _ = check_budget_feasibility(memory)
        if not is_feasible and est_cost_str:
            budget_feasibility = f"INFEASIBLE — suggested minimum: {est_cost_str}. User must increase budget or adjust requirements."
        elif is_feasible:
            budget_feasibility = "Budget is feasible for the selected destination, events, and guest counts."

    # ── Phase 4: Response Planning (AI Call 2) ────────────────────────────────
    messages = build_response_planner_prompt(
        stage=stage,
        memory=memory,
        recent_messages=recent_messages,
        extraction_summary=extraction.extraction_summary,
        extraction_patch=extraction_patch,
        user_message=user_message,
        image_context=image_context,
        budget_feasibility=budget_feasibility,
    )

    ai_result: dict | None = None
    telemetry: dict = {}
    error_code: str | None = None

    try:
        ai_result, telemetry = await call_llm(messages, stage, EventType.CONVERSATION_TURN.value)
    except AIGatewayError as e:
        error_code = e.code
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
        return make_error_response(request_id, session_id, stage, memory, error_code or "AI_CALL_FAILED")

    # ── Sanitize AI response ──────────────────────────────────────────────────
    ai_result = sanitize_ai_response(ai_result, stage)
    meta_intent = extraction.meta_intent

    # Meta turn enforcement
    if meta_intent == "gibberish":
        ai_result["memoryPatch"] = {}
        ai_result["stageDecision"] = {
            "type": StageDecisionType.REQUEST_CLARIFICATION.value, "stage": stage,
        }
        if not (ai_result.get("plannerReply") or "").strip():
            import random
            ai_result["plannerReply"] = random.choice([
                "I didn't quite catch that! Could you please clarify?",
                "Hmm, I'm not sure I understood — could you rephrase?",
                "I want to make sure I capture your vision — could you tell me more?",
            ])
    elif meta_intent in ("help", "more_suggestions"):
        ai_result["memoryPatch"] = {}
        ai_result["stageDecision"] = {"type": StageDecisionType.STAY.value, "stage": stage}

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
        return make_error_response(request_id, session_id, stage, memory, f"VALIDATION_FAILED:{val_error}")

    # ── Phase 5: Apply AI memory patch + resolve stage ────────────────────────
    ai_patch = ai_result.get("memoryPatch") or {}
    stale_sections: list = list(ai_result.get("staleSections") or [])
    open_questions: list = ai_result.get("openQuestions") or []

    # Apply additional AI patch (fields not already committed by extraction)
    additional_patch: dict = {}
    for _k, _v in ai_patch.items():
        if _k not in extraction_patch and _k != "earlySignals":
            additional_patch[_k] = _v

    if additional_patch:
        new_mem_version = await MemoryService.apply_patch(
            db, session, additional_patch,
            request_id=request_id,
            open_questions=open_questions,
            extra_stale=stale_sections,
        )
        memory = new_mem_version.memory_json
        version_no = new_mem_version.version_no
        stale_sections = new_mem_version.stale_sections

    # Persist client message
    _client_meta: dict = {"selectedChips": build_selected_chips(memory)}
    if images:
        _client_meta["imageCount"] = len(images)
    await SessionService.append_message(
        db, session_id=session_id,
        role=MessageRole.CLIENT.value, content=user_message,
        message_type=MessageType.CONVERSATION_TURN.value,
        stage=stage, source=None,
        request_id=request_id,
        metadata=_client_meta,
    )

    # ── Backend gate: resolve final stage decision ────────────────────────────
    sd = ai_result.get("stageDecision") or {"type": StageDecisionType.STAY.value, "stage": stage}
    ai_decision_type = sd.get("type", StageDecisionType.STAY.value)
    ai_to_stage = sd.get("stage", stage)

    if extraction.is_meta():
        final_decision_type = (
            StageDecisionType.REQUEST_CLARIFICATION.value
            if meta_intent == "gibberish"
            else StageDecisionType.STAY.value
        )
        final_stage = stage
    else:
        final_decision_type, final_stage, _reason = StagePolicy.resolve_final_decision_with_memory(
            ai_decision_type, ai_to_stage, stage, memory,
        )

    if final_stage != stage:
        await SessionService.update_stage(
            db, session,
            new_stage=final_stage,
            decision_type=final_decision_type,
            request_id=request_id,
        )

    # ── Auto-synthesis chains ──────────────────────────────────────────────────
    synthesis_result = None
    combined_patch = {**extraction_patch, **additional_patch}

    # 1. S4→S5: Auto-brief synthesis when advancing to S5 for the first time
    is_s4_to_s5 = (
        stage == StageId.S4_VIBE.value
        and final_stage == StageId.S5_BRIEF.value
        and StagePolicy.is_stage_complete(StageId.S3_PERSONALITY.value, memory)
        and StagePolicy.is_stage_complete(StageId.S4_VIBE.value, memory)
    )
    if is_s4_to_s5:
        brief_res = await execute_synthesis(
            db, session, session_id, SynthesisType.BRIEF.value,
            StageId.S5_BRIEF.value, request_id, save_planner_message=False,
        )
        if not brief_res.get("errorCode"):
            synthesis_result = brief_res

    if synthesis_result:
        return synthesis_result

    # 2. Silent Brief update at S5+: when S1-S4 fields (names, place, date, vibe, personality) are updated
    _S5_PLUS = {
        StageId.S5_BRIEF.value, StageId.S6_DIRECTIONS.value, StageId.S7_EVENTS.value,
        StageId.S8_GUESTS.value, StageId.S9_BUDGET.value, StageId.S10_VENDORS.value,
        StageId.S11_SUMMARY.value,
    }
    is_at_s5_plus = (stage in _S5_PLUS or final_stage in _S5_PLUS)
    has_early_section_update = any(
        sec in combined_patch for sec in ("identity", "occasion", "personality", "vibe")
    )

    if is_at_s5_plus and has_early_section_update:
        try:
            brief_res = await execute_synthesis(
                db, session, session_id, SynthesisType.BRIEF.value,
                stage, request_id, save_planner_message=False,
            )
            if brief_res and brief_res.get("updatedMemoryVersion"):
                latest_mem = await MemoryService.get_latest_memory(db, session_id)
                if latest_mem:
                    memory = latest_mem.memory_json
                    version_no = latest_mem.version_no
        except Exception as _be:
            import logging
            logging.getLogger(__name__).warning("Silent brief update failed: %s", _be)

    # ── Build & Return Response ────────────────────────────────────────────────
    _CHIP_STAGES = frozenset({
        StageId.S2_BASICS.value, StageId.S3_PERSONALITY.value,
        StageId.S4_VIBE.value, StageId.S7_EVENTS.value,
    })
    effective_stage = final_stage if final_stage != stage else stage
    if effective_stage not in _CHIP_STAGES or meta_intent == "gibberish":
        suggestions = []
    else:
        suggestions = build_ui_suggestions(
            stage, memory, ai_result.get("suggestions", []),
            for_stage=effective_stage,
        )
        suggestions = [
            s for s in suggestions
            if isinstance(s, str) and s.strip()
            and not re.search(r"guestcount|_guests$|_estimate", s, re.I)
        ]

    planner_reply = align_planner_reply(
        ai_reply=ai_result.get("plannerReply", "") or "",
        to_stage=final_stage,
    )

    await SessionService.append_message(
        db, session_id=session_id,
        role=MessageRole.PLANNER.value, content=planner_reply,
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

    combined_patch = {**extraction_patch, **additional_patch}
    return response_dict(
        request_id, session_id, ResponseSource.OPENAI.value, planner_reply, memory,
        memory_patch=combined_patch,
        updated_version=version_no,
        stage_decision={"type": final_decision_type, "stage": final_stage},
        stale_sections=stale_sections,
        open_questions=open_questions,
        suggestions=suggestions,
    )
