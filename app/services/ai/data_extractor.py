"""
Data Extractor — AI Call 1.

Extracts and validates stage-specific data from the user message.
Returns a typed ExtractionResult with a validated memory patch and early signals.
No conversational reply is generated here — only data extraction.
"""
from __future__ import annotations

import copy
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from app.domain.enums import EventType, StageId
from app.services.ai.ai_gateway import AIGatewayError, call_llm

logger = logging.getLogger(__name__)

# ─── Valid event names for normalisation ──────────────────────────────────────
_CANONICAL_EVENTS = {
    "mehendi": "Mehndi",
    "mehndi": "Mehndi",
    "haldi": "Haldi",
    "sangeet": "Sangeet",
    "reception": "Reception",
    "engagement": "Engagement",
    "wedding ceremony": "Wedding Ceremony",
    "nikah": "Nikah",
    "cocktail": "Cocktail Party",
    "cocktail party": "Cocktail Party",
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

    # ── derived helpers ────────────────────────────────────────────────────────

    def is_meta(self) -> bool:
        """True for help / more_suggestions / gibberish turns (no data content)."""
        return self.meta_intent in ("help", "more_suggestions", "gibberish")

    def has_new_data(self) -> bool:
        """True if any canonical data was extracted."""
        return bool(self.validated_patch)

    def has_early_signals(self) -> bool:
        """True if any early-signal data was captured."""
        es = self.early_signals
        return bool(
            es.get("personality") or es.get("vibe") or es.get("events")
            or es.get("budget") or es.get("vendors")
        )

    # ── factory ───────────────────────────────────────────────────────────────

    @classmethod
    def from_dict(cls, raw: dict, *, stage: str = "", memory: dict | None = None, user_message: str = "") -> "ExtractionResult":
        """
        Parse and validate the raw dict from LLM.
        All missing / bad fields fall back to safe defaults.
        """
        memory = memory or {}

        meta_intent = str(raw.get("metaIntent") or "normal").lower().strip()
        if meta_intent not in _VALID_META_INTENTS:
            meta_intent = "normal"

        # For meta turns, patch must be empty
        validated_patch = raw.get("validatedPatch") or {}
        if not isinstance(validated_patch, dict):
            validated_patch = {}
        if meta_intent in ("help", "more_suggestions", "gibberish"):
            validated_patch = {}

        # Normalise early signals (capped to max 3 relevant items for personality/vibe)
        raw_es = raw.get("earlySignals") or {}
        early_signals = {
            "personality": _clean_string_list(raw_es.get("personality"))[:3],
            "vibe": _clean_string_list(raw_es.get("vibe"))[:3],
            "events": _normalise_events(raw_es.get("events")),
            "budget": raw_es.get("budget") if isinstance(raw_es.get("budget"), dict) else {},
            "vendors": raw_es.get("vendors") if isinstance(raw_es.get("vendors"), dict) else {},
        }
        # guestCount: extract as integer if positive
        raw_gc = raw_es.get("guestCount")
        if raw_gc is not None and raw_gc != "" and raw_gc is not False:
            try:
                gc_int = int(float(str(raw_gc)))
                if gc_int > 0:
                    early_signals["guestCount"] = gc_int
            except (ValueError, TypeError):
                pass

        # For meta turns, clear early signals too
        if meta_intent in ("help", "more_suggestions", "gibberish"):
            early_signals = {"personality": [], "vibe": [], "events": [], "budget": {}, "vendors": {}}

        # Stage-specific sanitisation
        validated_patch = _sanitise_patch_for_stage(validated_patch, stage, raw, memory, user_message=user_message)

        # Re-evaluate meta_intent after stage sanitisation
        meta_intent = str(raw.get("metaIntent") or "normal").lower().strip()
        if meta_intent not in _VALID_META_INTENTS:
            meta_intent = "normal"
        if meta_intent in ("help", "more_suggestions", "gibberish"):
            validated_patch = {}
            early_signals = {"personality": [], "vibe": [], "events": [], "budget": {}, "vendors": {}}

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
        )

    @classmethod
    def empty(cls, meta_intent: str = "normal", summary: str = "") -> "ExtractionResult":
        """Safe empty result for error fallbacks."""
        return cls(meta_intent=meta_intent, extraction_summary=summary or "Extraction unavailable.")


