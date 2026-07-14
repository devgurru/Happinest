"""
Response Validator — validates AI structured output before any memory mutation.
Returns (is_valid, error_code). Never raises.
"""
from app.domain.enums import StageDecisionType, StageId
from app.services.memory_service import VALID_STALE


REQUIRED_FIELDS = {"plannerReply", "memoryPatch", "stageDecision", "staleSections", "openQuestions", "suggestions"}
REQUIRED_STAGE_DECISION_FIELDS = {"type", "stage"}
VALID_DECISION_TYPES = {d.value for d in StageDecisionType}
VALID_STAGE_IDS = {s.value for s in StageId}


def validate_ai_response(raw: dict, stage: str) -> tuple[bool, str | None]:
    """
    Validate AI response dict against the stage contract.
    Returns (True, None) on success or (False, error_code) on failure.
    """
    if not isinstance(raw, dict):
        return False, "RESPONSE_NOT_DICT"

    if "suggestions" not in raw:
        raw["suggestions"] = []

    # Required top-level fields
    missing = REQUIRED_FIELDS - raw.keys()
    if missing:
        return False, f"MISSING_FIELDS:{','.join(sorted(missing))}"

    # plannerReply must be non-empty string
    planner_reply = raw.get("plannerReply", "")
    if not isinstance(planner_reply, str) or not planner_reply.strip():
        return False, "EMPTY_PLANNER_REPLY"

    # memoryPatch must be dict (can be empty)
    if not isinstance(raw.get("memoryPatch"), dict):
        return False, "INVALID_MEMORY_PATCH"

    # stageDecision validation
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

    # staleSections must be a list of valid section names
    stale = raw.get("staleSections", [])
    if not isinstance(stale, list):
        return False, "INVALID_STALE_SECTIONS"
    invalid_stale = [s for s in stale if s not in VALID_STALE]
    if invalid_stale:
        return False, f"UNKNOWN_STALE_SECTIONS:{','.join(invalid_stale)}"

    # openQuestions must be a list
    if not isinstance(raw.get("openQuestions"), list):
        return False, "INVALID_OPEN_QUESTIONS"

    # suggestions must be a list (may be empty — backend fills UI hints)
    suggestions = raw.get("suggestions", [])
    if suggestions is None:
        suggestions = []
    if not isinstance(suggestions, list):
        return False, "INVALID_SUGGESTIONS"
    raw["suggestions"] = suggestions

    return True, None


def validate_synthesis_response(raw: dict, synthesis_type: str) -> tuple[bool, str | None]:
    """Validate AI response for synthesis requests (brief, direction, summary)."""
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
