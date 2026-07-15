"""
Lightweight shape cleanup before memory merge.
Meaning / gibberish / section choice are owned by the agent.
"""
from __future__ import annotations

from app.services.text_extract import sanitize_timing_fields


def sanitize_memory_patch(
    patch: dict,
    *,
    stage: str,
    message: str,
    memory: dict,
) -> dict:
    if not patch:
        return patch

    patch = dict(patch)

    # Hoist mis-filed top-level occasion keys
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

    # Nest logistics fields
    logistics = dict(patch.get("logistics") or {})
    for key in ("events", "guestCounts", "budget", "vendorPreferences", "eventsConfirmed"):
        if key in patch:
            logistics[key] = patch.pop(key)
    if logistics:
        patch["logistics"] = logistics

    return patch
