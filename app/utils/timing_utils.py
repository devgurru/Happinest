"""
Timing Utilities — date/timing validation and sanitization functions.
"""
from __future__ import annotations

import re
from datetime import date as _date

MONTHS = (
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
)
VALID_SEASONS = ("winter", "summer", "monsoon", "spring", "autumn", "fall")
VAGUE_TIMING = ("cold weather", "hot weather", "pleasant weather", "sometime", "not sure", "flexible", "anytime")


def _extract_year(text: str) -> int | None:
    match = re.search(r"\b(19\d{2}|20\d{2}|\d{4})\b", text)
    return int(match.group(1)) if match else None


def is_past_date(date_str: str) -> bool:
    """Return True if year is in the past (< current year)."""
    year = _extract_year(date_str or "")
    return year is not None and year < _date.today().year


def is_far_future_date(date_str: str) -> bool:
    """Return True if year is more than 15 years in the future (> current year + 15)."""
    year = _extract_year(date_str or "")
    return year is not None and year > (_date.today().year + 15)


def resolve_relative_date(date_preference: str) -> str:
    """Return clean date string."""
    return (date_preference or "").strip()


def is_concrete_timing(occasion: dict) -> bool:
    """True only when date/season is concrete, future, and within 15 years."""
    date = (occasion.get("datePreference") or "").strip().lower()
    season = (occasion.get("seasonPreference") or "").strip().lower()

    for val in (date, season):
        if val and not any(v in val for v in VAGUE_TIMING):
            if not is_past_date(val) and not is_far_future_date(val):
                if any(m in val for m in MONTHS) or any(s in val for s in VALID_SEASONS) or _extract_year(val):
                    return True
    return False


def sanitize_timing_fields(occasion: dict) -> dict:
    """Strip vague, past, or far-future (>15 years) timing values."""
    occ = dict(occasion)
    date = (occ.get("datePreference") or "").strip()
    season = (occ.get("seasonPreference") or "").strip()

    if date and (any(v in date.lower() for v in VAGUE_TIMING) or is_past_date(date) or is_far_future_date(date)):
        occ["datePreference"] = ""
    if season and any(v in season.lower() for v in VAGUE_TIMING) and not any(s in season.lower() for s in VALID_SEASONS):
        occ["seasonPreference"] = ""

    return occ
