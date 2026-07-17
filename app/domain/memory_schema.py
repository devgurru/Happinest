"""
Canonical planner memory schema.
This is the structure that lives in session_memory_versions.memory_json.
Backend owns it. AI may propose patches. Frontend may render it.
"""
import copy


DEFAULT_PLANNER_MEMORY: dict = {
    "identity": {
        "clientName": "",
        "partnerName": "",
        "displayName": "",
        "occasionType": "wedding",
    },
    "occasion": {
        "place": "",
        "locationPreference": "",
        "settingPreference": "",
        "datePreference": "",
        "seasonPreference": "",
        "destinationMode": "unknown",
        "isConfirmed": False,
    },
    "personality": {
        "tags": [],
        "culturalSignals": [],
        "relationshipSignals": [],
        "lifestyleSignals": [],
        "plannerInterpretation": "",
    },
    "vibe": {
        "primaryVibe": "",
        "secondaryVibes": [],
        "energyLevel": "",
        "formality": "",
        "familyRole": "",
        "plannerInterpretation": "",
    },
    "brief": {
        "status": "not_started",
        "text": "",
        "version": 0,
        "generatedFromMemoryVersion": 0,
    },
    "direction": {
        "status": "not_started",
        "selectedDirectionId": "",
        "options": [],
        "version": 0,
        "generatedFromMemoryVersion": 0,
    },
    "logistics": {
        "events": [],
        "guestCounts": {},
        "budget": {},
        "vendorPreferences": {},
    },
    "summary": {
        "status": "not_started",
        "text": "",
        "version": 0,
        "generatedFromMemoryVersion": 0,
    },
    "openQuestions": [],
    "staleSections": [],
    "earlySignals": {
        "personality": [],
        "vibe": [],
        "acknowledged": False,
    },
    "committedSelections": {
        "personality": [],
        "vibe": [],
        "events": [],
        "directionId": "",
        "directionName": "",
    },
    "confidence": {
        "identity": 0,
        "occasion": 0,
        "personality": 0,
        "vibe": 0,
        "logistics": 0,
    },
}


def fresh_memory() -> dict:
    """Return a deep copy of the default planner memory."""
    return copy.deepcopy(DEFAULT_PLANNER_MEMORY)


def resolve_primary_vibe(memory: dict) -> str:
    """Primary vibe from canonical memory or committed chips — pool labels only."""
    from app.domain.chip_pools import get_chip_pool
    from app.domain.enums import StageId
    from app.domain.text_extract import is_valid_primary_vibe, normalize_primary_vibe

    vibe = memory.get("vibe") or {}
    primary = (vibe.get("primaryVibe") or "").strip()
    if primary and is_valid_primary_vibe(primary):
        return normalize_primary_vibe(primary) or primary

    committed = (memory.get("committedSelections") or {}).get("vibe") or []
    for chip in committed:
        if isinstance(chip, str) and is_valid_primary_vibe(chip):
            return normalize_primary_vibe(chip) or chip.strip()

    pool_map = {v.lower(): v for v in get_chip_pool(StageId.S4_VIBE.value)}
    for tag in (memory.get("personality") or {}).get("tags") or []:
        if isinstance(tag, str) and tag.lower() in pool_map:
            return pool_map[tag.lower()]

    return ""


def build_selected_chips(memory: dict) -> dict:
    """
    Committed chip selections derived from canonical memory.
    Used for UI restore after page reload.
    """
    from app.domain.text_extract import filter_tags, is_valid_primary_vibe, normalize_primary_vibe

    personality = memory.get("personality", {})
    vibe = memory.get("vibe", {})
    logistics = memory.get("logistics", {})
    direction = memory.get("direction", {})
    committed = memory.get("committedSelections", {})

    vibe_chips = []
    primary = resolve_primary_vibe(memory)
    if primary:
        vibe_chips.append(primary)
    for s in vibe.get("secondaryVibes") or []:
        if isinstance(s, str) and is_valid_primary_vibe(s):
            mapped = normalize_primary_vibe(s) or s
            if mapped.lower() not in {v.lower() for v in vibe_chips}:
                vibe_chips.append(mapped)
    if not vibe_chips:
        for chip in committed.get("vibe") or []:
            if isinstance(chip, str) and is_valid_primary_vibe(chip):
                mapped = normalize_primary_vibe(chip) or chip
                vibe_chips.append(mapped)

    selected_id = direction.get("selectedDirectionId") or committed.get("directionId", "")
    direction_name = committed.get("directionName", "")
    if not direction_name and selected_id:
        for opt in direction.get("options", []):
            if opt.get("id") == selected_id:
                direction_name = opt.get("name", "")
                break

    personality_tags = filter_tags(
        personality.get("tags") or committed.get("personality", [])
    )

    return {
        "personality": personality_tags,
        "vibe": vibe_chips,
        "events": logistics.get("events") or committed.get("events", []),
        "directionId": selected_id,
        "directionName": direction_name,
    }


