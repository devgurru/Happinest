"""
Validators — pure validation/sanitization functions for memory data.

Data quality validators for location, personality tags, vibe, and S2 specificity.
Timing utilities → app/utils/timing_utils.py
AI response validation → app/utils/ai_response_validators.py
"""
from __future__ import annotations

import re
from datetime import date as _date

# ─────────────────────────────────────────────────────────────────────────────
# Re-exports for backward compatibility — existing imports keep working
# ─────────────────────────────────────────────────────────────────────────────
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


from app.utils.timing_utils import KNOWN_CITIES

CITY_TO_COUNTRY_MAP: dict[str, str] = {
    "london": "United Kingdom",
    "uk": "United Kingdom",
    "england": "United Kingdom",
    "paris": "France",
    "france": "France",
    "lahore": "Pakistan",
    "lahore fort": "Pakistan",
    "karachi": "Pakistan",
    "islamabad": "Pakistan",
    "pakistan": "Pakistan",
    "delhi": "India",
    "new delhi": "India",
    "goa": "India",
    "mumbai": "India",
    "jaipur": "India",
    "udaipur": "India",
    "bangalore": "India",
    "bengaluru": "India",
    "chennai": "India",
    "hyderabad": "India",
    "kolkata": "India",
    "agra": "India",
    "jodhpur": "India",
    "pune": "India",
    "chandigarh": "India",
    "lucknow": "India",
    "ahmedabad": "India",
    "kochi": "India",
    "shimla": "India",
    "rishikesh": "India",
    "dubai": "UAE",
    "uae": "UAE",
    "abu dhabi": "UAE",
    "istanbul": "Turkey",
    "turkey": "Turkey",
    "bali": "Indonesia",
    "indonesia": "Indonesia",
    "rome": "Italy",
    "tuscany": "Italy",
    "italy": "Italy",
}


def infer_country_from_place(place: str) -> str:
    """Infer country name from location/city string."""
    if not place or not isinstance(place, str):
        return ""
    p_lower = place.strip().lower()
    for key, country in CITY_TO_COUNTRY_MAP.items():
        if key in p_lower:
            return country
    return ""


# Phrases that belong in vibe / occasion — never personality tags
_OCCASION_OR_VIBE_WORDS = (
    "wedding", "festive", "intimate", "destination", "local", "north indian",
    "south indian", "punjabi", "bengali", "traditional", "modern", "grand",
)

_JUNK_TAG_PATTERNS = (
    r"^\s*i\s+",
    r"^\s*we\s+",
    r"\bthink\b",
    r"\bwant\b",
    r"\bhoping\b",
    r"\bprefer\b",
    r"\bnot sure\b",
    r"\bpreference\b",
)

_VIBE_ALIASES: list[tuple[tuple[str, ...], str]] = [
    (("big and festive", "big festive", "festive", "grand festive"), "Big and festive"),
    (("intimate", "small and intimate", "cozy intimate"), "Intimate"),
    (("family-led", "family led", "family first", "family-first"), "Family-led"),
    (("modern & sleek", "modern and sleek", "modern sleek", "sleek"), "Modern & sleek"),
    (("traditional & rooted", "traditional", "rooted"), "Traditional & rooted"),
    (("whimsical", "playful"), "Whimsical & playful"),
    (("royal", "grand royal"), "Royal & grand"),
    (("warm & personal", "warm and personal"), "Warm & personal"),
    (("minimalist", "minimal"), "Minimalist"),
    (("maximalist",), "Maximalist"),
    (("relaxed", "easy", "chill"), "Relaxed & easy"),
    (("dramatic", "theatrical"), "Dramatic & theatrical"),
    (("bohemian", "boho"), "Bohemian"),
]


# ─────────────────────────────────────────────────────────────────────────────
# S2 Specificity Classification
# ─────────────────────────────────────────────────────────────────────────────

S2_FLEXIBLE_PHRASES = (
    "nothing finalized", "not sure", "keep it flexible", "keep it broad",
    "decide later", "help me finalize later", "flexible", "no idea",
    "you tell me", "let's keep it flexible", "skip", "no preference",
    "not decided", "to be decided", "undecided", "later", "anywhere",
)

S2_REFINED_REGIONS = (
    "east asia", "south asia", "southeast asia", "middle east", "europe", "caribbean",
    "rajasthan", "south india", "north india", "goa or bali", "thailand or bali",
    "italy or greece", "bali or phuket", "kerala", "himachal", "uttarakhand",
)


