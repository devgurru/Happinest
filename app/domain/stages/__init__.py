"""Stage policy — split by concern into config, completion, transitions."""
from app.domain.stages.config import (
    GLOBAL_AGENT_RULES,
    STAGE_CONFIG,
    get_stage_prompt_context,
    get_stage_rules_for_intent,
)
from app.domain.stages.completion import get_stage_gap_guide, is_stage_complete
from app.domain.stages.transitions import (
    infer_synthesis_type,
    resolve_final_decision_with_memory,
    validate_transition,
)

__all__ = [
    "GLOBAL_AGENT_RULES", "STAGE_CONFIG",
    "get_stage_prompt_context", "get_stage_rules_for_intent",
    "get_stage_gap_guide", "is_stage_complete",
    "infer_synthesis_type", "resolve_final_decision_with_memory", "validate_transition",
]
