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
    StageId.S2_BASICS.value,
    StageId.S3_PERSONALITY.value,
    StageId.S4_VIBE.value,
    StageId.S7_EVENTS.value,
}

# Vendor category chips grouped by event (aligned with product screens)
EVENT_VENDOR_CHIPS: dict[str, list[str]] = {
    "mehndi": ["Mehendi artist", "Catering", "Décor", "Photography"],
    "mehendi": ["Mehendi artist", "Catering", "Décor", "Photography"],
    "haldi": ["Haldi setup", "Catering", "Décor", "Florals"],
    "ubtan": ["Haldi setup", "Catering", "Décor", "Florals"],
    "sangeet": ["Stage and sound", "Sangeet performers", "Catering", "DJ and entertainment"],
    "nikkah": ["Imam / Qazi", "Florals", "Photography", "Catering"],
    "nikah": ["Imam / Qazi", "Florals", "Photography", "Catering"],
    "baraat": ["Baraat coordinator", "Band & Dhol", "Photography", "Catering"],
    "walima": ["Venue & Décor", "Catering", "Photography", "Stage Setup"],
    "wedding ceremony": ["Pandit", "Baraat coordinator", "Photography", "Florals", "Catering"],
    "wedding": ["Pandit", "Baraat coordinator", "Photography", "Florals", "Catering"],
    "reception": ["Photography", "Catering", "DJ and entertainment", "Décor"],
    "engagement": ["Photography", "Décor", "Catering", "Ring Stage Setup"],
    "ring ceremony": ["Décor", "Photography", "Catering"],
    "cocktail night": ["Bar and beverages", "DJ and entertainment", "Décor", "Catering"],
    "cocktail": ["Bar and beverages", "DJ and entertainment", "Décor", "Catering"],
    "after party": ["DJ and entertainment", "Bar and beverages", "Lighting"],
}


def get_vendors_for_event(event_name: str) -> list[str]:
    """Return 3 to 5 curated vendor suggestions for a specific event."""
    key = event_name.strip().lower()
    for pattern, vendors in EVENT_VENDOR_CHIPS.items():
        if pattern in key:
            return vendors[:5]
    return ["Photography", "Catering", "Décor", "Florals"]


def build_vendor_suggestions_by_event(events: list[str]) -> dict[str, list[str]]:
    """Build a mapping of event name -> 3 to 5 curated vendor suggestions."""
    res = {}
    for event in events:
        res[event] = get_vendors_for_event(event)
    return res


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
        for chip in get_vendors_for_event(event):
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


def _already_selected_chips_for_stage(display_stage: str, memory: dict) -> set[str]:
    selected_set = set()
    personality = memory.get("personality") or {}
    vibe = memory.get("vibe") or {}
    logistics = memory.get("logistics") or {}
    early = memory.get("earlySignals") or {}
    committed = memory.get("committedSelections") or {}

    if display_stage == StageId.S3_PERSONALITY.value:
        for t in (personality.get("tags") or []) + (early.get("personality") or []) + (committed.get("personality") or []):
            if isinstance(t, str) and t.strip():
                selected_set.add(t.strip().lower())
    elif display_stage == StageId.S4_VIBE.value:
        if primary := vibe.get("primaryVibe"):
            selected_set.add(primary.strip().lower())
        for s in (vibe.get("secondaryVibes") or []) + (early.get("vibe") or []) + (committed.get("vibe") or []):
            if isinstance(s, str) and s.strip():
                selected_set.add(s.strip().lower())
    elif display_stage == StageId.S7_EVENTS.value:
        for e in (logistics.get("events") or []) + (early.get("events") or []) + (committed.get("events") or []):
            if isinstance(e, str) and e.strip():
                selected_set.add(e.strip().lower())
    elif display_stage == StageId.S10_VENDORS.value:
        vendors = logistics.get("vendorPreferences") or {}
        for cat in vendors.keys():
            if isinstance(cat, str) and cat.strip():
                selected_set.add(cat.strip().lower())

    return selected_set


def build_guest_count_suggestions(memory: dict) -> list[str]:
    """Suggest guest-count prompts only for events missing counts."""
    events = memory.get("logistics", {}).get("events") or []
    counts = memory.get("logistics", {}).get("guestCounts") or {}
    suggestions: list[str] = []
    for event in events:
        if not isinstance(counts.get(event), int) or counts.get(event, 0) <= 0:
            suggestions.append(f"{event} — guest count?")
    return suggestions[:6]


