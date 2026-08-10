"""
Synthesis Service — brief, direction, and summary synthesis pipelines.

Extracted from app/graph/wedding_graph.py. Handles synthesis execution,
guest count seeding, correction acknowledgment, and the public synthesis entry point.
"""
from __future__ import annotations

import re
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.domain.enums import (
    ArtifactStatus, ArtifactType, EventType, MessageRole, MessageType,
    ResponseSource, StageDecisionType, StageId, SynthesisType,
)
from app.domain.memory_schema import resolve_primary_vibe
from app.graph.direction_service import execute_direction_from_embeddings
from app.graph.response_builder import make_error_response, response_dict
from app.models.generated_artifact import GeneratedArtifact
from app.services.ai.ai_gateway import AIGatewayError, call_llm
from app.services.ai.prompt_builder import build_brief_synthesis_prompt, build_final_summary_prompt
from app.utils.ai_response_validators import validate_synthesis_response
from app.services.policy.stage_policy import StagePolicy
from app.services.session.memory_service import MemoryService
from app.services.session.session_service import SessionService
from app.services.ui.observability import log_ai_turn
from app.services.ui.ui_hints import build_ui_suggestions


def seed_tentative_guest_counts(memory: dict) -> dict:
    """Return memory unchanged — guest counts must be explicitly provided by the user."""
    return memory
    pref_str = str((memory.get("occasion") or {}).get("guestCountPreference") or "")
    match = re.search(r'\b(\d+)\b', pref_str)
    base_count = int(match.group(1)) if match else None

    BENCHMARKS = {
        "mehendi": 80,
        "mehndi": 80,
        "haldi": 60,
        "mayun": 60,
        "sangeet": 250,
        "cocktail": 200,
        "musical night": 200,
        "wedding": 450,
        "barat": 450,
        "ceremony": 450,
        "nikkah": 300,
        "reception": 350,
        "walima": 350,
    }

    updated = False
    for ev in events:
        if counts.get(ev) is None or not isinstance(counts.get(ev), (int, float)) or counts.get(ev, 0) <= 0:
            ev_lower = str(ev).lower()
            est = None
            for key, val in BENCHMARKS.items():
                if key in ev_lower:
                    est = val
                    break
            if est is None:
                est = 200

            if base_count and base_count > 0:
                if base_count < 200:
                    est = min(est, base_count)
                elif base_count > 1000:
                    est = int(est * (base_count / 500))

            counts[ev] = est
            updated = True

    if updated:
        logistics["guestCounts"] = counts
        memory["logistics"] = logistics

    return memory


def summarize_correction_for_reply(
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
                parts.append("vibe notes updated")
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


async def execute_synthesis(
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
        return await execute_direction_from_embeddings(
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
        return make_error_response(
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
        return make_error_response(
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
        return make_error_response(request_id, session_id, stage, memory, f"VALIDATION_FAILED:{val_error}")

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

    return response_dict(
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
            return make_error_response(
                request_id, session_id, stage, memory,
                "VIBE_INCOMPLETE",
                "Confirm your primary vibe with a conversation_turn first — "
                "then the brief generates automatically (or call synthesis_request again).",
            )
        return make_error_response(
            request_id, session_id, stage, memory,
            f"CANNOT_INFER_SYNTHESIS_TYPE:{stage}",
            "Synthesis is available on s5_brief (brief/directions), s6_directions, "
            "and s11_summary — or on s4_vibe once vibe is confirmed.",
        )

    run_stage = stage
    if stage == StageId.S4_VIBE.value and synthesis_type == SynthesisType.BRIEF.value:
        run_stage = StageId.S5_BRIEF.value

    return await execute_synthesis(
        db, session, session_id, synthesis_type, run_stage, request_id,
    )
