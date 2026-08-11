"""
Validators — pure validation/sanitization functions for memory data.

Data quality validators for location, personality tags, vibe, and S2 specificity.
Timing utilities → app/utils/timing_utils.py
AI response validation → app/utils/ai_response_validators.py
"""
from __future__ import annotations

import re

# Re-exports for backward compatibility
from app.utils.timing_utils import (  # noqa: F401
    MONTHS,
    VALID_SEASONS,
    VAGUE_TIMING,
    is_past_date,
    is_concrete_timing,
    resolve_relative_date,
    sanitize_timing_fields,
)
from app.utils.ai_response_validators import (  # noqa: F401
    sanitize_ai_response,
    validate_ai_response,
    validate_synthesis_response,
)


def infer_country_from_place(place: str) -> str:
    """Pass-through for country detection (LLM extraction owns country identification)."""
    return ""


# ─────────────────────────────────────────────────────────────────────────────
# S2 Specificity Classification
# ─────────────────────────────────────────────────────────────────────────────

S2_FLEXIBLE_PHRASES = (
    "nothing finalized", "not sure", "keep it flexible", "keep it broad",
    "decide later", "help me finalize later", "flexible", "skip", "no preference",
    "not decided", "to be decided", "undecided", "later", "anywhere",
    "somewhere in", "somewhere", "anywhere in", "any location", "open to suggestions",
    "open to ideas", "open to anything",
)


def classify_s2_info_level(memory_or_patch: dict, user_message: str = "") -> str:
    """
    Classify S2 input into exact specificity levels per requirement spec:
    - "L0": Junk / unusable / empty (stay on S2)
    - "IL1": Broad setting or season without specific place (stay on S2 once)
    - "IL1_FLEXIBLE": User consents to keep location flexible (advance to S3)
    - "IL2": Refined region/country + month/year (advance to S3)
    - "IL3": Exact city/venue + month/dates/year (advance to S3)
    """
    msg_l = (user_message or "").strip().lower()

    occ = memory_or_patch.get("occasion") if isinstance(memory_or_patch.get("occasion"), dict) else memory_or_patch
    if not isinstance(occ, dict):
        occ = {}

    place = (occ.get("place") or occ.get("locationPreference") or "").strip()
    setting = (occ.get("settingPreference") or "").strip()
    date_pref = (occ.get("datePreference") or "").strip()
    season_pref = (occ.get("seasonPreference") or "").strip()
    spec_level = (occ.get("specificityLevel") or "").strip().upper()

    if spec_level in ("IL1_FLEXIBLE", "IL2", "IL3"):
        return spec_level

    combined_text = f"{place} {setting} {date_pref} {season_pref} {msg_l}".lower()

    if any(phrase in combined_text for phrase in S2_FLEXIBLE_PHRASES):
        return "IL1_FLEXIBLE"

    has_specific_place = bool(place)
    has_setting = bool(setting)
    has_timing = bool(date_pref or season_pref)

    if not has_specific_place and not has_setting and not has_timing:
        return "L0"

    # Specific place + timing -> IL3 (exact) or IL2 (region)
    if has_specific_place:
        has_exact_date = bool(re.search(r"\b\d{1,2}(st|nd|rd|th)?\b|\b\d{1,2}\s*[-–—]\s*\d{1,2}\b", combined_text))
        has_month = any(m in combined_text for m in MONTHS) or bool(re.search(r"\b20\d{2}\b", combined_text))
        if len(place.split()) >= 2 or (has_exact_date or has_month):
            return "IL3"
        return "IL2"

    # Setting only (e.g. "beach", "mountains") or timing only WITHOUT a specific place -> IL1
    return "IL1"


def get_occasion_state(memory: dict, user_message: str = "") -> dict:
    """
    Resolved occasion place/timing from canonical occasion + legacy top-level fields.
    Evaluates specificityLevel for S2 stay/advance gate:
    - L0: stay on S2 (ask user to rephrase)
    - IL1: stay on S2 once (ask for region/place or flexible consent)
    - IL1_FLEXIBLE, IL2, IL3: advance to S3!
    """
    raw_occ = memory.get("occasion") if isinstance(memory, dict) and isinstance(memory.get("occasion"), dict) else memory
    occ = sanitize_timing_fields(dict(raw_occ or {}))

    place = (
        (occ.get("place") or "")
        or (occ.get("locationPreference") or "")
    ).strip()
    setting = (occ.get("settingPreference") or "").strip()

    has_place = bool(place)
    has_time = is_concrete_timing(occ)

    spec_level = classify_s2_info_level(occ, user_message=user_message)

    # IL1_FLEXIBLE, IL2, IL3 advance to S3; L0 and IL1 stay on S2
    if spec_level in ("IL1_FLEXIBLE", "IL2", "IL3"):
        is_complete = True
    else:
        is_complete = False

    occ["specificityLevel"] = spec_level

    return {
        "occasion": occ,
        "place": place or setting,
        "when": (occ.get("datePreference") or occ.get("seasonPreference") or "").strip(),
        "has_place": has_place,
        "has_time": has_time,
        "specificity_level": spec_level,
        "is_complete": is_complete,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Tag Quality & Gibberish Detection
# ─────────────────────────────────────────────────────────────────────────────

def looks_like_gibberish(text: str) -> bool:
    """Detect keyboard mash / nonsense."""
    if not text or not isinstance(text, str):
        return True
    t = text.strip()
    if len(t) < 2:
        return True
    low = t.lower()
    letters = re.sub(r"[^a-z]", "", low)
    if len(letters) >= 6:
        vowels = sum(1 for c in letters if c in "aeiou")
        if vowels / len(letters) < 0.18:
            return True
        if re.search(r"[bcdfghjklmnpqrstvwxyz]{6,}", letters):
            return True
    return False


def is_junk_tag(label: str) -> bool:
    """Validate tag string quality (length, non-empty, non-gibberish)."""
    if not label or not isinstance(label, str):
        return True
    text = label.strip()
    if len(text) < 2 or len(text) > 40:
        return True
    if looks_like_gibberish(text):
        return True
    return False


def filter_tags(tags: list) -> list[str]:
    """Dedupe, filter invalid tags, cap at 5 items."""
    out: list[str] = []
    seen: set[str] = set()
    for tag in tags or []:
        if not isinstance(tag, str):
            continue
        clean = tag.strip()
        if is_junk_tag(clean):
            continue
        key = clean.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(clean)
    return out[:5]


# ─────────────────────────────────────────────────────────────────────────────
# Vibe Validation & Normalization
# ─────────────────────────────────────────────────────────────────────────────

def extract_vibe_label(message: str) -> str | None:
    """Return stripped vibe label if non-empty and valid."""
    if message and isinstance(message, str) and not looks_like_gibberish(message):
        return message.strip()
    return None


def is_valid_primary_vibe(value: str) -> bool:
    """Check if vibe string is non-empty, reasonably sized, and not gibberish."""
    if not value or not isinstance(value, str):
        return False
    text = value.strip()
    if len(text) < 2 or len(text) > 50:
        return False
    return not looks_like_gibberish(text)


def normalize_primary_vibe(value: str | None, message: str = "") -> str | None:
    """Return clean vibe label."""
    if value and is_valid_primary_vibe(value):
        return value.strip()
    if message and is_valid_primary_vibe(message):
        return message.strip()
    return None
