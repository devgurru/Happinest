"""
Turn Interpreter — deterministic enrichment of AI memory patches from user text.
Backend owns canonical memory; this fills gaps when the model misses structured fields.
"""
from __future__ import annotations

import re

from app.domain.chip_pools import get_chip_pool
from app.domain.enums import StageId
from app.services.text_extract import (
    extract_early_signals,
    extract_month_or_season,
    filter_tags,
    sanitize_timing_fields,
)
from app.services.turn_intent import _extract_added_tags
from app.services.ui_hints import build_vendor_chip_pool, chips_mentioned_in_message
from app.services.stage_policy import StagePolicy


def find_direction_option_in_message(message: str, memory: dict) -> dict | None:
    """Match user text to a stored direction option (longest name wins)."""
    options = (memory.get("direction") or {}).get("options") or []
    msg_l = message.lower().strip()
    best: dict | None = None
    best_len = 0
    for opt in options:
        name = (opt.get("name") or "").strip()
        slug = (opt.get("id") or "").strip()
        name_l = name.lower()
        slug_l = slug.lower().replace("-", " ")
        if name_l and name_l in msg_l and len(name_l) > best_len:
            best = opt
            best_len = len(name_l)
        elif slug_l and slug_l in msg_l and len(slug_l) > best_len:
            best = opt
            best_len = len(slug_l)
    return best


def direction_selection_patch(message: str, memory: dict) -> dict | None:
    """Build direction selection patch when user names a listed option."""
    opt = find_direction_option_in_message(message, memory)
    if not opt:
        return None
    return {
        "direction": {
            "selectedDirectionId": opt.get("id"),
            "selectedDirectionName": opt.get("name"),
            "status": "selected",
        }
    }


def _merge_unique(base: list, extra: list) -> list:
    seen: set[str] = set()
    merged: list = []
    for item in base + extra:
        if not item:
            continue
        key = item.lower().strip()
        if key in seen:
            continue
        seen.add(key)
        merged.append(item.strip())
    return filter_tags(merged)