# ─── Stage-level sanitisation helpers ─────────────────────────────────────────

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
    return list(dict.fromkeys(normalised))  # deduplicate, preserve order


def _sanitise_patch_for_stage(patch: dict, stage: str, raw: dict, memory: dict, user_message: str = "") -> dict:
    """Apply hard backend rules to the extracted patch."""
    from datetime import date
    today = date.today()

    if not isinstance(patch, dict):
        patch = {}

    # S2_BASICS: process extracted specificityLevel
    if stage == StageId.S2_BASICS.value:
        from app.utils.validators import classify_s2_info_level, looks_like_gibberish
        validation_notes = raw.get("validationNotes") or {}
        extracted_level = str(
            validation_notes.get("specificityLevel")
            or validation_notes.get("informationLevel")
            or ""
        ).strip().upper()

        occ_patch = dict(patch.get("occasion") or {})
        memory_occ = memory.get("occasion") or {}
        merged_occ = {**memory_occ, **occ_patch}

        raw_meta = str(raw.get("metaIntent") or "").lower().strip()
        msg_text = (user_message or "").strip()

        from app.utils.validators import infer_country_from_place
        inferred_country = infer_country_from_place(msg_text) or infer_country_from_place(occ_patch.get("place") or "")

        if inferred_country:
            occ_patch["country"] = inferred_country
            if not occ_patch.get("locationPreference") and not occ_patch.get("place"):
                occ_patch["locationPreference"] = inferred_country

        is_country_query = bool(inferred_country)
        is_gibberish_turn = not is_country_query and (
            raw_meta == "gibberish"
            or extracted_level == "L0"
            or (bool(msg_text) and looks_like_gibberish(msg_text))
        )

        prior_spec_level = memory_occ.get("specificityLevel") or classify_s2_info_level(memory_occ)

        if is_gibberish_turn:
            spec_level = "L0"
            raw["metaIntent"] = "gibberish"
            if not patch.get("identity"):
                patch = {}
            occ_patch = {**memory_occ, "specificityLevel": "L0"}
        else:
            if is_country_query and raw_meta in ("gibberish", "clarification"):
                raw["metaIntent"] = "normal"
            from app.utils.validators import KNOWN_CITIES
            backend_level = classify_s2_info_level(merged_occ, user_message=user_message)
            if is_country_query and backend_level == "L0":
                backend_level = "IL1"
            
            # Format place string: e.g. "beach destination, Goa"
            prior_place = (memory_occ.get("place") or memory_occ.get("settingPreference") or "").strip()
            new_place = (occ_patch.get("place") or "").strip()

            is_new_city = any(city in new_place.lower() for city in KNOWN_CITIES) if new_place else False
            if is_new_city:
                backend_level = "IL3"
                broad_prefix = (memory_occ.get("settingPreference") or memory_occ.get("locationPreference") or "").strip()
                if not broad_prefix and "," in prior_place:
                    broad_prefix = prior_place.split(",")[0].strip()
                
                if broad_prefix and broad_prefix.lower() != new_place.lower():
                    occ_patch["place"] = f"{broad_prefix}, {new_place}"
                else:
                    occ_patch["place"] = new_place
            elif prior_place and new_place and new_place.lower() not in prior_place.lower():
                occ_patch["place"] = f"{prior_place}, {new_place}"

            spec_level = backend_level if backend_level in ("IL2", "IL3") else (extracted_level if extracted_level in ("L0", "IL1", "IL1_FLEXIBLE", "IL2", "IL3") else backend_level)
            if is_country_query and spec_level == "L0":
                spec_level = "IL1"

            # If prior turn was IL1 (broad setting prompt was asked on turn 1)
            if prior_spec_level == "IL1":
                new_setting = (occ_patch.get("settingPreference") or occ_patch.get("place") or "").strip().lower()
                old_setting = (memory_occ.get("settingPreference") or memory_occ.get("place") or "").strip().lower()
                is_new_broad_setting = bool(new_setting) and (new_setting != old_setting) and spec_level == "IL1"

                if is_new_broad_setting:
                    # User changed to a new broad setting on turn 2 -> Treat as IL1 for new setting (STAY ON S2 ONCE)
                    spec_level = "IL1"
                elif spec_level in ("IL2", "IL3"):
                    # User provided refined/exact location -> Keep IL2/IL3 (ADVANCE)
                    pass
                else:
                    # User confirmed/passed/responded to prompt -> Transition to IL1_FLEXIBLE (ADVANCE)
                    spec_level = "IL1_FLEXIBLE"
                    if raw_meta in ("clarification",):
                        raw["metaIntent"] = "normal"

        occ_patch["specificityLevel"] = spec_level
        patch["occasion"] = occ_patch

    # Validate occasion dates and resolve country across ALL stages
    occasion = dict(patch.get("occasion") or {})
    if occasion and isinstance(occasion, dict):
        # Reject past dates
        date_pref = occasion.get("datePreference") or ""
        if date_pref:
            from app.utils.validators import is_past_date
            try:
                if is_past_date(date_pref):
                    occasion.pop("datePreference", None)
                    val_notes = raw.setdefault("validationNotes", {})
                    val_notes["isPastDate"] = True
                    val_notes["rejectedDate"] = date_pref
            except Exception:
                pass  # Keep date if validator fails — backend safe default

        # Resolve country from LLM validationNotes or city lookup helper
        place = (occasion.get("place") or "").strip()
        validation_notes = raw.get("validationNotes") or {}
        resolved_country = (validation_notes.get("resolvedCountry") or "").strip()

        from app.utils.validators import infer_country_from_place
        inferred = infer_country_from_place(place)
        country = resolved_country or inferred

        if country:
            occasion["country"] = country

        if occasion:
            patch["occasion"] = occasion

    # S3: reject non-personality tags & preserve all user-selected/submitted chips
    if stage == StageId.S3_PERSONALITY.value:
        personality = patch.get("personality") or {}
        if not isinstance(personality, dict):
            personality = {}

        tags = list(personality.get("tags") or [])

        # Recover any personality or vibe earlySignals extracted on S3 turn
        early = raw.get("earlySignals") or {}
        if isinstance(early, dict):
            early_p = early.get("personality") or []
            if isinstance(early_p, list):
                tags.extend(early_p)
            early_v = early.get("vibe") or []
            if isinstance(early_v, list):
                tags.extend(early_v)

        # Parse user message for comma-separated chip inputs (e.g. "Foodies, Vibrant Cultural Celebration")
        if user_message:
            raw_parts = [p.strip() for p in user_message.split(",") if p.strip()]
            from app.utils.validators import is_junk_tag
            for part in raw_parts:
                if not is_junk_tag(part) and part not in tags:
                    tags.append(part)

        from app.utils.validators import filter_tags
        personality["tags"] = filter_tags(tags)[:5]
        if personality["tags"]:
            patch["personality"] = personality

    # S4: ensure primaryVibe is valid & cap secondaryVibes to max 3
    if stage == StageId.S4_VIBE.value:
        vibe = patch.get("vibe") or {}
        if isinstance(vibe, dict):
            primary = (vibe.get("primaryVibe") or "").strip()
            from app.utils.validators import is_valid_primary_vibe
            if primary and not is_valid_primary_vibe(primary):
                vibe.pop("primaryVibe", None)
            secondary = vibe.get("secondaryVibes") or []
            if isinstance(secondary, list):
                vibe["secondaryVibes"] = secondary[:3]
            patch["vibe"] = vibe

    # S7: event list confirmation: set eventsConfirmed to False whenever user adds/updates events or dissents
    if stage == StageId.S7_EVENTS.value:
        logistics = patch.get("logistics") or {}
        if isinstance(logistics, dict):
            msg_low = (user_message or "").strip().lower()
            add_or_update = any(re.search(pat, msg_low) for pat in [
                r"\badd\b", r"\bupdate\b", r"\bchange\b", r"\bdelete\b", r"\bremove\b",
                r"\bmore\b", r"\banother\b", r"\binclude\b", r"^\s*no\b", r"\bnope\b", r"\bnot yet\b", r"\bwait\b"
            ])
            if add_or_update:
                logistics["eventsConfirmed"] = False
            elif "eventsConfirmed" not in logistics:
                logistics["eventsConfirmed"] = False
            patch["logistics"] = logistics

    # S8: sanitise guest counts against confirmed events
    if stage == StageId.S8_GUESTS.value:
        logistics = patch.get("logistics") or {}
        if isinstance(logistics, dict):
            counts = logistics.get("guestCounts") or {}
            if isinstance(counts, dict):
                cleaned_counts = {}
                events_in_mem = (memory.get("logistics") or {}).get("events") or []
                events_map = {str(e).lower(): e for e in events_in_mem}
                for k, v in counts.items():
                    try:
                        val_int = int(float(str(v)))
                        if val_int > 0:
                            matched_ev = events_map.get(str(k).lower().strip(), str(k).strip().title())
                            cleaned_counts[matched_ev] = val_int
                    except (ValueError, TypeError):
                        pass
                if cleaned_counts:
                    logistics["guestCounts"] = cleaned_counts
                    patch["logistics"] = logistics

    # S10: sanitise vendor preferences to be EventName -> list of vendor strings
    if stage == StageId.S10_VENDORS.value or "vendorPreferences" in (patch.get("logistics") or {}):
        logistics = patch.get("logistics") or {}
        if isinstance(logistics, dict):
            events_in_mem = (memory.get("logistics") or {}).get("events") or []
            events_map = {str(e).strip().lower(): str(e).strip() for e in events_in_mem if isinstance(e, str)}

            parsed_vp = parse_vendor_preferences_by_event(user_message, events_in_mem)

            raw_vp = logistics.get("vendorPreferences") or {}
            cleaned_vp: dict[str, list[str]] = {}

            if isinstance(raw_vp, dict):
                for k, v in raw_vp.items():
                    matched_ev = events_map.get(str(k).strip().lower())
                    if matched_ev:
                        if isinstance(v, list):
                            cleaned_vp[matched_ev] = [str(item).strip() for item in v if str(item).strip()]
                        elif isinstance(v, str) and v.strip():
                            cleaned_vp[matched_ev] = [s.strip() for s in v.split(",") if s.strip()]

            for ev_name, v_list in parsed_vp.items():
                cleaned_vp[ev_name] = v_list

            if cleaned_vp:
                logistics["vendorPreferences"] = cleaned_vp
                patch["logistics"] = logistics

    # Remove empty nested dicts / empty lists from patch
    patch = _remove_empty(patch)
    return patch