def classify_s2_info_level(memory_or_patch: dict, user_message: str = "") -> str:
    """
    Classify S2 input into one of 5 specificity levels:
    - "L0": Junk / unusable
    - "IL1_FLEXIBLE": Flexible accept phrases (e.g. "not sure", "keep it flexible")
    - "IL1": Broad setting or season/year without specific region/month
    - "IL2": Refined region/country + month/year
    - "IL3": Exact city/venue + month/dates/year
    """
    msg_l = (user_message or "").strip().lower()
    if msg_l and any(phrase in msg_l for phrase in S2_FLEXIBLE_PHRASES):
        return "IL1_FLEXIBLE"

    occ = memory_or_patch.get("occasion") if isinstance(memory_or_patch.get("occasion"), dict) else memory_or_patch
    if not isinstance(occ, dict):
        occ = {}

    place = (occ.get("place") or "").strip()
    setting = (occ.get("settingPreference") or "").strip()
    location_pref = (occ.get("locationPreference") or "").strip()
    date_pref = (occ.get("datePreference") or "").strip()
    season_pref = (occ.get("seasonPreference") or "").strip()
    spec_level = (occ.get("specificityLevel") or "").strip().upper()

    combined_text = f"{place} {setting} {location_pref} {date_pref} {season_pref} {msg_l}".lower()
    if any(phrase in combined_text for phrase in S2_FLEXIBLE_PHRASES):
        return "IL1_FLEXIBLE"

    if not place and not setting and not location_pref and not date_pref and not season_pref:
        # Check if this turn is an identity/name update turn
        has_name_kw = any(kw in msg_l for kw in ("name is", "names are", "my name", "our names", "i am", "we are"))
        has_identity_patch = bool(
            isinstance(memory_or_patch.get("identity"), dict)
            and (memory_or_patch["identity"].get("groomName") or memory_or_patch["identity"].get("brideName"))
        )
        if has_name_kw or has_identity_patch:
            return "IL1_FLEXIBLE"
        if spec_level == "IL1_FLEXIBLE":
            return "IL1_FLEXIBLE"
        if msg_l and looks_like_gibberish(msg_l):
            return "L0"
        return "L0"

    p_lower = (place or location_pref or setting or msg_l).lower()
    has_exact_city = any(city in p_lower for city in KNOWN_CITIES)
    has_exact_date = bool(re.search(r"\b\d{1,2}(st|nd|rd|th)?\b|\b\d{1,2}\s*[-–—]\s*\d{1,2}\b", combined_text))
    has_month = any(m in combined_text for m in MONTHS)

    if has_exact_city and (has_exact_date or has_month):
        return "IL3"
    if has_exact_city:
        return "IL3"

    has_refined_region = any(reg in p_lower for reg in S2_REFINED_REGIONS) or bool(occ.get("country"))
    if has_refined_region and (has_month or has_exact_date):
        return "IL2"
    if has_refined_region:
        return "IL2"

    return "IL1"


