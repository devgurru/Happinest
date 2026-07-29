"""
Validators — pure validation/sanitization functions for memory data.

Extracted from text_extract.py. These are deterministic functions that
validate and clean memory values. They do NOT extract data from user
messages — the AI intent agent handles all message understanding.
"""
from __future__ import annotations

import re
from datetime import date as _date

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

KNOWN_CITIES = (
    "delhi", "mumbai", "udaipur", "jaipur", "goa", "bangalore", "bengaluru",
    "chennai", "hyderabad", "kolkata", "agra", "jodhpur", "pune", "gurgaon",
    "gurugram", "noida", "chandigarh", "lucknow", "ahmedabad", "kochi",
    "trivandrum", "indore", "bhopal", "shimla", "manali", "rishikesh",
    "london", "paris", "dubai", "lahore", "karachi", "islamabad",
    "phuket", "bali", "maldives", "koh samui", "boracay", "florence",
    "lake como", "swiss alps", "singapore", "muscat", "mussoorie", "aspen",
    "thailand", "hawaii", "santorini", "cancun",
)

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


_MONTH_INDEX = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}


def is_past_date(date_preference: str) -> bool:
    """
    Return True when datePreference refers to a date already in the past.
    Checks explicit year first; falls back to month-only (assumed current year).
    Examples that return True (assuming today >= July 2026):
      "March 2025", "January 2026", "2024", "March" (when current month > March)
    Examples that return False:
      "December 2026", "December", "Winter", "March 2027"
    """
    if not date_preference or not isinstance(date_preference, str):
        return False
    text = date_preference.strip().lower()
    today = _date.today()

    # Year-only entry (e.g. "2024", "2025")
    year_only = re.fullmatch(r"(19|20)\d{2}", text)
    if year_only:
        return int(text) < today.year

    # Extract optional year from the string
    year_match = re.search(r"\b(20\d{2})\b", text)
    year = int(year_match.group(1)) if year_match else None

    # Extract month
    month_num: int | None = None
    for month_name, idx in _MONTH_INDEX.items():
        if month_name in text:
            month_num = idx
            break

    if year and month_num:
        return _date(year, month_num, 1) < _date(today.year, today.month, 1)
    if year:
        return year < today.year
    if month_num:
        # Month only — assume current year; past if month already gone
        return month_num < today.month

    return False


def is_concrete_timing(occasion: dict) -> bool:
    """True only when date/season is concrete, future, and not vague."""
    date = (occasion.get("datePreference") or "").strip().lower()
    season = (occasion.get("seasonPreference") or "").strip().lower()

    if date:
        if any(vague in date for vague in VAGUE_TIMING):
            return False
        # Reject past dates — a wedding cannot be in the past
        if is_past_date(date):
            return False
        if any(m in date for m in MONTHS) or re.search(r"\b20\d{2}\b", date):
            return True

    # Bare season name without year/month (e.g. "Winter", "Summer") is incomplete for S2
    if season:
        if any(vague in season for vague in VAGUE_TIMING):
            return False
        has_year = bool(re.search(r"\b20\d{2}\b", season))
        has_month = any(m in season for m in MONTHS)
        if (has_year or has_month) and not is_past_date(season):
            return True

    return False



def resolve_relative_date(date_preference: str) -> str:
    """
    Resolve relative timing expressions & preserve exact day/month/year dates.
    Examples:
      "3 years after" -> "July 2029" (when today is July 2026)
      "in 3 years" -> "July 2029"
      "12 june 2028" -> "12 June 2028"
      "june next year" -> "June 2027"
    """
    if not date_preference or not isinstance(date_preference, str):
        return ""
    text = date_preference.strip()
    low = text.lower()
    today = _date.today()
    current_year = today.year
    next_year = current_year + 1

    # Find month
    found_month = None
    month_idx = None
    for month_name, idx in _MONTH_INDEX.items():
        if month_name in low:
            found_month = month_name.title()
            month_idx = idx
            break

    # Remove relative year offsets and 4-digit years before matching day
    text_no_years = re.sub(r"\b\d+\s*years?\b|\b20\d{2}\b", "", text, flags=re.I)
    day_val = None
    day_match = re.search(r"\b([1-9]|[12]\d|3[01])(?:st|nd|rd|th)?\b", text_no_years, re.I)
    if day_match:
        day_val = int(day_match.group(1))

    # Check relative year offsets (e.g. "3 years after", "in 3 years", "3 years from now")
    rel_year_match = re.search(r"\b(\d+)\s*years?\b", low)
    if rel_year_match:
        offset = int(rel_year_match.group(1))
        target_year = current_year + offset
        if found_month and day_val:
            return f"{day_val} {found_month} {target_year}"
        if found_month:
            return f"{found_month} {target_year}"
        current_month_name = MONTHS[today.month - 1].title()
        return f"{current_month_name} {target_year}"

    is_next_year_mentioned = any(kw in low for kw in ("next year", "coming year", "following year"))

    # Explicit 4-digit year already present (e.g. "2028")
    year_match = re.search(r"\b(20\d{2})\b", text)
    if year_match:
        year_val = year_match.group(1)
        if found_month and day_val:
            return f"{day_val} {found_month} {year_val}"
        if found_month:
            return f"{found_month} {year_val}"
        return year_val

    if is_next_year_mentioned:
        if found_month and day_val:
            return f"{day_val} {found_month} {next_year}"
        if found_month:
            return f"{found_month} {next_year}"
        return str(next_year)

    if found_month and month_idx:
        target_year = next_year if month_idx <= today.month else current_year
        if day_val:
            return f"{day_val} {found_month} {target_year}"
        return f"{found_month} {target_year}"

    return text