def _extract_budget_range(message: str) -> str | None:
    patterns = [
        r"(\d+\s*[-–to]+\s*\d+\s*lakh[s]?)",
        r"(around\s+\d+\s*lakh[s]?)",
        r"(\d+\s*lakh[s]?\s*(?:total|budget)?)",
        r"(₹\s*\d+[\d,\.]*\s*(?:lakhs?|L|cr)?)",
    ]
    for pattern in patterns:
        match = re.search(pattern, message, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return None


def _extract_guest_counts(message: str, events: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for event in events:
        pattern = rf"{re.escape(event)}[^0-9]*?(\d{{2,4}})"
        match = re.search(pattern, message, re.IGNORECASE)
        if match:
            counts[event] = int(match.group(1))
    if not counts:
        for match in re.finditer(r"(\d{2,4})\s*(?:guests|people)", message, re.IGNORECASE):
            if events:
                counts.setdefault(events[0], int(match.group(1)))
                break
    return counts


def merge_patches(base: dict, extra: dict) -> dict:
    """Shallow-deep merge for patch dicts."""
    result = dict(base)
    for key, val in extra.items():
        if isinstance(val, dict) and isinstance(result.get(key), dict):
            result[key] = {**result[key], **val}
        else:
            result[key] = val
    return result


def _scrub_ai_personality_tags(patch: dict) -> None:
    """Drop junk / date tags the model may have stuffed into personality."""
    pers = patch.get("personality")
    if isinstance(pers, dict) and pers.get("tags"):
        pers["tags"] = filter_tags(pers["tags"])
        if not pers["tags"]:
            pers.pop("tags", None)
        if not pers:
            patch.pop("personality", None)


def enrich_memory_patch(stage: str, user_message: str, patch: dict, memory: dict) -> dict:
    """Merge deterministic signals into the AI-proposed patch."""
    patch = dict(patch or {})
    message = user_message.strip()
    if not message:
        return patch

    try:
        stage_id = StageId(stage)
    except ValueError:
        return patch

    # S6: direction pick beats occasion/AI bleed (e.g. "Delhi Rooftop" ≠ city)
    if stage_id == StageId.S6_DIRECTIONS:
        sel = direction_selection_patch(message, memory)
        if sel:
            patch = {**patch, **sel}
            patch.pop("occasion", None)
            patch.pop("personality", None)
            patch.pop("vibe", None)
            return patch

    # Always scrub junk tags the model may invent
    _scrub_ai_personality_tags(patch)
    if isinstance(patch.get("occasion"), dict):
        patch["occasion"] = sanitize_timing_fields(patch["occasion"])

    # Concrete month spoken on the wrong stage → occasion, never personality
    timing = extract_month_or_season(message)
    if timing and stage_id != StageId.S2_BASICS:
        # Pure date answer while on S3/S4 (common when S2 wrongly advanced)
        msg_l = message.lower().strip()
        mostly_date = bool(
            re.fullmatch(
                r"(i\s+think\s+in\s+|in\s+|around\s+|maybe\s+)?"
                r"(january|february|march|april|may|june|july|august|september|october|november|december)"
                r"(\s+\d{4})?\.?",
                msg_l,
            )
        ) or (timing.get("datePreference") and len(message.split()) <= 6)
        if mostly_date or stage_id in (StageId.S3_PERSONALITY, StageId.S4_VIBE):
            occ = dict(patch.get("occasion") or memory.get("occasion") or {})
            occ.update(timing)
            patch["occasion"] = sanitize_timing_fields(occ)
            if mostly_date and stage_id == StageId.S3_PERSONALITY:
                # Do not treat this turn as a personality commitment
                patch.pop("personality", None)

    if stage_id == StageId.S2_BASICS:
        occasion = dict(patch.get("occasion") or memory.get("occasion") or {})
        if not occasion.get("place"):
            for city in (
                "Delhi", "Mumbai", "Udaipur", "Jaipur", "Goa", "Bangalore",
                "Chennai", "Hyderabad", "Kolkata", "Agra", "Jodhpur",
            ):
                if city.lower() in message.lower():
                    occasion["place"] = city
                    break
        occasion.update(extract_month_or_season(message))
        if "destination" in message.lower():
            occasion["destinationMode"] = "destination"

        early = extract_early_signals(message)
        for k, v in (early.get("occasionHints") or {}).items():
            occasion.setdefault(k, v)

        occasion = sanitize_timing_fields(occasion)
        patch["occasion"] = occasion

        # Persist early personality/vibe hints without advancing those stages
        if early.get("personality") or early.get("vibe"):
            signals = dict(memory.get("earlySignals") or {})
            signals["personality"] = _merge_unique(
                signals.get("personality") or [], early.get("personality") or []
            )
            signals["vibe"] = _merge_unique(
                signals.get("vibe") or [], early.get("vibe") or []
            )
            signals["acknowledged"] = False
            patch["earlySignals"] = signals
            # Strip any personality/vibe the model prematurely wrote during S2
            patch.pop("personality", None)
            patch.pop("vibe", None)

    elif stage_id == StageId.S3_PERSONALITY:
        pool = get_chip_pool(stage)
        found = _extract_added_tags(message, pool)
        # Seed from early signals once if user confirms / hasn't committed yet
        early = memory.get("earlySignals") or {}
        early_tags = early.get("personality") or []
        personality = dict(patch.get("personality") or {})
        existing = memory.get("personality", {}).get("tags") or []

        is_correction = any(
            c in message.lower() for c in ("add ", "also", "actually", "change", "sorry", "update")
        )
        if "," in message and len(found) >= 2 and not is_correction:
            personality["tags"] = found
        elif found:
            personality["tags"] = _merge_unique(
                personality.get("tags") or [],
                _merge_unique(existing, found),
            )
        elif early_tags and not existing:
            # Suggest early signals into memory only when user affirms vaguely
            if any(w in message.lower() for w in ("yes", "that", "those", "sounds", "keep", "same")):
                personality["tags"] = filter_tags(early_tags)

        if personality.get("tags"):
            personality["tags"] = filter_tags(personality["tags"])
            patch["personality"] = personality
            if early_tags:
                patch["earlySignals"] = {
                    **early,
                    "acknowledged": True,
                    "personality": early_tags,
                }

    elif stage_id in (StageId.S4_VIBE, StageId.S6_DIRECTIONS):
        pool = get_chip_pool(stage)
        found = _extract_added_tags(message, pool) or chips_mentioned_in_message(message, pool)
        vibe = dict(patch.get("vibe") or {})
        mem_vibe = memory.get("vibe") or {}
        primary = vibe.get("primaryVibe") or mem_vibe.get("primaryVibe") or ""
        secondary = list(vibe.get("secondaryVibes") or mem_vibe.get("secondaryVibes") or [])
        early_vibe = (memory.get("earlySignals") or {}).get("vibe") or []
        is_add = any(c in message.lower() for c in ("add ", "also", "along with", "as well", "update my vibe"))
        add_m = re.search(r"(?:add|include)\s+(.+?)(?:\s+as well|\s+too|\s*$)", message, re.I)
        if is_add and add_m:
            extra = add_m.group(1).strip().strip(".")
            if extra and extra.lower() not in {s.lower() for s in secondary}:
                secondary.append(extra[0].upper() + extra[1:] if extra else extra)
            if primary:
                vibe["primaryVibe"] = primary
            if secondary:
                vibe["secondaryVibes"] = secondary
        elif found:
            if is_add and primary:
                for f in found:
                    if f.lower() != primary.lower() and f.lower() not in {s.lower() for s in secondary}:
                        secondary.append(f)
                vibe["primaryVibe"] = primary
                if secondary:
                    vibe["secondaryVibes"] = secondary
            else:
                vibe["primaryVibe"] = found[0]
                if len(found) > 1:
                    vibe["secondaryVibes"] = found[1:]
        elif "intimate" in message.lower() and not vibe.get("primaryVibe"):
            vibe["primaryVibe"] = "Intimate"
        elif "festive" in message.lower() and not vibe.get("primaryVibe"):
            vibe["primaryVibe"] = "Big & festive"
        elif "party" in message.lower() and not vibe.get("primaryVibe"):
            vibe["primaryVibe"] = "Big & festive"
        elif early_vibe and not vibe.get("primaryVibe") and not mem_vibe.get("primaryVibe"):
            if any(w in message.lower() for w in ("yes", "that", "those", "sounds", "keep", "same")):
                vibe["primaryVibe"] = early_vibe[0]
                if len(early_vibe) > 1:
                    vibe["secondaryVibes"] = early_vibe[1:]
        if vibe:
            patch["vibe"] = vibe

    elif stage_id == StageId.S7_EVENTS:
        pool = get_chip_pool(stage)
        found = chips_mentioned_in_message(message, pool)
        if "reception" in message.lower() and "Reception" not in found:
            found.append("Reception")
        logistics = dict(patch.get("logistics") or {})
        existing = memory.get("logistics", {}).get("events") or []
        events = _merge_unique(logistics.get("events") or [], _merge_unique(existing, found))
        if events:
            logistics["events"] = events
            if StagePolicy.events_finalize_cue(message):
                logistics["eventsConfirmed"] = True
            patch["logistics"] = logistics

    elif stage_id == StageId.S8_GUESTS:
        # Allow adding missing events while collecting guest counts
        pool = get_chip_pool(StageId.S7_EVENTS.value)
        found = chips_mentioned_in_message(message, pool)
        if "reception" in message.lower() and "Reception" not in found:
            found.append("Reception")
        events = memory.get("logistics", {}).get("events") or []
        logistics = dict(patch.get("logistics") or {})
        if found:
            events = _merge_unique(events, found)
            logistics["events"] = events
        counts = dict(logistics.get("guestCounts") or memory.get("logistics", {}).get("guestCounts") or {})
        counts.update(_extract_guest_counts(message, events))
        if counts:
            logistics["guestCounts"] = counts
        if logistics:
            patch["logistics"] = logistics
        # Never let S8 turns rewrite personality/vibe
        patch.pop("personality", None)
        patch.pop("vibe", None)

    elif stage_id == StageId.S9_BUDGET:
        budget_range = _extract_budget_range(message)
        if budget_range:
            logistics = dict(patch.get("logistics") or {})
            budget = dict(logistics.get("budget") or memory.get("logistics", {}).get("budget") or {})
            budget["range"] = budget_range
            budget.setdefault("currency", "INR")
            logistics["budget"] = budget
            patch["logistics"] = logistics

    elif stage_id == StageId.S10_VENDORS:
        pool = build_vendor_chip_pool(memory.get("logistics", {}).get("events") or [])
        found = chips_mentioned_in_message(message, pool)
        logistics = dict(patch.get("logistics") or {})
        prefs = dict(logistics.get("vendorPreferences") or memory.get("logistics", {}).get("vendorPreferences") or {})
        for chip in found:
            key = chip.lower().replace(" ", "_")
            prefs[key] = "requested"
        if "photograph" in message.lower():
            prefs["photography"] = message[:200]
        if "dj" in message.lower() or "sufi" in message.lower():
            prefs["entertainment"] = message[:200]
        if prefs:
            logistics["vendorPreferences"] = prefs
            patch["logistics"] = logistics

    elif stage_id == StageId.S6_DIRECTIONS:
        sel = direction_selection_patch(message, memory)
        if sel:
            patch.update(sel)

    # Cross-stage enrichment only when explicit correction language is present.
    _CORRECTION_WORDS = (
        "actually", "sorry", "change ", "update ", "instead", "meant",
        "wrong", "also want", "add ", "along with", "rather",
    )
    if any(w in message.lower() for w in _CORRECTION_WORDS):
        _apply_cross_stage_corrections(message, patch, memory, stage)

    return patch


def _apply_cross_stage_corrections(
    message: str,
    patch: dict,
    memory: dict,
    current_stage: str,
) -> None:
    """Apply upstream patches only for explicit corrections from later stages."""
    msg_l = message.lower()
    try:
        current = StageId(current_stage)
    except ValueError:
        return

    if current not in (StageId.S4_VIBE,) and "vibe" not in patch:
        if any(k in msg_l for k in ("vibe", "intimate", "festive", "classic village", "family-led")):
            pool = get_chip_pool(StageId.S4_VIBE.value)
            found = chips_mentioned_in_message(message, pool) or _extract_added_tags(message, pool)
            vibe: dict = {}
            if found:
                vibe["primaryVibe"] = found[0]
            elif "intimate" in msg_l:
                vibe["primaryVibe"] = "Intimate"
            elif "festive" in msg_l:
                vibe["primaryVibe"] = "Big & festive"
            elif "classic" in msg_l and "village" in msg_l:
                vibe["primaryVibe"] = "Classic village"
            if vibe.get("primaryVibe"):
                patch["vibe"] = vibe

    if current not in (StageId.S3_PERSONALITY,) and "personality" not in patch:
        if "personality" in msg_l or "add " in msg_l or "along with" in msg_l or "update " in msg_l:
            pool = get_chip_pool(StageId.S3_PERSONALITY.value)
            found = _extract_added_tags(message, pool)
            if found:
                existing = memory.get("personality", {}).get("tags") or []
                # Replace-mode when user says update personality to X
                if re.search(r"update\s+(?:my\s+|the\s+)?personality", msg_l) and "add " not in msg_l:
                    patch["personality"] = {"tags": filter_tags(found)}
                else:
                    patch["personality"] = {"tags": _merge_unique(existing, found)}

    if current not in (StageId.S2_BASICS,) and "occasion" not in patch:
        occasion: dict = {}
        if "beach" in msg_l:
            occasion["locationPreference"] = "beach"
            occasion["settingPreference"] = "beach"
        timing = extract_month_or_season(message)
        if timing and any(w in msg_l for w in ("move", "date", "actually", "change", "update", "instead")):
            occasion.update(timing)
        for city in ("delhi", "mumbai", "udaipur", "goa", "jaipur"):
            if city in msg_l and any(w in msg_l for w in ("move", "actually", "change", "instead", "update")):
                occasion["place"] = city.title()
                break
        if occasion:
            patch["occasion"] = sanitize_timing_fields(occasion)
