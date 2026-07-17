"""
Stage transitions — validates allowed stage movements and resolves the final,
backend-owned stage decision after a memory patch is applied.
"""
from __future__ import annotations

from app.domain.enums import (
    ALLOWED_TRANSITIONS,
    StageDecisionType,
    StageId,
    SynthesisType,
)
from app.domain.stages.completion import is_stage_complete


def validate_transition(from_stage: str, to_stage: str, decision_type: str) -> tuple[bool, str | None]:
    """
    Returns (is_valid, error_reason).
    Backend uses this to reject AI-proposed stage jumps that violate policy.
    """
    try:
        from_s = StageId(from_stage)
        to_s = StageId(to_stage)
    except ValueError as e:
        return False, f"Unknown stage: {e}"

    allowed = ALLOWED_TRANSITIONS.get(from_s, set())
    if to_s not in allowed:
        return False, f"Transition {from_stage}→{to_stage} not allowed"

    if decision_type == StageDecisionType.STAY.value and from_s != to_s:
        return False, "STAY decision must keep same stage"

    # REANCHOR must stay on current stage
    if decision_type == StageDecisionType.REANCHOR.value:
        if from_s == to_s:
            return True, None
        return False, "REANCHOR must keep same stage"

    # REQUEST_CLARIFICATION stays on current stage
    if decision_type == StageDecisionType.REQUEST_CLARIFICATION.value:
        if from_s == to_s:
            return True, None
        return False, "REQUEST_CLARIFICATION must keep same stage"

    # JUMP may go to any earlier or same stage (corrections from later stages)
    if decision_type == StageDecisionType.JUMP.value:
        order = StageId.ordered()
        try:
            from_idx = order.index(from_s)
            to_idx = order.index(to_s)
        except ValueError:
            return False, "Unknown stage in JUMP"
        if to_idx <= from_idx:
            return True, None
        return False, f"JUMP cannot go forward from {from_stage} to {to_stage}"

    if decision_type == StageDecisionType.ADVANCE.value:
        expected_next = from_s.next_stage()
        if to_s != expected_next:
            return False, f"ADVANCE must go to {expected_next}, not {to_s}"

    return True, None


def resolve_final_decision_with_memory(
    ai_decision_type: str,
    ai_to_stage: str,
    current_stage: str,
    memory: dict,
    *,
    open_questions: list | None = None,
) -> tuple[str, str, str | None]:
    """
    Backend-owned final stage decision after memory patch is applied.
    Returns (decision_type, to_stage, reason_code).
    """
    is_valid, _ = validate_transition(
        current_stage, ai_to_stage, ai_decision_type
    )

    # Explicit jump (correction to earlier stage)
    if ai_decision_type == StageDecisionType.JUMP.value:
        jump_ok, _ = validate_transition(
            current_stage, ai_to_stage, StageDecisionType.JUMP.value
        )
        if jump_ok:
            return ai_decision_type, ai_to_stage, "jump_correction"

    # Re-anchor stays on current stage but reframes
    if ai_decision_type == StageDecisionType.REANCHOR.value:
        return StageDecisionType.REANCHOR.value, current_stage, "reanchor"

    # Clarification: always honor — never auto-advance past agent rejection
    if ai_decision_type == StageDecisionType.REQUEST_CLARIFICATION.value:
        return (
            StageDecisionType.REQUEST_CLARIFICATION.value,
            current_stage,
            "need_clarification",
        )

    # Do not advance while the model still has open questions for this stage
    if open_questions and ai_decision_type == StageDecisionType.ADVANCE.value:
        return StageDecisionType.STAY.value, current_stage, "open_questions_block_advance"

    # Memory-complete stages advance automatically (backend owns movement)
    # S5 brief is advanced via auto-synthesis after S4, not conversation_turn
    if current_stage == StageId.S5_BRIEF.value:
        if is_valid and ai_decision_type == StageDecisionType.STAY.value:
            return ai_decision_type, ai_to_stage, "ai_stay"
        return StageDecisionType.STAY.value, current_stage, "awaiting_brief_synthesis"

    if is_stage_complete(current_stage, memory):
        try:
            next_stage = StageId(current_stage).next_stage()
        except ValueError:
            next_stage = None
        if next_stage:
            ok, _ = validate_transition(
                current_stage,
                next_stage.value,
                StageDecisionType.ADVANCE.value,
            )
            if ok:
                return (
                    StageDecisionType.ADVANCE.value,
                    next_stage.value,
                    "memory_complete",
                )

    # Never honor model "advance" when this stage has a completeness check and fails it.
    # (Prevents S2 skipping on vague timing / early personality signals.)
    _gated = {
        StageId.S2_BASICS.value,
        StageId.S3_PERSONALITY.value,
        StageId.S4_VIBE.value,
        StageId.S6_DIRECTIONS.value,
        StageId.S7_EVENTS.value,
        StageId.S8_GUESTS.value,
        StageId.S9_BUDGET.value,
        StageId.S10_VENDORS.value,
    }
    if (
        current_stage in _gated
        and not is_stage_complete(current_stage, memory)
        and ai_decision_type == StageDecisionType.ADVANCE.value
    ):
        return StageDecisionType.STAY.value, current_stage, "memory_incomplete_block_advance"

    # Honor a valid AI advance only for stages without tight completeness gates
    if is_valid and ai_decision_type == StageDecisionType.ADVANCE.value:
        return ai_decision_type, ai_to_stage, "ai_advance"

    if is_valid and ai_decision_type == StageDecisionType.STAY.value:
        return ai_decision_type, ai_to_stage, "ai_stay"

    return StageDecisionType.STAY.value, current_stage, "continue_gathering"


def infer_synthesis_type(stage: str, memory: dict | None = None) -> str | None:
    """Map synthesis stages to synthesis type when client omits it."""
    from app.domain.memory_schema import resolve_primary_vibe

    memory = memory or {}

    # S4 complete → client may trigger brief synthesis explicitly
    if stage == StageId.S4_VIBE.value:
        if resolve_primary_vibe(memory):
            return SynthesisType.BRIEF.value
        return None

    if stage == StageId.S5_BRIEF.value and memory:
        brief = memory.get("brief", {})
        stale = memory.get("staleSections", [])
        if brief.get("status") != "ready" or "brief" in stale:
            return SynthesisType.BRIEF.value
        return SynthesisType.DIRECTION.value

    mapping = {
        StageId.S5_BRIEF.value: SynthesisType.BRIEF.value,
        StageId.S6_DIRECTIONS.value: SynthesisType.DIRECTION.value,
        StageId.S11_SUMMARY.value: SynthesisType.SUMMARY.value,
    }
    return mapping.get(stage)