def sanitize_timing_fields(occasion: dict) -> dict:
    """Strip vague or past timing values that should not unlock S2 advance."""
    occ = dict(occasion)
    raw_date = (occ.get("datePreference") or "").strip()

    # Resolve relative dates & preserve exact date strings
    if raw_date:
        occ["datePreference"] = resolve_relative_date(raw_date)

    date = (occ.get("datePreference") or "").strip().lower()
    season = (occ.get("seasonPreference") or "").strip().lower()

    if date and any(v in date for v in VAGUE_TIMING):
        occ["datePreference"] = ""

    # Strip past dates — never save a past wedding date to memory
    if occ.get("datePreference") and is_past_date(occ["datePreference"]):
        occ["datePreference"] = ""
    if season and not any(s in season for s in VALID_SEASONS):
        occ["seasonPreference"] = ""
    if season and any(v in season for v in VAGUE_TIMING) and not any(s in season for s in VALID_SEASONS):
        occ["seasonPreference"] = ""


    # Don't let vibe/culture words pollute place or setting
    place = (occ.get("place") or "").strip().lower()
    if place in KNOWN_CITIES:
        occ["place"] = place.title()
    elif place and any(w in place for w in ("festive", "intimate", "wedding", "north indian")):
        # keep known city if embedded
        for city in KNOWN_CITIES:
            if city in place:
                occ["place"] = city.title()
                break

    loc = (occ.get("locationPreference") or "").strip().lower()
    if loc in ("north indian", "south indian", "festive", "traditional", "big", "wedding"):
        occ["locationPreference"] = ""
    setting = (occ.get("settingPreference") or "").strip().lower()
    if setting in ("festive", "traditional", "north indian", "big", "wedding", "spring"):
        occ["settingPreference"] = ""

    return occ


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

    if spec_level == "IL1_FLEXIBLE":
        return "IL1_FLEXIBLE"

    combined_text = f"{place} {setting} {location_pref} {date_pref} {season_pref} {msg_l}".lower()
    if any(phrase in combined_text for phrase in S2_FLEXIBLE_PHRASES):
        return "IL1_FLEXIBLE"

    if not place and not setting and not location_pref and not date_pref and not season_pref:
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

    spec_level = occ.get("specificityLevel") or classify_s2_info_level(occ, user_message=user_message)

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


# ─────────────────────────────────────────────────────────────────────────────
# AI Response Sanitization & Validation (Consolidated)
# ─────────────────────────────────────────────────────────────────────────────

from app.domain.enums import StageDecisionType, StageId
from app.services.session.memory_service import VALID_STALE

VALID_STAGE_IDS = {s.value for s in StageId}
VALID_DECISION_TYPES = {d.value for d in StageDecisionType}
REQUIRED_FIELDS = {"plannerReply", "memoryPatch", "stageDecision", "staleSections", "openQuestions", "suggestions"}
REQUIRED_STAGE_DECISION_FIELDS = {"type", "stage"}

_STAGE_ALIASES: list[tuple[str, str]] = [
    ("personality", StageId.S3_PERSONALITY.value),
    ("names", StageId.S1_NAMES.value),
    ("s1", StageId.S1_NAMES.value),
    ("basics", StageId.S2_BASICS.value),
    ("s2", StageId.S2_BASICS.value),
    ("vibe", StageId.S4_VIBE.value),
    ("s4", StageId.S4_VIBE.value),
    ("brief", StageId.S5_BRIEF.value),
    ("s5", StageId.S5_BRIEF.value),
    ("direction", StageId.S6_DIRECTIONS.value),
    ("s6", StageId.S6_DIRECTIONS.value),
    ("events", StageId.S7_EVENTS.value),
    ("guest", StageId.S8_GUESTS.value),
    ("budget", StageId.S9_BUDGET.value),
    ("vendor", StageId.S10_VENDORS.value),
    ("summary", StageId.S11_SUMMARY.value),
]


def _normalize_stage_id(raw_stage: str, current_stage: str) -> str:
    if raw_stage in VALID_STAGE_IDS:
        return raw_stage
    raw_l = (raw_stage or "").lower()
    for needle, stage_id in _STAGE_ALIASES:
        if needle in raw_l:
            return stage_id
    return current_stage


