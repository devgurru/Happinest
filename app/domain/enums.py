"""
Canonical enums and constants for Happinest.
These are the source of truth for all stage, event, and decision vocabulary.
Backend enforces these. No other code should hardcode stage/event strings.
"""
from enum import Enum
from app.config import settings

# ─── Stage IDs ────────────────────────────────────────────────────────────────

class StageId(str, Enum):
    S1_NAMES       = "s1_names"
    S2_BASICS      = "s2_basics"
    S3_PERSONALITY = "s3_personality"
    S4_VIBE        = "s4_vibe"
    S5_BRIEF       = "s5_brief"
    S6_DIRECTIONS  = "s6_directions"
    S7_EVENTS      = "s7_events"
    S8_GUESTS      = "s8_guests"
    S9_BUDGET      = "s9_budget"
    S10_VENDORS    = "s10_vendors"
    S11_SUMMARY    = "s11_summary"

    @classmethod
    def ordered(cls) -> list["StageId"]:
        return [
            cls.S1_NAMES, cls.S2_BASICS, cls.S3_PERSONALITY, cls.S4_VIBE,
            cls.S5_BRIEF, cls.S6_DIRECTIONS, cls.S7_EVENTS, cls.S8_GUESTS,
            cls.S9_BUDGET, cls.S10_VENDORS, cls.S11_SUMMARY,
        ]

    def next_stage(self) -> "StageId | None":
        order = StageId.ordered()
        try:
            idx = order.index(self)
            return order[idx + 1] if idx + 1 < len(order) else None
        except ValueError:
            return None


# ─── Event Types ──────────────────────────────────────────────────────────────

class EventType(str, Enum):
    DRAFT_UPDATE      = "draft_update"      # Local only, never reaches backend AI
    CONVERSATION_TURN = "conversation_turn" # Committed user message → AI may run
    SYNTHESIS_REQUEST = "synthesis_request" # Generate brief/direction/summary
    SYSTEM_RECOMPUTE  = "system_recompute"  # Backend-triggered after upstream change


# ─── Stage Decision Types ─────────────────────────────────────────────────────

class StageDecisionType(str, Enum):
    STAY                  = "stay"                  # Remain, continue conversation
    ADVANCE               = "advance"               # Move to next guided stage
    REANCHOR              = "reanchor"              # Stay but reframe
    JUMP                  = "jump"                  # Jump to different stage
    REQUEST_CLARIFICATION = "request_clarification" # Stay, ask focused follow-up

# ─── Turn Intent Types ────────────────────────────────────────────────────────
# Classification of what a client's conversation_turn message is trying to do.
# Currently produced by the Call-1 intent LLM; targeted for local rule resolution
# so the intent classification no longer requires an LLM round-trip.

class IntentType(str, Enum):
    NORMAL           = "normal"           # Answering the current stage with usable content
    GIBBERISH        = "gibberish"        # Random keystrokes / unintelligible nonsense
    HELP             = "help"             # Asking what a stage/chip means or how to answer
    MORE_SUGGESTIONS = "more_suggestions" # Asking for more/other/different suggestion chips
    CLARIFICATION    = "clarification"    # Vague/incomplete answer, not mash, not a help question
    CORRECTION       = "correction"       # Changing earlier info from a later stage


# ─── Response Sources ─────────────────────────────────────────────────────────

class ResponseSource(str, Enum):
    OPENAI = settings.llm_provider   # Successful AI response (Gemma3 locally = same semantics)
    SYSTEM = "system"   # Backend/rule-based response (e.g. S1 names)
    RULE   = "rule"     # Deterministic backend rule applied
    ERROR  = "error"    # Explicit failure — no fallback generated


# ─── Stale Section IDs ────────────────────────────────────────────────────────

class StaleSectionId(str, Enum):
    BRIEF     = "brief"
    DIRECTION = "direction"
    BUDGET    = "budget"
    VENDORS   = "vendors"
    SUMMARY   = "summary"


