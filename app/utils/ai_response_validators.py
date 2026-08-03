"""
AI Response Validators — sanitization and validation for AI structured output.

Extracted from app/utils/validators.py. These handle sanitizing and validating
the JSON responses returned by AI Call 2 (response planner) and synthesis calls.
"""
from __future__ import annotations

from app.domain.enums import StageDecisionType, StageId
from app.services.session.memory_service import VALID_STALE
from app.utils.timing_utils import sanitize_timing_fields

VALID_STAGE_IDS = {s.value for s in StageId}
VALID_DECISION_TYPES = {d.value for d in StageDecisionType}
REQUIRED_FIELDS = {"plannerReply", "memoryPatch", "stageDecision", "staleSections", "openQuestions", "suggestions"}
REQUIRED_STAGE_DECISION_FIELDS = {"type", "stage"}

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


def _sanitize_memory_patch_schema(patch: dict) -> dict:
    """Hoisting and nesting of memory patch fields."""
    if not patch:
        return patch

    patch = dict(patch)

    occasion = dict(patch.get("occasion") or {})
    for key in (
        "place", "locationPreference", "settingPreference",
        "datePreference", "seasonPreference", "destinationMode", "isConfirmed",
    ):
        if key in patch and key != "occasion":
            val = patch.pop(key)
            if val is not None and val != "":
                occasion[key] = val
    if occasion:
        patch["occasion"] = sanitize_timing_fields(occasion)

    logistics = dict(patch.get("logistics") or {})
    for key in ("events", "guestCounts", "budget", "vendorPreferences", "eventsConfirmed"):
        if key in patch:
            logistics[key] = patch.pop(key)
    if logistics:
        patch["logistics"] = logistics

    return patch


def sanitize_ai_response(raw: dict, current_stage: str) -> dict:
    """Sanitize AI structured output prior to schema validation."""
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
    raw["stageDecision"] = {"type": decision_type, "stage": to_stage}

    raw["memoryPatch"] = _sanitize_memory_patch_schema(raw.get("memoryPatch", {}))
    return raw


def validate_ai_response(raw: dict, stage: str) -> tuple[bool, str | None]:
    """Validate AI response dict against stage contract."""
    if not isinstance(raw, dict):
        return False, "RESPONSE_NOT_DICT"

    if "suggestions" not in raw:
        raw["suggestions"] = []

    missing = REQUIRED_FIELDS - raw.keys()
    if missing:
        return False, f"MISSING_FIELDS:{','.join(sorted(missing))}"

    planner_reply = raw.get("plannerReply", "")
    if not isinstance(planner_reply, str) or not planner_reply.strip():
        return False, "EMPTY_PLANNER_REPLY"

    if not isinstance(raw.get("memoryPatch"), dict):
        return False, "INVALID_MEMORY_PATCH"

    sd = raw.get("stageDecision", {})
    if not isinstance(sd, dict):
        return False, "INVALID_STAGE_DECISION"

    sd_missing = REQUIRED_STAGE_DECISION_FIELDS - sd.keys()
    if sd_missing:
        return False, f"MISSING_STAGE_DECISION_FIELDS:{','.join(sorted(sd_missing))}"

    if sd.get("type") not in VALID_DECISION_TYPES:
        return False, f"INVALID_DECISION_TYPE:{sd.get('type')}"

    if sd.get("stage") not in VALID_STAGE_IDS:
        return False, f"INVALID_STAGE_ID:{sd.get('stage')}"

    stale = raw.get("staleSections", [])
    if not isinstance(stale, list):
        return False, "INVALID_STALE_SECTIONS"
    invalid_stale = [s for s in stale if s not in VALID_STALE]
    if invalid_stale:
        return False, f"UNKNOWN_STALE_SECTIONS:{','.join(invalid_stale)}"

    if not isinstance(raw.get("openQuestions"), list):
        return False, "INVALID_OPEN_QUESTIONS"

    suggestions = raw.get("suggestions", [])
    if suggestions is None:
        suggestions = []
    if not isinstance(suggestions, list):
        return False, "INVALID_SUGGESTIONS"
    raw["suggestions"] = suggestions

    return True, None


def validate_synthesis_response(raw: dict, synthesis_type: str) -> tuple[bool, str | None]:
    """Validate AI response for synthesis requests."""
    is_valid, err = validate_ai_response(raw, f"synthesis_{synthesis_type}")
    if not is_valid:
        return False, err

    if synthesis_type == "brief":
        if not raw.get("briefText", "").strip():
            return False, "EMPTY_BRIEF_TEXT"

    elif synthesis_type == "direction":
        options = raw.get("directionOptions", [])
        if not isinstance(options, list) or len(options) == 0:
            return False, "EMPTY_DIRECTION_OPTIONS"
        for opt in options:
            if not isinstance(opt, dict):
                return False, "INVALID_DIRECTION_OPTION"
            for req in ("id", "name", "rankOrder", "reasonText"):
                if not opt.get(req):
                    return False, f"DIRECTION_OPTION_MISSING:{req}"

    elif synthesis_type == "summary":
        if not raw.get("summaryText", "").strip():
            return False, "EMPTY_SUMMARY_TEXT"

    return True, None
