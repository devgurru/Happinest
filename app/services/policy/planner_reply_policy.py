"""
Planner Reply Policy — Trust the agent's plannerReply.

Only intervene when the model returned an empty or whitespace-only reply.
All other reply logic (early signals, confirmation, stage transitions) is
handled by the agent via conversation_turn.txt and stage_policy.py rules.

Removed: Hard-coded fallback questions that override agent intelligence.
"""
from __future__ import annotations

from app.domain.enums import StageDecisionType, StageId


def _fallback_question(stage: str, memory: dict) -> str:
    """
    Minimal fallback only when agent returns completely empty reply.
    Keep it generic to avoid contradicting agent's intended flow.
    """
    if stage == StageId.S2_BASICS.value:
        from app.utils.validators import get_occasion_state
        state = get_occasion_state(memory)
        if state["has_place"] and not state["has_time"]:
            return f"{state['place']} — roughly which month or season?"
        if state["has_time"] and not state["has_place"]:
            return f"{state['when']} works — where's the wedding?"
        return "Where and when is the wedding?"
    if stage == StageId.S3_PERSONALITY.value:
        return "What makes you two special as a couple?"
    if stage == StageId.S4_VIBE.value:
        return "What's the vibe you're going for?"
    if stage == StageId.S6_DIRECTIONS.value:
        return "Which direction feels closest?"
    if stage == StageId.S7_EVENTS.value:
        return "Which wedding functions would you like?"
    if stage == StageId.S8_GUESTS.value:
        return "Roughly how many guests for each event?"
    if stage == StageId.S9_BUDGET.value:
        return "What budget range feels comfortable?"
    if stage == StageId.S10_VENDORS.value:
        return "Which vendor priorities matter most?"
    if stage == StageId.S5_BRIEF.value:
        return "Want me to show design directions next?"
    return "Tell me more when you're ready."


def align_planner_reply(
    *,
    ai_reply: str,
    from_stage: str,
    to_stage: str,
    decision_type: str,
    memory: dict,
    correction: dict | None = None,
    correction_ack: str = "",
) -> str:
    """
    Trust the agent's plannerReply. Only use fallback when completely empty.
    
    Agent handles:
    - Early signals acknowledgment
    - Stage-appropriate questions
    - Confirmation flows
    - Transition messaging
    
    This policy only ensures we never return empty string to user.
    """
    reply = (ai_reply or "").strip()
    landing = to_stage or from_stage

    # If agent provided a reply, trust it as-is
    if reply:
        # REANCHOR: prepend correction acknowledgment if not already present
        if (decision_type == StageDecisionType.REANCHOR.value or correction) and correction_ack:
            if correction_ack.lower()[:20] not in reply.lower():
                return f"{correction_ack} {reply}".strip()
        return reply

    # Empty reply: use minimal fallback
    if correction_ack:
        return f"{correction_ack} {_fallback_question(landing, memory)}".strip()
    
    return _fallback_question(landing, memory)