def update_committed_selections(memory: dict, patch: dict) -> dict:
    """Merge patch into committedSelections for UI restore."""
    from app.domain.text_extract import filter_tags, is_valid_primary_vibe, normalize_primary_vibe

    selections = copy.deepcopy(memory.get("committedSelections", {}))
    if "personality" in patch:
        tags = filter_tags(patch["personality"].get("tags") or [])
        if tags:
            selections["personality"] = tags
    if "vibe" in patch:
        vibe = patch["vibe"]
        chips = []
        primary = vibe.get("primaryVibe")
        if primary and is_valid_primary_vibe(primary):
            chips.append(normalize_primary_vibe(primary) or primary)
        for s in vibe.get("secondaryVibes") or []:
            if isinstance(s, str) and is_valid_primary_vibe(s):
                mapped = normalize_primary_vibe(s) or s
                if mapped.lower() not in {c.lower() for c in chips}:
                    chips.append(mapped)
        if chips:
            selections["vibe"] = chips
    if "logistics" in patch and patch["logistics"].get("events"):
        selections["events"] = patch["logistics"]["events"]
    if "direction" in patch:
        d = patch["direction"]
        if d.get("selectedDirectionId"):
            selections["directionId"] = d["selectedDirectionId"]
        if d.get("selectedDirectionName"):
            selections["directionName"] = d["selectedDirectionName"]
    return selections


def build_planner_notes_view(memory: dict) -> dict:
    """
    Build the left-rail planner notes projection from canonical memory.
    This is a display projection, NOT the source of truth.
    """
    identity = memory.get("identity", {})
    occasion = memory.get("occasion", {})
    vibe = memory.get("vibe", {})
    direction = memory.get("direction", {})
    logistics = memory.get("logistics", {})

    client = identity.get("clientName", "")
    partner = identity.get("partnerName", "")
    couple = f"{client} & {partner}" if client and partner else client or partner or ""

    place = occasion.get("place", "")
    date = occasion.get("datePreference", "")
    occ = ", ".join(filter(None, [place, date]))

    primary_vibe = resolve_primary_vibe(memory)
    vibe_interp = vibe.get("plannerInterpretation", "")
    feeling = primary_vibe or vibe_interp or ""

    direction_name = ""
    selected_id = direction.get("selectedDirectionId", "")
    if selected_id:
        direction_name = direction.get("selectedDirectionName") or ""
        if not direction_name:
            for opt in direction.get("options", []):
                if opt.get("id") == selected_id:
                    direction_name = opt.get("name", "")
                    break
    if not direction_name:
        direction_name = memory.get("committedSelections", {}).get("directionName", "")

    events = logistics.get("events", [])
    guest_counts = logistics.get("guestCounts", {})
    budget = logistics.get("budget", {})

    plan_parts = []
    if events:
        plan_parts.append(f"{len(events)} events")
    total_guests = sum(guest_counts.values()) if isinstance(guest_counts, dict) else 0
    if total_guests:
        plan_parts.append(f"~{total_guests:,} guests")
    budget_range = budget.get("range", "")
    if budget_range:
        plan_parts.append(budget_range)

    return {
        "couple": couple,
        "occasion": occ,
        "feeling": feeling,
        "direction": direction_name,
        "plan": " · ".join(plan_parts),
    }