def get_occasion_state(memory: dict, user_message: str = "") -> dict:
    """
    Resolved occasion place/timing from canonical occasion + legacy top-level fields.
    Evaluates specificityLevel for S2 stay/advance gate.
    """
    occ = sanitize_timing_fields(dict(memory.get("occasion") or {}))

    if not (occ.get("place") or "").strip():
        legacy = (memory.get("place") or "").strip()
        if legacy:
            occ["place"] = legacy
    if not (occ.get("datePreference") or "").strip():
        legacy_date = (memory.get("datePreference") or "").strip()
        if legacy_date:
            occ["datePreference"] = legacy_date
    if not (occ.get("seasonPreference") or "").strip():
        legacy_season = (memory.get("seasonPreference") or "").strip()
        if legacy_season:
            occ["seasonPreference"] = legacy_season

    place = (
        (occ.get("place") or "")
        or (occ.get("locationPreference") or "")
        or (occ.get("settingPreference") or "")
    ).strip()
    has_place = bool(place)
    has_time = is_concrete_timing(occ)

    spec_level = classify_s2_info_level(occ, user_message=user_message)

    if spec_level == "L0":
        is_complete = False
    elif spec_level == "IL1":
        is_complete = False  # Stay on S2 once for IL1 turn 1
    else:
        # IL1_FLEXIBLE, IL2, IL3 advance to S3!
        is_complete = True

    return {
        "occasion": occ,
        "place": place,
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
    """
    Detect random keystrokes / nonsense that must never enter personality/vibe.
    e.g. "Asdfasidfu asg", "akdjlkasdjlfasjdlaj"
    """
    if not text or not isinstance(text, str):
        return True
    t = text.strip()
    if len(t) < 2:
        return True
    low = t.lower()
    # Very high consonant clusters without vowels (keyboard mash)
    letters = re.sub(r"[^a-z]", "", low)
    if len(letters) >= 6:
        vowels = sum(1 for c in letters if c in "aeiou")
        if vowels / len(letters) < 0.18:
            return True
        # Long run of same finger-adjacent nonsense without spaces meaning
        if re.search(r"[bcdfghjklmnpqrstvwxyz]{6,}", letters):
            return True
    # Tokens that look like mash (no dictionary-like vowels pattern)
    tokens = re.findall(r"[a-zA-Z]+", t)
    if not tokens:
        return True
    mash = 0
    for tok in tokens:
        tl = tok.lower()
        if len(tl) <= 2:
            continue
        v = sum(1 for c in tl if c in "aeiou")
        if v == 0 and len(tl) >= 4:
            mash += 1
        elif len(tl) >= 8 and v / len(tl) < 0.25:
            mash += 1
        # asdf / qwer keyboard walks
        if any(walk in tl for walk in ("asdf", "qwer", "zxcv", "hjkl", "dfgh", "jkl;")):
            mash += 1
    if mash >= 1 and len(tokens) <= 4:
        return True
    return False


def is_junk_tag(label: str) -> bool:
    """Reject cities, months, vibe/occasion words, gibberish, and sentence junk."""
    if not label or not isinstance(label, str):
        return True
    text = label.strip()
    if len(text) < 2 or len(text) > 40:
        return True
    words = text.split()
    if len(words) > 5:
        return True
    if looks_like_gibberish(text):
        return True
    low = text.lower()
    if low in KNOWN_CITIES or any(c == low for c in KNOWN_CITIES):
        return True
    if any(m == low or m in low.split() for m in MONTHS):
        return True
    if re.search(r"\b(19|20)\d{2}\b", low):
        return True
    for pat in _JUNK_TAG_PATTERNS:
        if re.search(pat, low):
            return True
    if any(w == low or w in low for w in _OCCASION_OR_VIBE_WORDS):
        if low in (
            "north indian", "south indian", "festive", "intimate", "wedding",
            "traditional", "modern", "big festive", "big & festive",
        ):
            return True
    if low in ("september preference", "cold weather", "beach", "goa", "delhi", "mumbai"):
        return True
    return False


def filter_tags(tags: list) -> list[str]:
    """Dedupe, drop junk tags, and cap at max 3 tags."""
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
    """Map free text to a canonical vibe pool label when possible."""
    from app.domain.chip_pools import get_chip_pool
    from app.domain.enums import StageId
    from app.services.ui.ui_hints import chips_mentioned_in_message

    pool = get_chip_pool(StageId.S4_VIBE.value)
    mentioned = chips_mentioned_in_message(message, pool)
    if mentioned:
        return mentioned[0]

    msg_l = message.lower()
    # Longer alias phrases first
    for aliases, label in sorted(_VIBE_ALIASES, key=lambda x: -max(len(a) for a in x[0])):
        for alias in aliases:
            if alias in msg_l:
                return label
    return None


def is_valid_primary_vibe(value: str) -> bool:
    from app.domain.chip_pools import get_chip_pool
    from app.domain.enums import StageId

    if not value or not isinstance(value, str):
        return False
    text = value.strip()
    low = text.lower()
    if len(text) < 2 or len(text) > 40:
        return False
    if low in KNOWN_CITIES or any(m in low.split() for m in MONTHS):
        return False
    if re.search(r"\b(19|20)\d{2}\b", low):
        return False
    pool = {v.lower() for v in get_chip_pool(StageId.S4_VIBE.value)}
    if low in pool:
        return True
    mapped = extract_vibe_label(value)
    if mapped and mapped.lower() in pool:
        return True
    # Custom short vibe labels (chips are reference, not a closed set)
    if looks_like_gibberish(text):
        return False
    words = text.split()
    return 1 <= len(words) <= 5


def normalize_primary_vibe(value: str | None, message: str = "") -> str | None:
    """Return a pool vibe label, a valid custom vibe, or None."""
    if value and is_valid_primary_vibe(value):
        from app.domain.chip_pools import get_chip_pool
        from app.domain.enums import StageId
        pool_map = {v.lower(): v for v in get_chip_pool(StageId.S4_VIBE.value)}
        return pool_map.get(value.strip().lower()) or value.strip()
    for source in (message, value or ""):
        mapped = extract_vibe_label(source)
        if mapped:
            return mapped
        if source and is_valid_primary_vibe(source):
            return source.strip()
    return None