def parse_vendor_preferences_by_event(message: str, events: list[str]) -> dict[str, list[str]]:
    """
    Parse event-centric vendor preferences string from message into dict[str, list[str]].
    Format: "Mehndi: Catering, Photography, Mehendi artist; Sangeet: Stage and sound, DJ"
    """
    if not message or not isinstance(message, str):
        return {}

    events_map = {str(e).strip().lower(): str(e).strip() for e in events if isinstance(e, str)}
    res: dict[str, list[str]] = {}

    sections = re.split(r"[;\n]", message)
    for sec in sections:
        if ":" in sec:
            parts = sec.split(":", 1)
            raw_ev = parts[0].strip().lower()
            raw_vendors = parts[1].strip()

            matched_event = events_map.get(raw_ev)
            if not matched_event:
                for k_low, k_actual in events_map.items():
                    if k_low in raw_ev or raw_ev in k_low:
                        matched_event = k_actual
                        break

            if matched_event:
                vendor_list = [v.strip() for v in raw_vendors.split(",") if v.strip()]
                if vendor_list:
                    res[matched_event] = vendor_list

    return res


def _remove_empty(d: dict) -> dict:
    """Recursively remove empty dicts and empty lists from a dict."""
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

    Returns an ExtractionResult with:
    - validated_patch: safe to write to memory for the current stage
    - early_signals: data for future stages mentioned in this message
    - meta_intent: what the user is actually doing (normal / help / etc.)
    - extraction_summary: one-line summary for the response planner prompt
    """
    from app.services.ai.prompt_builder import build_data_extraction_prompt

    messages = build_data_extraction_prompt(stage, memory, user_message)

    try:
        raw, _telemetry = await call_llm(messages, stage, EventType.CONVERSATION_TURN.value)
    except AIGatewayError as e:
        logger.warning("Data extraction AI call failed: %s — %s", e.code, e.message)
        # Safe fallback: treat as normal turn with no data extracted
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
