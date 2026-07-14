"""
Shared text extraction helpers for occasion timing and chip tags.
Keeps junk (dates, sentences) out of personality/vibe chip lists.
"""
from __future__ import annotations

import re

MONTHS = (
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
)

VALID_SEASONS = (
    "winter", "summer", "monsoon", "spring", "autumn", "fall",
)

VAGUE_TIMING = (
    "cold weather", "cooler weather", "cold", "hot weather", "nice weather",
    "good weather", "beautiful weather", "pleasant weather", "sometime",
    "not sure", "flexible", "anytime",
)

_JUNK_TAG_PATTERNS = (
    r"^\s*i\s+",
    r"^\s*we\s+",
    r"\bthink\b",
    r"\bwant\b",
    r"\bhoping\b",
    r"\bprefer\b",
    r"\bnot sure\b",
    r"\bseptember\b",
    r"\bpreference\b",
)


def extract_month_or_season(message: str) -> dict:
    """
    Return {datePreference?} and/or {seasonPreference?} only for concrete timing.
    Vague phrases like 'cold weather' are ignored for completion.
    """
    msg_l = message.lower()
    result: dict = {}

    date_match = re.search(
        r"(early\s+|late\s+|mid\s+)?"
        r"(january|february|march|april|may|june|july|august|september|october|november|december)"
        r"(\s+\d{4})?",
        message,
        re.IGNORECASE,
    )
    if date_match:
        result["datePreference"] = date_match.group(0).strip().title()

    for season in VALID_SEASONS:
        if re.search(rf"\b{season}\b", msg_l):
            result["seasonPreference"] = season.title()
            break

    return result


def is_concrete_timing(occasion: dict) -> bool:
    """True only when date/season is concrete enough to complete S2."""
    date = (occasion.get("datePreference") or "").strip().lower()
    season = (occasion.get("seasonPreference") or "").strip().lower()

    if date:
        if any(vague in date for vague in VAGUE_TIMING):
            return False
        if any(m in date for m in MONTHS):
            return True
        # Year alone is not enough
        if re.fullmatch(r"\d{4}", date):
            return False

    if season:
        if any(vague in season for vague in VAGUE_TIMING):
            return False
        if any(s == season or s in season for s in VALID_SEASONS):
            return True

    return False


def sanitize_timing_fields(occasion: dict) -> dict:
    """Strip vague timing values that should not unlock S2 advance."""
    occ = dict(occasion)
    date = (occ.get("datePreference") or "").strip().lower()
    season = (occ.get("seasonPreference") or "").strip().lower()
    if date and (any(v in date for v in VAGUE_TIMING) or not any(m in date for m in MONTHS)):
        # Keep concrete months only
        extracted = extract_month_or_season(occ.get("datePreference") or "")
        if extracted.get("datePreference"):
            occ["datePreference"] = extracted["datePreference"]
        else:
            occ["datePreference"] = ""
    if season and not any(s in season for s in VALID_SEASONS):
        occ["seasonPreference"] = ""
    if season and any(v in season for v in VAGUE_TIMING) and not any(s in season for s in VALID_SEASONS):
        occ["seasonPreference"] = ""
    return occ


def is_junk_tag(label: str) -> bool:
    """Reject sentences, months, and other non-chip text from tag lists."""
    if not label or not isinstance(label, str):
        return True
    text = label.strip()
    if len(text) < 2 or len(text) > 40:
        return True
    words = text.split()
    if len(words) > 5:
        return True
    low = text.lower()
    if any(m in low for m in MONTHS):
        return True
    if re.search(r"\b(19|20)\d{2}\b", low):
        return True
    for pat in _JUNK_TAG_PATTERNS:
        if re.search(pat, low):
            return True
    # Reject bare preference/weather fluff
    if low in ("september preference", "cold weather", "beach", "goa"):
        return True
    return False


def filter_tags(tags: list) -> list[str]:
    """Dedupe and drop junk tags."""
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
    return out


def extract_early_signals(message: str) -> dict:
    """
    Capture personality/vibe hints mentioned outside their stages
    without advancing those stages.
    """
    from app.domain.chip_pools import get_chip_pool
    from app.domain.enums import StageId
    from app.services.ui_hints import chips_mentioned_in_message

    msg_l = message.lower()
    signals: dict = {"personality": [], "vibe": [], "occasionHints": {}}

    if "beach" in msg_l:
        signals["occasionHints"]["settingPreference"] = "beach"
        signals["occasionHints"]["locationPreference"] = "beach"
        signals["personality"].append("Beach lovers")

    personality_pool = get_chip_pool(StageId.S3_PERSONALITY.value)
    vibe_pool = get_chip_pool(StageId.S4_VIBE.value)
    signals["personality"].extend(chips_mentioned_in_message(message, personality_pool))
    signals["vibe"].extend(chips_mentioned_in_message(message, vibe_pool))

    if "party" in msg_l or "banger" in msg_l:
        signals["vibe"].append("Big & festive")
    if "food" in msg_l or "foodie" in msg_l:
        signals["personality"].append("Foodies")
    if "music" in msg_l:
        signals["personality"].append("Music-obsessed")

    signals["personality"] = filter_tags(signals["personality"])
    signals["vibe"] = filter_tags(signals["vibe"])
    return signals
