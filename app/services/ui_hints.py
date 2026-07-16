"""
UI Hints — backend-owned chip/suggestion assembly for the frontend.

Per architecture docs, the backend validates AI signals and assembles UI hints.
Chip pools are curated vocabulary; the backend guarantees suggestions for chip stages
even when the model returns an empty or invalid suggestions array.
"""
from __future__ import annotations

import re

from app.domain.chip_pools import CHIP_POOLS, get_chip_pool
from app.domain.enums import StageId

CHIP_STAGES = {
    StageId.S3_PERSONALITY.value,
    StageId.S4_VIBE.value,
    StageId.S7_EVENTS.value,
    StageId.S8_GUESTS.value,
    StageId.S10_VENDORS.value,
}

# Vendor category chips grouped by event (aligned with product screens)
EVENT_VENDOR_CHIPS: dict[str, list[str]] = {
    "mehndi": ["Mehendi artist", "Catering", "Décor"],
    "haldi": ["Haldi setup", "Catering", "Décor"],
    "sangeet": ["Stage and sound", "Sangeet performers", "Catering", "DJ"],
    "wedding ceremony": ["Pandit", "Baraat coordinator", "Photography", "Florals", "Catering"],
    "reception": ["Photography", "Catering", "DJ and entertainment"],
    "engagement": ["Photography", "Décor", "Catering"],
    "cocktail night": ["Bar and beverages", "DJ", "Décor", "Catering"],
    "ring ceremony": ["Décor", "Photography", "Catering"],
    "after party": ["DJ and entertainment", "Bar and beverages", "Lighting"],
}


def _normalize_label(label: str) -> str:
    return re.sub(r"\s+", " ", label.strip())


def _labels_from_ai(raw: list | None) -> list[str]:
    labels: list[str] = []
    for item in raw or []:
        if isinstance(item, str) and item.strip():
            labels.append(_normalize_label(item))
        elif isinstance(item, dict):
            label = item.get("label", "")
            if isinstance(label, str) and label.strip():
                labels.append(_normalize_label(label))
    return labels


def build_vendor_chip_pool(events: list[str]) -> list[str]:
    """Build vendor chips from selected events, preserving event order."""
    pool: list[str] = []
    seen: set[str] = set()
    for event in events:
        key = event.strip().lower()
        for chip in EVENT_VENDOR_CHIPS.get(key, []):
            if chip not in seen:
                seen.add(chip)
                pool.append(chip)
    if not pool:
        pool = [
            "Photography",
            "Catering",
            "Décor",
            "DJ and entertainment",
            "Florals",
            "Mehendi artist",
        ]
    return pool


def _pool_for_stage(stage: str, memory: dict) -> list[str]:
    if stage == StageId.S10_VENDORS.value:
        events = memory.get("logistics", {}).get("events", [])
        return build_vendor_chip_pool(events)
    return get_chip_pool(stage)


def _contextual_chip_order(stage: str, memory: dict, pool: list[str]) -> list[str]:
    """Rank chips using memory context so UI feels personalized."""
    occasion = memory.get("occasion", {})
    personality = memory.get("personality", {})
    vibe = memory.get("vibe", {})
    early = memory.get("earlySignals") or {}

    place = (occasion.get("place") or occasion.get("locationPreference") or "").lower()
    tags = [t.lower() for t in personality.get("tags", [])]
    early_p = [t.lower() for t in (early.get("personality") or [])]
    early_v = [t.lower() for t in (early.get("vibe") or [])]
    primary_vibe = (vibe.get("primaryVibe") or "").lower()

    scored: list[tuple[int, str]] = []
    for chip in pool:
        score = 0
        chip_l = chip.lower()
        if place and chip_l in place:
            score += 3
        if any(tag in chip_l or chip_l in tag for tag in tags):
            score += 2
        if any(tag in chip_l or chip_l in tag for tag in early_p):
            score += 3
        if primary_vibe and (primary_vibe in chip_l or chip_l in primary_vibe):
            score += 2
        if any(tag in chip_l or chip_l in tag for tag in early_v):
            score += 3
        if stage == StageId.S3_PERSONALITY.value:
            if "delhi" in place and "delhi" in chip_l:
                score += 2
            if "beach" in place or "beach" in (occasion.get("settingPreference") or "").lower():
                if "beach" in chip_l:
                    score += 4
            if "food" in chip_l or "music" in chip_l:
                score += 1
        scored.append((score, chip))

    scored.sort(key=lambda x: (-x[0], pool.index(x[1])))
    return [chip for _, chip in scored]


def build_guest_count_suggestions(memory: dict) -> list[dict]:
    """Suggest guest-count prompts only for events missing counts."""
    events = memory.get("logistics", {}).get("events") or []
    counts = memory.get("logistics", {}).get("guestCounts") or {}
    suggestions: list[dict] = []
    for event in events:
        if not isinstance(counts.get(event), int) or counts.get(event, 0) <= 0:
            suggestions.append({
                "label": f"{event} — guest count?",
                "category": "guests",
            })
    return suggestions[:6]


def build_ui_suggestions(
    stage: str,
    memory: dict,
    ai_suggestions: list | None = None,
    *,
    for_stage: str | None = None,
    prefer_custom: bool = False,
) -> list[dict]:
    """
    Return normalized suggestion objects for the frontend chip UI.
    `for_stage` lets us attach chips for the stage we are advancing into.
    `prefer_custom` (more_suggestions): keep AI-invented labels; do not refill only from pool.
    """
    display_stage = for_stage or stage

    if display_stage == StageId.S8_GUESTS.value:
        guest_hints = build_guest_count_suggestions(memory)
        if guest_hints:
            return guest_hints

    if display_stage not in CHIP_STAGES:
        return [{"label": label, "category": None} for label in _labels_from_ai(ai_suggestions)]

    pool = _pool_for_stage(display_stage, memory)
    if not pool:
        return [{"label": label, "category": None} for label in _labels_from_ai(ai_suggestions)]

    pool_set = {c.lower(): c for c in pool}
    selected: list[str] = []
    allow_custom = prefer_custom or display_stage in (
        StageId.S3_PERSONALITY.value,
        StageId.S4_VIBE.value,
    )

    for label in _labels_from_ai(ai_suggestions):
        canonical = pool_set.get(label.lower())
        if canonical:
            if canonical not in selected:
                selected.append(canonical)
        elif allow_custom and 2 <= len(label) <= 40 and label not in selected:
            # Chip pool is reference — keep agent-invented short labels
            if label.lower() not in {s.lower() for s in selected}:
                selected.append(label)
        if prefer_custom and len(selected) >= 6:
            break

    if not prefer_custom or len(selected) < 3:
        for chip in _contextual_chip_order(display_stage, memory, pool):
            if chip.lower() not in {s.lower() for s in selected}:
                selected.append(chip)
            if len(selected) >= 6:
                break

    category = {
        StageId.S3_PERSONALITY.value: "personality",
        StageId.S4_VIBE.value: "vibe",
        StageId.S7_EVENTS.value: "events",
        StageId.S8_GUESTS.value: "guests",
        StageId.S10_VENDORS.value: "vendors",
    }.get(display_stage, "chip")

    return [{"label": chip, "category": category} for chip in selected[:6]]


def chips_mentioned_in_message(message: str, pool: list[str]) -> list[str]:
    """Find chip-pool labels referenced in free text (case-insensitive)."""
    message_l = message.lower()
    found: list[str] = []
    for chip in pool:
        if chip.lower() in message_l:
            found.append(chip)
    return found