# ─── Synthesis Types ──────────────────────────────────────────────────────────

class SynthesisType(str, Enum):
    BRIEF     = "brief"
    DIRECTION = "direction"
    SUMMARY   = "summary"


# ─── Message Roles ────────────────────────────────────────────────────────────

class MessageRole(str, Enum):
    CLIENT  = "client"   # User/client message
    PLANNER = "planner"  # AI/planner response
    SYSTEM  = "system"   # Internal system note


# ─── Message Types ────────────────────────────────────────────────────────────

class MessageType(str, Enum):
    CONVERSATION_TURN = "conversation_turn"
    SYNTHESIS_REQUEST = "synthesis_request"
    SYSTEM_RECOMPUTE  = "system_recompute"
    ERROR             = "error"


# ─── Artifact Types ───────────────────────────────────────────────────────────

class ArtifactType(str, Enum):
    BRIEF     = "brief"
    DIRECTION = "direction"
    SUMMARY   = "summary"


# ─── Artifact Status ──────────────────────────────────────────────────────────

class ArtifactStatus(str, Enum):
    READY      = "ready"
    STALE      = "stale"
    SUPERSEDED = "superseded"


# ─── Session Status ───────────────────────────────────────────────────────────

class SessionStatus(str, Enum):
    ACTIVE    = "active"
    COMPLETED = "completed"
    ARCHIVED  = "archived"


# ─── Vendor Types ─────────────────────────────────────────────────────────────

class VendorType(str, Enum):
    VENUE         = "venue"
    DECOR         = "decor"
    CATERING      = "catering"
    PHOTOGRAPHY   = "photography"
    ENTERTAINMENT = "entertainment"
    PLANNER       = "planner"
    MULTI_SERVICE = "multi_service"


# ─── Validation ───────────────────────────────────────────────────────────────

# Stages that require AI calls on conversation_turn
AI_REQUIRED_STAGES = {
    StageId.S2_BASICS,
    StageId.S3_PERSONALITY,
    StageId.S4_VIBE,
    StageId.S7_EVENTS,
    StageId.S8_GUESTS,
    StageId.S9_BUDGET,
    StageId.S10_VENDORS,
}

# Stages that use synthesis_request (not conversation_turn) to advance
SYNTHESIS_STAGES = {
    StageId.S5_BRIEF,
    StageId.S6_DIRECTIONS,
    StageId.S11_SUMMARY,
}

# Sections that can be invalidated
ALL_STALE_SECTIONS = {s.value for s in StaleSectionId}

# Allowed next stages from each stage (for policy validation)
ALLOWED_TRANSITIONS: dict[StageId, set[StageId]] = {
    StageId.S1_NAMES:       {StageId.S2_BASICS},
    StageId.S2_BASICS:      {StageId.S3_PERSONALITY, StageId.S2_BASICS},
    StageId.S3_PERSONALITY: {StageId.S4_VIBE, StageId.S3_PERSONALITY},
    StageId.S4_VIBE:        {StageId.S5_BRIEF, StageId.S4_VIBE, StageId.S3_PERSONALITY},
    StageId.S5_BRIEF:       {StageId.S6_DIRECTIONS, StageId.S5_BRIEF},
    StageId.S6_DIRECTIONS:  {StageId.S7_EVENTS, StageId.S6_DIRECTIONS},
    StageId.S7_EVENTS:      {StageId.S8_GUESTS, StageId.S7_EVENTS},
    StageId.S8_GUESTS:      {StageId.S9_BUDGET, StageId.S8_GUESTS},
    StageId.S9_BUDGET:      {StageId.S10_VENDORS, StageId.S9_BUDGET},
    StageId.S10_VENDORS:    {StageId.S11_SUMMARY, StageId.S10_VENDORS},
    StageId.S11_SUMMARY:    {StageId.S11_SUMMARY},
}
