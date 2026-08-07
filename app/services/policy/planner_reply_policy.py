"""
Planner Reply Policy — minimal safety net.

Only intervenes when the AI returned an empty/whitespace reply.
All reply logic is handled by the agent via conversation_planner.txt.
"""
from __future__ import annotations

from app.domain.enums import StageId


def _fallback_question(stage: str) -> str:
    """Minimal fallback only when agent returns completely empty reply."""
    fallbacks = {
        StageId.S2_BASICS.value: "Where and when is the wedding?",
        StageId.S3_PERSONALITY.value: "What makes you two special as a couple?",
        StageId.S4_VIBE.value: "What's the vibe you're going for?",
        StageId.S5_BRIEF.value: "Want me to show design directions next?",
        StageId.S6_DIRECTIONS.value: "Which direction feels closest?",
        StageId.S7_EVENTS.value: "Which wedding functions would you like?",
        StageId.S8_GUESTS.value: "Roughly how many guests for each event?",
        StageId.S9_BUDGET.value: "What budget range feels comfortable?",
        StageId.S10_VENDORS.value: "Which vendor priorities matter most?",
    }
    return fallbacks.get(stage, "Tell me more when you're ready.")


def align_planner_reply(*, ai_reply: str, to_stage: str) -> str:
    """Trust the agent's reply. Only use fallback when completely empty."""
    reply = (ai_reply or "").strip()
    return reply if reply else _fallback_question(to_stage)
