"""
Sanitize AI / intent memory patches before apply.
Strips cross-section noise (event names in personality, etc.) and normalizes shape.
"""
from __future__ import annotations

import re

from app.domain.chip_pools import get_chip_pool
from app.domain.enums import StageId
from app.services.text_extract import filter_tags, is_junk_tag


def _event_names_lower() -> set[str]:
    pool = get_chip_pool(StageId.S7_EVENTS.value)
    names: set[str] = set()
    for e in pool:
        names.add(e.lower())
        names.add(e.lower().rstrip("s"))  # reception / receptions
    names.update(
        {
            "reception", "receptions", "sangeet", "mehndi", "haldi",
            "wedding ceremony", "engagement", "cocktail", "after party",
            "pre wedding", "pre-wedding", "festive", "festives",
        }
    )
    return names


_EVENT_NAMES = _event_names_lower()

_VIBE_POOL = {v.lower() for v in get_chip_pool(StageId.S4_VIBE.value)}


def _is_event_focused_message(message: str) -> bool:
    msg_l = message.lower()
    if any(e in msg_l for e in _EVENT_NAMES):
        return True
    return any(
        w in msg_l
        for w in (
            "event", "events", "function", "functions", "ceremony", "ceremonies",
            "pre wedding", "pre-wedding", "festive",
        )
    )


def _normalize_logistics_shape(patch: dict) -> None:
    """Move top-level events/guestCounts/budget into logistics."""
    logistics = dict(patch.get("logistics") or {})
    if "events" in patch and isinstance(patch["events"], list):
        logistics["events"] = patch.pop("events")
    if "guestCounts" in patch and isinstance(patch["guestCounts"], dict):
        logistics["guestCounts"] = patch.pop("guestCounts")
    if "budget" in patch and isinstance(patch["budget"], dict):
        logistics["budget"] = patch.pop("budget")
    if logistics:
        patch["logistics"] = logistics


def _strip_event_names_from_tags(tags: list) -> list[str]:
    out: list[str] = []
    for tag in tags or []:
        if not isinstance(tag, str):
            continue
        low = tag.lower().strip()
        if low in _EVENT_NAMES or any(e in low for e in ("reception", "sangeet", "mehndi", "haldi")):
            continue
        if not is_junk_tag(tag):
            out.append(tag.strip())
    return filter_tags(out)


def sanitize_memory_patch(
    patch: dict,
    *,
    stage: str,
    message: str,
    memory: dict,
) -> dict:
    """Return a cleaned patch safe to merge into canonical memory."""
    if not patch:
        return patch

    patch = dict(patch)
    _normalize_logistics_shape(patch)

    msg_l = message.lower()
    event_focus = _is_event_focused_message(message)

    # S6 direction pick — never treat option names as occasion place updates
    if stage == StageId.S6_DIRECTIONS.value:
        from app.services.turn_interpreter import direction_selection_patch
        sel = direction_selection_patch(message, memory)
        if sel:
            patch.update(sel)
            patch.pop("occasion", None)
            patch.pop("personality", None)
            patch.pop("vibe", None)

    logistics_stages = {
        StageId.S7_EVENTS.value,
        StageId.S8_GUESTS.value,
        StageId.S9_BUDGET.value,
        StageId.S10_VENDORS.value,
    }

    # ── Personality: never accept event names as tags ───────────────────────
    if "personality" in patch:
        pers = dict(patch["personality"])
        if pers.get("tags"):
            pers["tags"] = _strip_event_names_from_tags(pers["tags"])
        if not pers.get("tags") and not any(
            pers.get(k) for k in ("culturalSignals", "relationshipSignals", "lifestyleSignals")
        ):
            patch.pop("personality", None)
        else:
            patch["personality"] = pers

    # ── Vibe: reject event names masquerading as primaryVibe ───────────────
    if "vibe" in patch:
        vibe = dict(patch["vibe"])
        primary = (vibe.get("primaryVibe") or "").strip()
        if primary and primary.lower() not in _VIBE_POOL:
            # Custom secondary cue (light music) — not a primary vibe swap
            if primary.lower() in _EVENT_NAMES or "reception" in primary.lower():
                vibe.pop("primaryVibe", None)
            elif stage in logistics_stages and event_focus and "vibe" not in msg_l:
                vibe.pop("primaryVibe", None)
        sec = vibe.get("secondaryVibes") or []
        vibe["secondaryVibes"] = [
            s for s in sec
            if isinstance(s, str) and s.lower() not in _EVENT_NAMES
        ]
        if not vibe.get("primaryVibe") and not vibe.get("secondaryVibes"):
            patch.pop("vibe", None)
        else:
            patch["vibe"] = vibe

    # ── Drop personality/vibe on logistics turns unless explicitly targeted ─
    if stage in logistics_stages and event_focus:
        if "personality" in patch and "personality" not in msg_l:
            patch.pop("personality", None)
        if "vibe" in patch and "vibe" not in msg_l:
            patch.pop("vibe", None)

    # ── Events on S8+ corrections: merge into logistics only ────────────────
    if stage in (StageId.S8_GUESTS.value, StageId.S9_BUDGET.value, StageId.S10_VENDORS.value):
        if event_focus and "logistics" not in patch:
            from app.services.ui_hints import chips_mentioned_in_message

            pool = get_chip_pool(StageId.S7_EVENTS.value)
            found = chips_mentioned_in_message(message, pool)
            if not found and "reception" in msg_l:
                found = ["Reception"]
            if found:
                existing = memory.get("logistics", {}).get("events") or []
                merged = list(existing)
                seen = {e.lower() for e in merged}
                for e in found:
                    if e.lower() not in seen:
                        merged.append(e)
                        seen.add(e.lower())
                patch["logistics"] = {**(patch.get("logistics") or {}), "events": merged}

    return patch
