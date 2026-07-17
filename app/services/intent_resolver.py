"""
Local intent resolver — classifies a conversation_turn message into a TurnIntent
using deterministic rules, with NO LLM call.

SHADOW MODE (current): the orchestrator runs this alongside the Call-1 intent model
and logs where the two agree/disagree. The LLM result stays authoritative until the
local rules are proven out on real traffic; then Call-1 can be dropped and this
becomes the sole intent classifier — removing one LLM round-trip per turn.

Design mirrors app/prompts/turn_intent.txt so the two classifiers are comparable:
  more_suggestions | help | gibberish | correction | normal
Detection order matters — a help/more-suggestions question with typos must not be
misread as gibberish (see turn_intent.txt).
"""
from __future__ import annotations

import re

from app.domain.chip_pools import get_chip_pool
from app.domain.enums import IntentType, StageDecisionType, StageId
from app.domain.intent import TurnIntent
from app.domain.text_extract import (
    chips_mentioned_in_message,
    extract_month_or_season,
    extract_place_from_message,
    extract_vibe_label,
    looks_like_gibberish,
)

# Current stage → the memory section a "normal" answer on that stage fills.
# Used to tell a same-stage answer apart from a correction of an earlier section.
_STAGE_SECTION: dict[str, str] = {
    StageId.S2_BASICS.value: "occasion",
    StageId.S3_PERSONALITY.value: "personality",
    StageId.S4_VIBE.value: "vibe",
    StageId.S7_EVENTS.value: "logistics",
    StageId.S8_GUESTS.value: "logistics",
    StageId.S9_BUDGET.value: "logistics",
    StageId.S10_VENDORS.value: "logistics",
}

# "Give me more/other options" — asking for fresh suggestions, not answering.
_MORE_SUGGESTIONS_RE = re.compile(
    r"\b(more|other|another|different|alternative[s]?|else)\b"
    r".*\b(option|options|suggestion|suggestions|idea|ideas|chip|chips|vibe|vibes|one|ones|example|examples)\b"
    r"|\b(something|anything|what)\s+else\b"
    r"|\bgive me more\b|\bmore (options|ideas|suggestions|examples)\b",
    re.IGNORECASE,
)

# Process / meaning questions ("what does personality mean?", "can I type my own?").
_HELP_RE = re.compile(
    r"\bwhat (do you mean|does .*\bmean\b|is\b|are\b|'?s the difference)"
    r"|\bwhat do .*\bmean\b"
    r"|\bhow (do|should|does|can) (i|we|it)\b"
    r"|\bcan i\b|\bdo i (have to|need to|pick|choose|get to|just)\b"
    r"|\bnot sure what\b|\b(i'?m|am i) confused\b|\bwhat should i\b"
    r"|\bmeaning of\b|\bwhat counts as\b|\bexplain\b",
    re.IGNORECASE,
)

# Correcting earlier info ("actually, change the city to…", "no, I meant…").
_CORRECTION_RE = re.compile(
    r"\b(actually|instead|rather|i meant|scratch that|correction|"
    r"no wait|wait no|change (it|the|that|to)|make it|let'?s change)\b",
    re.IGNORECASE,
)


def _sections_in_message(message: str) -> set[str]:
    """Which memory sections this message plausibly references (deterministic extractors)."""
    found: set[str] = set()
    if extract_place_from_message(message) or extract_month_or_season(message):
        found.add("occasion")
    if extract_vibe_label(message):
        found.add("vibe")
    if chips_mentioned_in_message(message, get_chip_pool(StageId.S3_PERSONALITY.value)):
        found.add("personality")
    if chips_mentioned_in_message(message, get_chip_pool(StageId.S7_EVENTS.value)):
        found.add("logistics")
    return found


def resolve_intent(stage: str, memory: dict, message: str) -> TurnIntent:
    """Classify a conversation_turn message into a TurnIntent using local rules only."""
    text = (message or "").strip()
    if not text:
        return TurnIntent(
            intent_type=IntentType.GIBBERISH,
            decision_hint=StageDecisionType.REQUEST_CLARIFICATION.value,
            summary="local: empty message",
        )

    # 1. More-suggestions and help come first: a real question with typos is NOT gibberish.
    if _MORE_SUGGESTIONS_RE.search(text):
        return TurnIntent(
            intent_type=IntentType.MORE_SUGGESTIONS,
            decision_hint=StageDecisionType.STAY.value,
            summary="local: asked for more suggestions",
        )

    if _HELP_RE.search(text):
        return TurnIntent(
            intent_type=IntentType.HELP,
            decision_hint=StageDecisionType.STAY.value,
            summary="local: process/meaning question",
        )

    # 2. Gibberish — random keystrokes / mash.
    if looks_like_gibberish(text):
        return TurnIntent(
            intent_type=IntentType.GIBBERISH,
            decision_hint=StageDecisionType.REQUEST_CLARIFICATION.value,
            summary="local: gibberish",
        )

    # 3. Correction — an explicit change cue plus a reference to a section other than
    #    the one this stage is gathering (i.e. correcting earlier info from a later stage).
    current_section = _STAGE_SECTION.get(stage)
    other_sections = _sections_in_message(text) - {current_section}
    if _CORRECTION_RE.search(text) and other_sections:
        return TurnIntent(
            intent_type=IntentType.CORRECTION,
            target_sections=sorted(other_sections),
            decision_hint=StageDecisionType.REANCHOR.value,
            summary="local: correction of earlier section(s)",
        )

    # 4. Default — a normal answer for the current stage.
    return TurnIntent(
        intent_type=IntentType.NORMAL,
        decision_hint=StageDecisionType.STAY.value,
        summary="local: normal answer",
    )
