"""
Timing Utilities — date/timing validation and sanitization functions.

Handles date resolution, past-date detection, timing concreteness checks,
and basic occasion field sanitization.
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
    """Return True when datePreference refers to a date in the past."""
    if not date_preference or not isinstance(date_preference, str):
        return False
    text = date_preference.strip().lower()
    today = _date.today()

    year_only = re.fullmatch(r"(19|20)\d{2}", text)
    if year_only:
        return int(text) < today.year

    year_match = re.search(r"\b(20\d{2})\b", text)
    year = int(year_match.group(1)) if year_match else None

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
        return month_num < today.month

    return False


def is_far_future_date(date_preference: str) -> bool:
    """Return True when date contains a year > 10 years in the future (e.g. 3030, 2099)."""
    if not date_preference or not isinstance(date_preference, str):
        return False
    text = date_preference.strip()
    year_match = re.search(r"\b(\d{4})\b", text)
    if year_match:
        year = int(year_match.group(1))
        today = _date.today()
        if year > today.year + 10:
            return True
    return False


def is_concrete_timing(occasion: dict) -> bool:
    """True only when date/season is concrete, future, and not vague or far-future."""
    date = (occasion.get("datePreference") or "").strip().lower()
    season = (occasion.get("seasonPreference") or "").strip().lower()

    if date:
        if any(vague in date for vague in VAGUE_TIMING):
            return False
        if is_past_date(date) or is_far_future_date(date):
            return False
        if any(m in date for m in MONTHS) or re.search(r"\b20\d{2}\b", date):
            return True

    if season:
        if any(vague in season for vague in VAGUE_TIMING):
            return False
        has_year = bool(re.search(r"\b20\d{2}\b", season))
        has_month = any(m in season for m in MONTHS)
        if (has_year or has_month) and not is_past_date(season) and not is_far_future_date(season):
            return True

    return False


def resolve_relative_date(date_preference: str) -> str:
    """Resolve relative timing expressions & preserve exact day/month/year dates."""
    if not date_preference or not isinstance(date_preference, str):
        return ""
    text = date_preference.strip()
    low = text.lower()
    today = _date.today()
    current_year = today.year
    next_year = current_year + 1

    found_month = None
    month_idx = None
    for month_name, idx in _MONTH_INDEX.items():
        if month_name in low:
            found_month = month_name.title()
            month_idx = idx
            break

    text_no_years = re.sub(r"\b\d+\s*years?\b|\b20\d{2}\b", "", text, flags=re.I)
    day_val = None
    day_match = re.search(r"\b([1-9]|[12]\d|3[01])(?:st|nd|rd|th)?\b", text_no_years, re.I)
    if day_match:
        day_val = int(day_match.group(1))

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
    """Strip vague, past, or far-future (>10 years) timing values."""
    occ = dict(occasion)
    raw_date = (occ.get("datePreference") or "").strip()

    if raw_date:
        occ["datePreference"] = resolve_relative_date(raw_date)

    date = (occ.get("datePreference") or "").strip().lower()
    season = (occ.get("seasonPreference") or "").strip().lower()

    if date and any(v in date for v in VAGUE_TIMING):
        occ["datePreference"] = ""
    if occ.get("datePreference") and (is_past_date(occ["datePreference"]) or is_far_future_date(occ["datePreference"])):
        occ["datePreference"] = ""
    if season and any(v in season for v in VAGUE_TIMING) and not any(s in season for s in VALID_SEASONS):
        occ["seasonPreference"] = ""

    return occ