def _sanitize_memory_patch_schema(patch: dict) -> dict:
    """Hoisting and nesting of memory patch fields."""
    if not patch:
        return patch

    patch = dict(patch)

    occasion = dict(patch.get("occasion") or {})
    for key in (
        "place", "locationPreference", "settingPreference",
        "datePreference", "seasonPreference", "destinationMode", "isConfirmed",
    ):
        if key in patch and key != "occasion":
            val = patch.pop(key)
            if val is not None and val != "":
                occasion[key] = val
    if occasion:
        patch["occasion"] = sanitize_timing_fields(occasion)

    logistics = dict(patch.get("logistics") or {})
    for key in ("events", "guestCounts", "budget", "vendorPreferences", "eventsConfirmed"):
        if key in patch:
            logistics[key] = patch.pop(key)
    if logistics:
        patch["logistics"] = logistics

    return patch


def sanitize_ai_response(raw: dict, current_stage: str) -> dict:
    """Sanitize AI structured output prior to schema validation."""
    raw = dict(raw)
    if "suggestions" not in raw or raw["suggestions"] is None:
        raw["suggestions"] = []
    if "staleSections" not in raw or raw["staleSections"] is None:
        raw["staleSections"] = []
    if "openQuestions" not in raw or raw["openQuestions"] is None:
        raw["openQuestions"] = []
    if not isinstance(raw.get("memoryPatch"), dict):
        raw["memoryPatch"] = {}

    sd = raw.get("stageDecision") if isinstance(raw.get("stageDecision"), dict) else {}
    decision_type = sd.get("type", StageDecisionType.STAY.value)
    if decision_type not in VALID_DECISION_TYPES:
        decision_type = StageDecisionType.STAY.value
    to_stage = _normalize_stage_id(sd.get("stage", current_stage), current_stage)
    raw["stageDecision"] = {"type": decision_type, "stage": to_stage}

    raw["memoryPatch"] = _sanitize_memory_patch_schema(raw.get("memoryPatch", {}))
    return raw


def validate_ai_response(raw: dict, stage: str) -> tuple[bool, str | None]:
    """Validate AI response dict against stage contract."""
    if not isinstance(raw, dict):
        return False, "RESPONSE_NOT_DICT"

    if "suggestions" not in raw:
        raw["suggestions"] = []

    missing = REQUIRED_FIELDS - raw.keys()
    if missing:
        return False, f"MISSING_FIELDS:{','.join(sorted(missing))}"

    planner_reply = raw.get("plannerReply", "")
    if not isinstance(planner_reply, str) or not planner_reply.strip():
        return False, "EMPTY_PLANNER_REPLY"

    if not isinstance(raw.get("memoryPatch"), dict):
        return False, "INVALID_MEMORY_PATCH"

    sd = raw.get("stageDecision", {})
    if not isinstance(sd, dict):
        return False, "INVALID_STAGE_DECISION"

    sd_missing = REQUIRED_STAGE_DECISION_FIELDS - sd.keys()
    if sd_missing:
        return False, f"MISSING_STAGE_DECISION_FIELDS:{','.join(sorted(sd_missing))}"

    if sd.get("type") not in VALID_DECISION_TYPES:
        return False, f"INVALID_DECISION_TYPE:{sd.get('type')}"

    if sd.get("stage") not in VALID_STAGE_IDS:
        return False, f"INVALID_STAGE_ID:{sd.get('stage')}"

    stale = raw.get("staleSections", [])
    if not isinstance(stale, list):
        return False, "INVALID_STALE_SECTIONS"
    invalid_stale = [s for s in stale if s not in VALID_STALE]
    if invalid_stale:
        return False, f"UNKNOWN_STALE_SECTIONS:{','.join(invalid_stale)}"

    if not isinstance(raw.get("openQuestions"), list):
        return False, "INVALID_OPEN_QUESTIONS"

    suggestions = raw.get("suggestions", [])
    if suggestions is None:
        suggestions = []
    if not isinstance(suggestions, list):
        return False, "INVALID_SUGGESTIONS"
    raw["suggestions"] = suggestions

    return True, None


def validate_synthesis_response(raw: dict, synthesis_type: str) -> tuple[bool, str | None]:
    """Validate AI response for synthesis requests."""
    is_valid, err = validate_ai_response(raw, f"synthesis_{synthesis_type}")
    if not is_valid:
        return False, err

    if synthesis_type == "brief":
        if not raw.get("briefText", "").strip():
            return False, "EMPTY_BRIEF_TEXT"

    elif synthesis_type == "direction":
        options = raw.get("directionOptions", [])
        if not isinstance(options, list) or len(options) == 0:
            return False, "EMPTY_DIRECTION_OPTIONS"
        for opt in options:
            if not isinstance(opt, dict):
                return False, "INVALID_DIRECTION_OPTION"
            for req in ("id", "name", "rankOrder", "reasonText"):
                if not opt.get(req):
                    return False, f"DIRECTION_OPTION_MISSING:{req}"

    elif synthesis_type == "summary":
        if not raw.get("summaryText", "").strip():
            return False, "EMPTY_SUMMARY_TEXT"

    return True, None
