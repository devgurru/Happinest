"""
Turn intent — the classified shape of a single client conversation_turn.

Today `TurnIntent.from_llm` parses the Call-1 intent model's JSON. The planned
local intent resolver will build the same `TurnIntent` from deterministic rules
(gibberish detection, keyword matching, section extractors) so intent
classification no longer needs an LLM round-trip.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.enums import IntentType, StageDecisionType


@dataclass
class TurnIntent:
    """What a client's message on the current turn is trying to do."""
    intent_type: IntentType = IntentType.NORMAL
    target_sections: list[str] = field(default_factory=list)
    decision_hint: str = StageDecisionType.STAY.value
    summary: str = ""

    @classmethod
    def default(cls) -> "TurnIntent":
        """Neutral fallback used when classification is unavailable."""
        return cls()

    @classmethod
    def from_llm(cls, raw: dict | None) -> "TurnIntent":
        """Parse the Call-1 intent model's raw JSON into a TurnIntent.

        Unknown/malformed values fall back to a neutral `normal` intent so a bad
        classification never breaks the turn.
        """
        if not isinstance(raw, dict):
            return cls()

        try:
            intent_type = IntentType(str(raw.get("intentType") or "normal"))
        except ValueError:
            intent_type = IntentType.NORMAL

        sections = [s for s in (raw.get("targetSections") or []) if isinstance(s, str)]

        return cls(
            intent_type=intent_type,
            target_sections=sections,
            decision_hint=str(raw.get("decisionHint") or StageDecisionType.STAY.value),
            summary=str(raw.get("summary") or ""),
        )
