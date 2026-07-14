"""
Sanitize AI structured output before validation.
Fixes common model mistakes (invalid stage ids, bad decision types).
Does NOT block normal stage advance — only overrides on explicit corrections.
"""
from __future__ import annotations

from app.domain.enums import StageDecisionType, StageId
from app.services.turn_intent import TurnIntent

VALID_STAGE_IDS = {s.value for s in StageId}
VALID_DECISION_TYPES = {d.value for d in StageDecisionType}

_STAGE_ALIASES: list[tuple[str, str]] = [
    ("personality", StageId.S3_PERSONALITY.value),
    ("names", StageId.S1_NAMES.value),
    ("s1", StageId.S1_NAMES.value),
    ("basics", StageId.S2_BASICS.value),
    ("s2", StageId.S2_BASICS.value),
    ("vibe", StageId.S4_VIBE.value),
    ("s4", StageId.S4_VIBE.value),
    ("brief", StageId.S5_BRIEF.value),
    ("s5", StageId.S5_BRIEF.value),
    ("direction", StageId.S6_DIRECTIONS.value),
    ("s6", StageId.S6_DIRECTIONS.value),
    ("events", StageId.S7_EVENTS.value),
    ("guest", StageId.S8_GUESTS.value),
    ("budget", StageId.S9_BUDGET.value),
    ("vendor", StageId.S10_VENDORS.value),
    ("summary", StageId.S11_SUMMARY.value),
]


def _normalize_stage_id(raw_stage: str, current_stage: str) -> str:
    if raw_stage in VALID_STAGE_IDS:
        return raw_stage
    raw_l = (raw_stage or "").lower()
    for needle, stage_id in _STAGE_ALIASES:
        if needle in raw_l:
            return stage_id
    return current_stage


def sanitize_ai_response(
    raw: dict,
    current_stage: str,
    intent: TurnIntent | None = None,
) -> dict:
    """Fix invalid stage decisions so validation passes. Preserve advance when valid."""
    raw = dict(raw)

    if "suggestions" not in raw or raw["suggestions"] is None:
        raw["suggestions"] = []
    if "staleSections" not in raw or raw["staleSections"] is None:
        raw["staleSections"] = []
    if "openQuestions" not in raw or raw["openQuestions"] is None:
        raw["openQuestions"] = []
    if not isinstance(raw.get("memoryPatch"), dict):
        raw["memoryPatch"] = {}

    sd = raw.get("stageDecision") if isinstance(raw.get("stageDecision"), dict) else {}

    decision_type = sd.get("type", StageDecisionType.STAY.value)
    if decision_type not in VALID_DECISION_TYPES:
        decision_type = StageDecisionType.STAY.value

    to_stage = _normalize_stage_id(sd.get("stage", current_stage), current_stage)

    # Explicit corrections only: reanchor on current stage (docs Example 3/5)
    if (
        intent
        and intent.is_correction
        and intent.confidence in ("medium", "high")
        and intent.decision_type == StageDecisionType.REANCHOR.value
    ):
        decision_type = StageDecisionType.REANCHOR.value
        to_stage = current_stage

    raw["stageDecision"] = {"type": decision_type, "stage": to_stage}
    return raw
