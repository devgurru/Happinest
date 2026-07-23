"""
Context Builder — Pure Python, no AI.

Bridge between AI Call 1 (data extraction) and AI Call 2 (response planning).
Takes the extraction result + current memory and produces a TurnContext that:
  - Decides stay / advance / reanchor (deterministically)
  - Identifies missing fields
  - Builds early signal summary strings
  - Tells the response planner exactly what to patch and what stage decision to use

This is the single source of "what should happen this turn" logic.
The response planner's only job is to write a warm, natural reply and chips.
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.ai.data_extractor import ExtractionResult


# ─── TurnContext ───────────────────────────────────────────────────────────────

@dataclass
class TurnContext:
    """
    Pre-computed context for AI Call 2 (response planner).

    All behavioural decisions are made HERE so the response planner
    only needs to write a human reply and generate chips.
    """
    stage_status: str           # "complete" | "incomplete" | "needs_early_signal_confirm" | "meta"
    decision: str               # "advance" | "stay" | "reanchor" | "request_clarification"
    meta_intent: str            # from extraction: normal / help / more_suggestions / etc.
    confirmed_data: dict        # validated_patch to write to memory
    early_signals_to_patch: dict  # extracted earlySignals → merged into memory patch by backend
    missing_fields: list[str]   # human-readable list of what's still needed
    early_signal_summary: str   # e.g. "Heard earlier: personality: Foodies, Travel lovers"
    extraction_summary: str     # one-line from extraction
    corrected_section: str | None  # set on reanchor
    next_stage: str | None      # set when advancing
    stage_decision: dict        # ready-to-use stageDecision for the prompt
    confirmed_patch_json: str   # json.dumps of confirmed_data for the prompt

    def is_advancing(self) -> bool:
        return self.decision == "advance"

    def is_meta(self) -> bool:
        return self.stage_status == "meta"


# ─── Main builder ─────────────────────────────────────────────────────────────

def build_turn_context(
    stage: str,
    memory: dict,
    extraction: "ExtractionResult",
) -> TurnContext:
    """
    Build a TurnContext from the extraction result and current memory.

    Flow:
    1. Meta intents (help/more_suggestions/gibberish) → short-circuit, no data
    2. Correction intent → reanchor with corrected data
    3. Normal/clarification → tentative merge → stage completeness check → advance/stay
    4. Early signal confirmation needed → stay with confirmation prompt
    """
    from app.domain.enums import StageId, StageDecisionType
    from app.services.policy.stage_policy import StagePolicy

    meta_intent = extraction.meta_intent

    # ── 1. Meta turns ──────────────────────────────────────────────────────────
    if meta_intent in ("help", "more_suggestions", "gibberish"):
        decision = (
            StageDecisionType.REQUEST_CLARIFICATION.value
            if meta_intent == "gibberish"
            else StageDecisionType.STAY.value
        )
        return TurnContext(
            stage_status="meta",
            decision=decision,
            meta_intent=meta_intent,
            confirmed_data={},
            early_signals_to_patch={},
            missing_fields=[],
            early_signal_summary="",
            extraction_summary=extraction.extraction_summary,
            corrected_section=None,
            next_stage=None,
            stage_decision={"type": decision, "stage": stage},
            confirmed_patch_json="{}",
        )

    # ── 2. Correction / reanchor ────────────────────────────────────────────
    if meta_intent == "correction" and extraction.corrected_section:
        early_sum = _build_early_signal_summary(extraction.early_signals, stage, memory)
        early_signals = _non_empty_early_signals(extraction.early_signals)

        # Critical: even on correction turns, check whether the stage is now complete.
        # memory is already post-extraction (Phase 2.5 applied it to DB before this call).
        # Case: first S2 message where user corrects names AND provides all S2 data at once —
        # the correction should NOT block advancement if all required fields are now present.
        stage_complete = StagePolicy.is_stage_complete(stage, memory)

        if stage_complete:
            try:
                from app.domain.enums import StageId as _SID
                next_s = _SID(stage).next_stage()
                next_stage = next_s.value if next_s else None
            except ValueError:
                next_stage = None

            stage_dec = {"type": StageDecisionType.ADVANCE.value, "stage": next_stage or stage}
            return TurnContext(
                stage_status="complete",
                decision=StageDecisionType.ADVANCE.value,
                meta_intent=meta_intent,
                confirmed_data=extraction.validated_patch,
                early_signals_to_patch=early_signals,
                missing_fields=[],
                early_signal_summary=early_sum,
                extraction_summary=extraction.extraction_summary,
                corrected_section=extraction.corrected_section,
                next_stage=next_stage,
                stage_decision=stage_dec,
                confirmed_patch_json=json.dumps(extraction.validated_patch, indent=2),
            )

        # Stage still incomplete after correction — stay and ask for what's missing
        missing = _get_missing_fields(stage, memory)
        stage_dec = {"type": StageDecisionType.REANCHOR.value, "stage": stage}
        return TurnContext(
            stage_status="reanchor",
            decision=StageDecisionType.REANCHOR.value,
            meta_intent=meta_intent,
            confirmed_data=extraction.validated_patch,
            early_signals_to_patch=early_signals,
            missing_fields=missing,
            early_signal_summary=early_sum,
            extraction_summary=extraction.extraction_summary,
            corrected_section=extraction.corrected_section,
            next_stage=None,
            stage_decision=stage_dec,
            confirmed_patch_json=json.dumps(extraction.validated_patch, indent=2),
        )

    # ── 3. Normal / clarification ──────────────────────────────────────────────
    # Tentatively merge extraction patch into a scratch copy of memory
    scratch = _deep_merge(copy.deepcopy(memory), extraction.validated_patch)

    # Check if early-signal confirmation is needed (before completeness check)
    needs_confirm = _needs_early_signal_confirm(stage, memory, extraction)

    if needs_confirm:
        # First turn on this stage — earlySignals exist but canonical field is empty
        # and the user didn't provide new data for the canonical field
        stage_dec = {"type": StageDecisionType.STAY.value, "stage": stage}
        early_sum = _build_early_signal_confirm_prompt(stage, memory)
        return TurnContext(
            stage_status="needs_early_signal_confirm",
            decision=StageDecisionType.STAY.value,
            meta_intent=meta_intent,
            confirmed_data={},
            early_signals_to_patch={},
            missing_fields=[],
            early_signal_summary=early_sum,
            extraction_summary=extraction.extraction_summary,
            corrected_section=None,
            next_stage=None,
            stage_decision=stage_dec,
            confirmed_patch_json="{}",
        )

    # Check stage completeness after tentative merge
    stage_complete = StagePolicy.is_stage_complete(stage, scratch)
    early_sum = _build_early_signal_summary(extraction.early_signals, stage, memory)
    early_signals = _non_empty_early_signals(extraction.early_signals)

    if stage_complete:
        # Advance to next stage
        try:
            from app.domain.enums import StageId as _SID
            next_s = _SID(stage).next_stage()
            next_stage = next_s.value if next_s else None
        except ValueError:
            next_stage = None

        stage_dec = {
            "type": StageDecisionType.ADVANCE.value,
            "stage": next_stage or stage,
        }
        return TurnContext(
            stage_status="complete",
            decision=StageDecisionType.ADVANCE.value,
            meta_intent=meta_intent,
            confirmed_data=extraction.validated_patch,
            early_signals_to_patch=early_signals,
            missing_fields=[],
            early_signal_summary=early_sum,
            extraction_summary=extraction.extraction_summary,
            corrected_section=None,
            next_stage=next_stage,
            stage_decision=stage_dec,
            confirmed_patch_json=json.dumps(extraction.validated_patch, indent=2),
        )

    # Stage still incomplete — stay and ask for missing fields
    missing = _get_missing_fields(stage, scratch)
    stage_dec = {"type": StageDecisionType.STAY.value, "stage": stage}

    return TurnContext(
        stage_status="incomplete",
        decision=StageDecisionType.STAY.value,
        meta_intent=meta_intent,
        confirmed_data=extraction.validated_patch,
        early_signals_to_patch=early_signals,
        missing_fields=missing,
        early_signal_summary=early_sum,
        extraction_summary=extraction.extraction_summary,
        corrected_section=None,
        next_stage=None,
        stage_decision=stage_dec,
        confirmed_patch_json=json.dumps(extraction.validated_patch, indent=2),
    )


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _deep_merge(base: dict, patch: dict) -> dict:
    """Recursively merge patch into base (lists are replaced, not appended)."""
    for key, value in patch.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def _non_empty_early_signals(es: dict) -> dict:
    """Return earlySignals dict with only non-empty values."""
    result = {}
    for k in ("personality", "vibe", "events"):
        lst = es.get(k) or []
        if lst:
            result[k] = lst
    for k in ("budget", "vendors"):
        d = es.get(k) or {}
        if d:
            result[k] = d
    guest = es.get("guestCount")
    if guest is not None and isinstance(guest, (int, float)) and guest > 0:
        result["guestCount"] = int(guest)
    return result


def _needs_early_signal_confirm(
    stage: str,
    memory: dict,
    extraction: "ExtractionResult",
) -> bool:
    """
    True when:
    - The stage has earlySignals data in memory for its canonical field
    - That canonical field is still empty
    - The extraction did NOT provide new data for that canonical field
    """
    from app.domain.enums import StageId
    early = memory.get("earlySignals") or {}
    patch = extraction.validated_patch

    if stage == StageId.S3_PERSONALITY.value:
        from app.utils.validators import filter_tags
        existing_tags = filter_tags((memory.get("personality") or {}).get("tags") or [])
        ep = early.get("personality") or []
        new_tags = filter_tags((patch.get("personality") or {}).get("tags") or [])
        return bool(ep and not existing_tags and not new_tags)

    if stage == StageId.S4_VIBE.value:
        from app.domain.memory_schema import resolve_primary_vibe
        ev = early.get("vibe") or []
        primary = resolve_primary_vibe(memory)
        new_vibe = (patch.get("vibe") or {}).get("primaryVibe", "")
        return bool(ev and not primary and not new_vibe)

    if stage == StageId.S7_EVENTS.value:
        existing_events = (memory.get("logistics") or {}).get("events") or []
        ee = early.get("events") or []
        new_events = (patch.get("logistics") or {}).get("events") or []
        return bool(ee and not existing_events and not new_events)

    if stage == StageId.S9_BUDGET.value:
        existing_budget = (memory.get("logistics") or {}).get("budget") or {}
        eb = early.get("budget") or {}
        new_budget = (patch.get("logistics") or {}).get("budget") or {}
        return bool(eb and not existing_budget.get("range") and not new_budget.get("range"))

    return False


def _build_early_signal_confirm_prompt(stage: str, memory: dict) -> str:
    """Build the confirmation prompt string when earlySignals need confirming."""
    from app.domain.enums import StageId
    early = memory.get("earlySignals") or {}

    if stage == StageId.S3_PERSONALITY.value:
        ep = early.get("personality") or []
        if ep:
            return f"You mentioned you're {', '.join(ep)} earlier — does that capture you two, or want to add more?"

    if stage == StageId.S4_VIBE.value:
        ev = early.get("vibe") or []
        if ev:
            return f"You mentioned {', '.join(ev[:2])} earlier — does that feel right, or want something different?"

    if stage == StageId.S7_EVENTS.value:
        ee = early.get("events") or []
        if ee:
            return f"You mentioned {', '.join(ee)} earlier — are those the main events, or want to add/change anything?"

    if stage == StageId.S9_BUDGET.value:
        eb = early.get("budget") or {}
        rng = eb.get("range") or str(eb)
        if rng:
            return f"You mentioned a budget of {rng} earlier — does that still work, or want to adjust?"

    return ""


def _get_missing_fields(stage: str, memory: dict) -> list[str]:
    """Return human-readable list of missing required fields for the stage."""
    from app.services.policy.stage_policy import STAGE_CONFIG
    config = STAGE_CONFIG.get(stage, {})
    hints = config.get("missingFieldsHint") or []

    if hints:
        # Filter to only fields that are actually missing
        missing = []
        from app.domain.enums import StageId
        if stage == StageId.S2_BASICS.value:
            from app.utils.validators import get_occasion_state
            state = get_occasion_state(memory)
            if not state["has_place"]:
                missing.append("wedding destination (city or region)")
            if not state["has_time"]:
                missing.append("wedding date or season")
            return missing
        if stage == StageId.S3_PERSONALITY.value:
            from app.utils.validators import filter_tags
            tags = filter_tags((memory.get("personality") or {}).get("tags") or [])
            if len(tags) < 2:
                missing.append("personality traits or labels (2+ needed)")
            return missing
        if stage == StageId.S4_VIBE.value:
            from app.domain.memory_schema import resolve_primary_vibe
            if not resolve_primary_vibe(memory):
                missing.append("wedding vibe or atmosphere")
            return missing
        if stage == StageId.S7_EVENTS.value:
            events = (memory.get("logistics") or {}).get("events") or []
            if not events:
                missing.append("wedding functions/events")
            elif not (memory.get("logistics") or {}).get("eventsConfirmed"):
                missing.append("confirmation that event list is complete (say 'that's all')")
            return missing
        if stage == StageId.S8_GUESTS.value:
            events = (memory.get("logistics") or {}).get("events") or []
            counts = (memory.get("logistics") or {}).get("guestCounts") or {}
            missing_events = [e for e in events if not counts.get(e)]
            if missing_events:
                missing.append(f"guest count for: {', '.join(missing_events)}")
            return missing
        if stage == StageId.S9_BUDGET.value:
            budget = (memory.get("logistics") or {}).get("budget") or {}
            if not budget.get("range"):
                missing.append("budget range in lakhs")
            return missing
        if stage == StageId.S10_VENDORS.value:
            prefs = (memory.get("logistics") or {}).get("vendorPreferences") or {}
            if not prefs:
                missing.append("vendor category preferences")
            return missing

        return hints

    return [f"{stage} information"]


def _build_early_signal_summary(early_signals: dict, stage: str, memory: dict) -> str:
    """
    Build a human-readable summary of early signals for the response planner.
    Only includes signals relevant for stages OTHER than the current one.
    """
    from app.domain.enums import StageId

    memory_early = memory.get("earlySignals") or {}

    # Merge extraction early signals with what's already in memory
    combined: dict = {
        "personality": list(dict.fromkeys(
            (memory_early.get("personality") or []) + (early_signals.get("personality") or [])
        )),
        "vibe": list(dict.fromkeys(
            (memory_early.get("vibe") or []) + (early_signals.get("vibe") or [])
        )),
        "events": list(dict.fromkeys(
            (memory_early.get("events") or []) + (early_signals.get("events") or [])
        )),
        "budget": {**(memory_early.get("budget") or {}), **(early_signals.get("budget") or {})},
        "vendors": {**(memory_early.get("vendors") or {}), **(early_signals.get("vendors") or {})},
    }

    parts = []
    # Show personality only if we're NOT on S3
    if combined.get("personality") and stage != StageId.S3_PERSONALITY.value:
        parts.append(f"personality: {', '.join(combined['personality'])}")
    # Show vibe only if not on S4
    if combined.get("vibe") and stage != StageId.S4_VIBE.value:
        parts.append(f"vibe: {', '.join(combined['vibe'])}")
    # Show events only if not on S7
    if combined.get("events") and stage != StageId.S7_EVENTS.value:
        parts.append(f"events: {', '.join(combined['events'])}")
    # Show budget only if not on S9
    if combined.get("budget") and stage != StageId.S9_BUDGET.value:
        rng = combined["budget"].get("range") or str(combined["budget"])
        parts.append(f"budget: {rng}")

    if not parts:
        return ""
    return "Also heard earlier: " + "; ".join(parts)


def merge_early_signals(existing: dict, new: dict) -> dict:
    """
    Merge two earlySignals dicts.
    Lists are combined (deduplicated). Dicts are merged (new takes precedence).
    Exported for use in wedding_graph.py.
    """
    merged = dict(existing)
    for key in ("personality", "vibe", "events"):
        new_list = new.get(key) or []
        if new_list:
            existing_list = merged.get(key) or []
            merged[key] = list(dict.fromkeys(existing_list + new_list))
    for key in ("budget", "vendors"):
        new_dict = new.get(key) or {}
        if new_dict:
            merged[key] = {**(merged.get(key) or {}), **new_dict}
    # guestCount: keep the non-zero value (or the larger if both set)
    new_gc = new.get("guestCount")
    existing_gc = merged.get("guestCount")
    if new_gc and isinstance(new_gc, (int, float)) and new_gc > 0:
        if not existing_gc or new_gc > existing_gc:
            merged["guestCount"] = int(new_gc)
    return merged

