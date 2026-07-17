"""
Planner Reply Policy — prefer the agent's plannerReply.

Only intervene when:
  - the model returned an empty reply, or
  - backend advanced stages and the reply is still stuck asking the *previous* stage.

Everyday stay / clarification / reanchor copy comes from the LLM (varied wording).
"""
from __future__ import annotations

from app.domain.enums import StageDecisionType, StageId


def _s2_asks_place_when_place_known(reply_lower: str, memory: dict) -> bool:
    """True if model re-asks location while place is already saved."""
    from app.domain.text_extract import get_occasion_state

    state = get_occasion_state(memory)
    if not state["has_place"]:
        return False
    place_ask = any(
        p in reply_lower
        for p in (
            "where is the wedding", "where's the wedding", "where are you",
            "which city", "what location", "planning to have the wedding",
        )
    )
    asks_timing = any(
        t in reply_lower
        for t in ("month", "season", "when", "date", "timing")
    )
    return place_ask and not asks_timing


def _stuck_on_previous_stage(reply_lower: str, from_stage: str, to_stage: str) -> bool:
    """After ADVANCE, detect replies still asking the completed stage's question."""
    if from_stage == StageId.S2_BASICS.value and to_stage == StageId.S3_PERSONALITY.value:
        return any(
            p in reply_lower
            for p in (
                "where is the wedding", "where's the wedding", "which month",
                "what season", "where are you thinking",
            )
        )
    if from_stage == StageId.S3_PERSONALITY.value and to_stage == StageId.S4_VIBE.value:
        return "personality" in reply_lower and "vibe" not in reply_lower
    if from_stage == StageId.S6_DIRECTIONS.value and to_stage == StageId.S7_EVENTS.value:
        return "direction" in reply_lower and "event" not in reply_lower and "function" not in reply_lower
    return False


def _fallback_question(stage: str, memory: dict) -> str:
    """Last-resort short question only when the agent returned no usable reply."""
    if stage == StageId.S2_BASICS.value:
        from app.domain.text_extract import get_occasion_state
        state = get_occasion_state(memory)
        if state["has_place"] and not state["has_time"]:
            return f"{state['place']} is noted — roughly which month or season?"
        if state["has_time"] and not state["has_place"]:
            return f"{state['when']} works — where's the wedding?"
        return "Where's the wedding, and roughly when?"
    if stage == StageId.S3_PERSONALITY.value:
        return "What feels most like the two of you?"
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
    return "Tell me a bit more when you're ready."


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
    Prefer the agent's plannerReply. Use fallback only when empty or
    clearly stuck on the wrong stage after an advance.
    """
    reply = (ai_reply or "").strip()
    low = reply.lower()
    landing = to_stage or from_stage

    # ADVANCE: keep agent copy unless empty or still asking the old stage
    if (
        decision_type == StageDecisionType.ADVANCE.value
        and to_stage
        and to_stage != from_stage
    ):
        if reply and not _stuck_on_previous_stage(low, from_stage, to_stage):
            return reply
        return _fallback_question(to_stage, memory)

    # REANCHOR: prefer agent's wording; prepend correction_ack only if reply lacks it
    if decision_type == StageDecisionType.REANCHOR.value or correction:
        if reply:
            if correction_ack and correction_ack.lower()[:20] not in low:
                return f"{correction_ack} {reply}".strip()
            return reply
        if correction_ack:
            return f"{correction_ack} {_fallback_question(landing, memory)}".strip()
        return _fallback_question(landing, memory)

    # STAY / clarification: agent's creative reply wins
    if reply:
        # One surgical fix: don't re-ask place when it is already in memory
        if from_stage == StageId.S2_BASICS.value and _s2_asks_place_when_place_known(low, memory):
            return _fallback_question(from_stage, memory)
        return reply

    return _fallback_question(from_stage, memory)