COUNTRY_DESTINATIONS: dict[str, list[str]] = {
    "pakistan": ["Lahore", "Karachi", "Islamabad", "Bhurban", "Hunza", "Naran"],
    "india": ["Udaipur", "Goa", "Jaipur", "Jodhpur", "Kerala", "Mussoorie"],
    "thailand": ["Phuket", "Bangkok", "Koh Samui", "Chiang Mai", "Krabi", "Pattaya"],
    "uae": ["Dubai", "Abu Dhabi", "Ras Al Khaimah"],
    "dubai": ["Dubai", "Abu Dhabi", "Ras Al Khaimah"],
    "italy": ["Florence", "Lake Como", "Amalfi Coast", "Venice", "Tuscany", "Rome"],
    "france": ["Paris", "French Riviera", "Nice", "Provence"],
    "uk": ["London", "Cotswolds", "Edinburgh"],
    "united kingdom": ["London", "Cotswolds", "Edinburgh"],
    "usa": ["Hawaii", "Aspen", "Napa Valley", "Miami", "New York"],
    "indonesia": ["Bali", "Ubud", "Seminyak"],
    "maldives": ["Male", "Maafushi", "Baa Atoll"],
    "spain": ["Barcelona", "Ibiza", "Mallorca", "Seville"],
    "turkey": ["Istanbul", "Cappadocia", "Antalya", "Bodrum"],
}


def build_s2_location_suggestions(memory: dict) -> list[str]:
    """
    Generate context-aware city/region chip suggestions for S2 when location/timing is broad (IL1).
    Matches user's country, setting (beach, palace, mountains, nature, urban, etc.) and destination mode.
    Returns ONLY destination / city / place suggestions matching input values.
    Returns empty list on gibberish / L0 turns.
    """
    occ = memory.get("occasion") or {}
    spec_level = (occ.get("specificityLevel") or "").strip().upper()
    if spec_level == "L0":
        return []

    country = (occ.get("country") or "").strip().lower()
    place_text = (
        f"{occ.get('place') or ''} {occ.get('locationPreference') or ''} {occ.get('settingPreference') or ''} {occ.get('destinationMode') or ''} {country}"
    ).lower().strip()

    if not place_text:
        return []

    # 1. Country-based destination suggestions
    for c_key, c_destinations in COUNTRY_DESTINATIONS.items():
        if c_key in place_text:
            return c_destinations[:6]

    # 2. Setting/Location-based destination suggestions
    if any(k in place_text for k in ("beach", "coastal", "ocean", "sea", "island", "tropical")):
        return ["Goa", "Phuket", "Bali", "Maldives", "Koh Samui", "Boracay"]
    elif any(k in place_text for k in ("palace", "royal", "fort", "heritage", "castle")):
        return ["Udaipur", "Jaipur", "Jodhpur", "Florence", "Agra", "Muscat"]
    elif any(k in place_text for k in ("mountain", "hill", "nature", "outdoor", "valley", "alpine")):
        return ["Shimla", "Manali", "Lake Como", "Swiss Alps", "Mussoorie", "Aspen"]
    elif any(k in place_text for k in ("urban", "modern", "city", "skyline")):
        return ["Dubai", "Singapore", "Delhi", "Mumbai", "London", "New York"]
    elif any(k in place_text for k in ("destination", "resort")):
        return ["Goa", "Udaipur", "Phuket", "Bali", "Jaipur", "Maldives"]

    return ["Goa", "Udaipur", "Jaipur", "Phuket", "Bali", "Thailand"]


def build_ui_suggestions(
    stage: str,
    memory: dict,
    ai_suggestions: list | None = None,
    *,
    for_stage: str | None = None,
    prefer_custom: bool = False,
) -> list[str]:
    """
    Return clean string suggestions (labels) for the frontend chip UI.
    `for_stage` lets us attach chips for the stage we are advancing into.
    Excludes any chip labels already selected in memory.
    """
    display_stage = for_stage or stage

    if display_stage == StageId.S2_BASICS.value:
        return build_s2_location_suggestions(memory)

    already_selected = _already_selected_chips_for_stage(display_stage, memory)

    if display_stage not in CHIP_STAGES:
        return [lbl for lbl in _labels_from_ai(ai_suggestions) if lbl.lower() not in already_selected]

    pool = _pool_for_stage(display_stage, memory)
    if not pool:
        return [lbl for lbl in _labels_from_ai(ai_suggestions) if lbl.lower() not in already_selected]

    pool_set = {c.lower(): c for c in pool}
    selected: list[str] = []
    allow_custom = prefer_custom or display_stage in (
        StageId.S3_PERSONALITY.value,
        StageId.S4_VIBE.value,
    )

    for label in _labels_from_ai(ai_suggestions):
        lbl_low = label.lower()
        if lbl_low in already_selected:
            continue
        canonical = pool_set.get(lbl_low)
        if canonical:
            if canonical.lower() not in {s.lower() for s in selected}:
                selected.append(canonical)
        elif allow_custom and 2 <= len(label) <= 40:
            if lbl_low not in {s.lower() for s in selected}:
                selected.append(label)
        if prefer_custom and len(selected) >= 6:
            break

    if not prefer_custom or len(selected) < 3:
        for chip in _contextual_chip_order(display_stage, memory, pool):
            chip_low = chip.lower()
            if chip_low in already_selected:
                continue
            if chip_low not in {s.lower() for s in selected}:
                selected.append(chip)
            if len(selected) >= 6:
                break

    return selected[:6]



def chips_mentioned_in_message(message: str, pool: list[str]) -> list[str]:
    """Find chip-pool labels referenced in free text (case-insensitive)."""
    message_l = message.lower()
    found: list[str] = []
    for chip in pool:
        if chip.lower() in message_l:
            found.append(chip)
    return found
