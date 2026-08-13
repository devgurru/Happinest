"""
Data Extractor — AI Call 1.

Extracts and validates stage-specific data from the user message.
Returns a typed ExtractionResult with a validated memory patch and early signals.
No conversational reply is generated here — only data extraction.

Simplified: per-stage sanitization removed — agent handles validation in prompt.
Kept: schema whitelist guard, event normalization, vendor parsing.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from app.domain.enums import EventType, StageId
from app.services.ai.ai_gateway import AIGatewayError, call_llm

logger = logging.getLogger(__name__)

# ─── Valid event names for normalisation ──────────────────────────────────────
_CANONICAL_EVENTS = {
    "mehendi": "Mehndi", "mehndi": "Mehndi", "haldi": "Haldi",
    "sangeet": "Sangeet", "reception": "Reception", "engagement": "Engagement",
    "wedding ceremony": "Wedding Ceremony", "nikah": "Nikah",
    "cocktail": "Cocktail Party", "cocktail party": "Cocktail Party",
}

_VALID_META_INTENTS = {"normal", "help", "more_suggestions", "clarification", "correction", "gibberish"}


# ─── Extraction Result ─────────────────────────────────────────────────────────

@dataclass
class ExtractionResult:
    """Typed result from AI Call 1 (data extractor)."""
    validated_patch: dict = field(default_factory=dict)
    early_signals: dict = field(default_factory=lambda: {
        "personality": [], "vibe": [], "events": [], "budget": {}, "vendors": {}
    })
    meta_intent: str = "normal"
    corrected_section: str | None = None
    validation_notes: dict = field(default_factory=dict)
    extraction_summary: str = ""
    more_vendors_for: str | None = None
    vendor_selections: dict = field(default_factory=dict)
    vendors_confirmed: bool = False

    def is_meta(self) -> bool:
        return self.meta_intent in ("help", "more_suggestions", "gibberish")

    def has_new_data(self) -> bool:
        return bool(self.validated_patch)

    def has_early_signals(self) -> bool:
        es = self.early_signals
        return bool(
            es.get("personality") or es.get("vibe") or es.get("events")
            or es.get("budget") or es.get("vendors")
        )

    @classmethod
    def from_dict(cls, raw: dict, *, stage: str = "", memory: dict | None = None) -> "ExtractionResult":
        """Parse and validate the raw dict from LLM."""
        memory = memory or {}

        meta_intent = str(raw.get("metaIntent") or "normal").lower().strip()
        if meta_intent not in _VALID_META_INTENTS:
            meta_intent = "normal"

        validated_patch = raw.get("validatedPatch") or {}
        if not isinstance(validated_patch, dict):
            validated_patch = {}
        if meta_intent in ("help", "more_suggestions", "gibberish"):
            validated_patch = {}

        # Normalise early signals
        raw_es = raw.get("earlySignals") or {}
        early_signals = {
            "personality": _clean_string_list(raw_es.get("personality"))[:3],
            "vibe": _clean_string_list(raw_es.get("vibe"))[:3],
            "events": _normalise_events(raw_es.get("events")),
            "budget": raw_es.get("budget") if isinstance(raw_es.get("budget"), dict) else {},
            "vendors": raw_es.get("vendors") if isinstance(raw_es.get("vendors"), dict) else {},
        }
        # guestCount
        raw_gc = raw_es.get("guestCount")
        if raw_gc is not None and raw_gc != "" and raw_gc is not False:
            try:
                gc_int = int(float(str(raw_gc)))
                if gc_int > 0:
                    early_signals["guestCount"] = gc_int
            except (ValueError, TypeError):
                pass

        if meta_intent in ("help", "gibberish"):
            early_signals = {"personality": [], "vibe": [], "events": [], "budget": {}, "vendors": {}}
        elif meta_intent == "more_suggestions":
            # Preserve moreVendorsFor on more_suggestions turns
            early_signals = {"personality": [], "vibe": [], "events": [], "budget": {}, "vendors": {}}

        # Parse vendor-specific S10 fields
        more_vendors_for = raw_es.get("moreVendorsFor")
        if more_vendors_for and not isinstance(more_vendors_for, str):
            more_vendors_for = None
        vendor_selections = raw_es.get("vendorSelections") or {}
        if not isinstance(vendor_selections, dict):
            vendor_selections = {}
        vendors_confirmed = bool(raw_es.get("vendorsConfirmed", False))

        # Normalise events in validated patch
        logistics = validated_patch.get("logistics") or {}
        if isinstance(logistics, dict) and "events" in logistics:
            logistics["events"] = _normalise_events(logistics.get("events"))
            validated_patch["logistics"] = logistics

        # Sanitize patch against canonical schema
        validated_patch = _sanitize_extracted_patch(validated_patch, stage=stage)
        validated_patch = _remove_empty(validated_patch)

        corrected_section = raw.get("correctedSection")
        if corrected_section and not isinstance(corrected_section, str):
            corrected_section = None

        validation_notes = raw.get("validationNotes") or {}
        if not isinstance(validation_notes, dict):
            validation_notes = {}

        extraction_summary = str(raw.get("extractionSummary") or "").strip()
        if not extraction_summary:
            if validated_patch:
                extraction_summary = f"Extracted {list(validated_patch.keys())} data."
            elif meta_intent != "normal":
                extraction_summary = f"Meta turn: {meta_intent}."
            else:
                extraction_summary = "No new data extracted."

        return cls(
            validated_patch=validated_patch,
            early_signals=early_signals,
            meta_intent=meta_intent,
            corrected_section=corrected_section,
            validation_notes=validation_notes,
            extraction_summary=extraction_summary,
            more_vendors_for=more_vendors_for,
            vendor_selections=vendor_selections,
            vendors_confirmed=vendors_confirmed,
        )

    @classmethod
    def empty(cls, meta_intent: str = "normal", summary: str = "") -> "ExtractionResult":
        return cls(meta_intent=meta_intent, extraction_summary=summary or "Extraction unavailable.")


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _clean_string_list(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if isinstance(item, str) and str(item).strip()]


def _normalise_events(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    normalised = []
    for item in raw:
        if not isinstance(item, str):
            continue
        key = item.strip().lower()
        normalised.append(_CANONICAL_EVENTS.get(key, item.strip().title()))
    return list(dict.fromkeys(normalised))


def _sanitize_extracted_patch(patch: dict, stage: str | None = None) -> dict:
    """
    Sanitize AI Call 1 patch against canonical memory schema.
    Prevents database pollution by filtering unknown keys and relocating misplaced fields.
    """
    if not isinstance(patch, dict):
        return {}

    valid_sections = {
        "identity", "occasion", "personality", "vibe",
        "brief", "direction", "logistics", "summary"
    }
    valid_keys = {
        "identity": {"groomName", "brideName", "displayName", "occasionType"},
        "occasion": {
            "place", "locationPreference", "settingPreference",
            "datePreference", "seasonPreference", "destinationMode",
            "specificityLevel", "country"
        },
        "personality": {"tags", "culturalSignals", "relationshipSignals", "lifestyleSignals", "plannerInterpretation"},
        "vibe": {"primaryVibe", "secondaryVibes", "energyLevel", "formality", "familyRole", "plannerInterpretation"},
        "brief": {"status", "text", "quote", "version", "generatedFromMemoryVersion"},
        "direction": {"status", "selectedDirectionId", "options", "seenOptionIds", "selectedDirectionName", "version", "generatedFromMemoryVersion"},
        "logistics": {"events", "guestCounts", "budget", "vendorPreferences", "vendorSelections", "vendorOffsets", "eventsConfirmed"},
        "summary": {"status", "text", "version", "generatedFromMemoryVersion"}
    }

    # Move misplaced logistics / early signal keys from occasion
    occasion = patch.get("occasion")
    if isinstance(occasion, dict):
        for key in ("events", "guestCounts", "budget", "vendorPreferences", "eventsConfirmed"):
            if key in occasion:
                val = occasion.pop(key)
                if key == "budget" and stage != StageId.S9_BUDGET.value:
                    es = patch.setdefault("earlySignals", {})
                    if isinstance(es, dict) and not es.get("budget"):
                        es["budget"] = val
                else:
                    logistics = patch.setdefault("logistics", {})
                    if isinstance(logistics, dict) and key not in logistics:
                        logistics[key] = val



    sanitized = {}
    for sec, val in patch.items():
        if sec in valid_sections and isinstance(val, dict):
            sec_keys = valid_keys[sec]
            sanitized_sec = {k: v for k, v in val.items() if k in sec_keys}
            if sanitized_sec:
                sanitized[sec] = sanitized_sec

    return sanitized


def parse_vendor_preferences_by_event(message: str, events: list[str]) -> dict[str, list[str]]:
    """Parse event-centric vendor preferences from message."""
    if not message or not isinstance(message, str):
        return {}

    events_map = {str(e).strip().lower(): str(e).strip() for e in events if isinstance(e, str)}
    res: dict[str, list[str]] = {}

    for sec in re.split(r"[;\n]", message):
        if ":" in sec:
            parts = sec.split(":", 1)
            raw_ev = parts[0].strip().lower()
            raw_vendors = parts[1].strip()
            matched = events_map.get(raw_ev)
            if not matched:
                for k_low, k_actual in events_map.items():
                    if k_low in raw_ev or raw_ev in k_low:
                        matched = k_actual
                        break
            if matched:
                vendor_list = [v.strip() for v in raw_vendors.split(",") if v.strip()]
                if vendor_list:
                    res[matched] = vendor_list
    return res


def _remove_empty(d: dict) -> dict:
    """Recursively remove empty dicts and empty lists."""
    result = {}
    for k, v in d.items():
        if isinstance(v, dict):
            cleaned = _remove_empty(v)
            if cleaned:
                result[k] = cleaned
        elif isinstance(v, list):
            if v:
                result[k] = v
        elif v is not None and v != "":
            result[k] = v
    return result


# ─── Main extraction call ──────────────────────────────────────────────────────

async def extract_and_validate(
    stage: str,
    memory: dict,
    user_message: str,
) -> ExtractionResult:
    """
    AI Call 1: Extract and validate stage-specific data from the user message.
    """
    from app.services.ai.prompt_builder import build_data_extraction_prompt

    messages = build_data_extraction_prompt(stage, memory, user_message)

    try:
        raw, _telemetry = await call_llm(messages, stage, EventType.CONVERSATION_TURN.value)
    except AIGatewayError as e:
        logger.warning("Data extraction AI call failed: %s — %s", e.code, e.message)
        return ExtractionResult.empty(
            meta_intent="normal",
            summary="Extraction call failed — proceeding with empty patch.",
        )
    except Exception as e:
        logger.error("Unexpected error in extract_and_validate: %s", e)
        return ExtractionResult.empty(summary="Unexpected extraction error.")

    if not isinstance(raw, dict):
        return ExtractionResult.empty(summary="Extraction returned non-dict response.")

    return ExtractionResult.from_dict(raw, stage=stage, memory=memory)
