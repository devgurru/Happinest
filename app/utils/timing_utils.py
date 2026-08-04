"""
Timing Utilities — date/timing validation and sanitization functions.

Extracted from app/utils/validators.py. These handle all date resolution,
past-date detection, timing concreteness checks, and occasion field sanitization.
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
