"""
Sanitize AI / intent memory patches before apply.
Strips cross-section noise (event names in personality, etc.) and normalizes shape.
"""
from __future__ import annotations

from app.domain.chip_pools import get_chip_pool
from app.domain.enums import StageId
from app.services.text_extract import (
    VALID_SEASONS,
    extract_month_or_season,
    extract_place_from_message,
    filter_tags,
    is_junk_tag,
    is_valid_primary_vibe,
    looks_like_occasion_rehash,
    normalize_primary_vibe,
    sanitize_timing_fields,
)


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


def _normalize_occasion_shape(patch: dict, memory: dict) -> None:
    """Hoist mis-filed top-level occasion fields into occasion.{place,date,...}."""
    occasion = dict(patch.get("occasion") or memory.get("occasion") or {})
    moved = False
    for key in (
        "place", "locationPreference", "settingPreference",
        "datePreference", "seasonPreference", "destinationMode", "isConfirmed",
    ):
        if key in patch and key != "occasion":
            val = patch.pop(key)
            if val is not None and val != "":
                occasion[key] = val
                moved = True
    if moved or patch.get("occasion"):
        patch["occasion"] = sanitize_timing_fields(occasion)


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


def _scrub_occasion_inventions(occasion: dict, message: str, memory: dict) -> dict:
    """
    Hard rules for occasion fields:
    - Never invent season from month alone
    - Never store culture/vibe adjectives as location/setting
    - Keep known place/date when user rehashes
    """
    occ = sanitize_timing_fields(dict(occasion or {}))
    msg_l = (message or "").lower()
    spoken = extract_month_or_season(message)
    mem_occ = memory.get("occasion") or {}

    # Drop AI season unless user explicitly said a season word
    season = (occ.get("seasonPreference") or "").strip().lower()
    if season and not any(s in msg_l for s in VALID_SEASONS):
        prior = (mem_occ.get("seasonPreference") or "").strip()
        if prior.lower() == season:
            occ["seasonPreference"] = prior
        else:
            occ["seasonPreference"] = ""

    place_hit = extract_place_from_message(message)
    if place_hit:
        occ["place"] = place_hit
    elif not (occ.get("place") or "").strip():
        prior_place = (mem_occ.get("place") or "").strip()
        if prior_place:
            occ["place"] = prior_place

    if spoken.get("datePreference"):
        occ["datePreference"] = spoken["datePreference"]

    return sanitize_timing_fields(occ)


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
    _normalize_occasion_shape(patch, memory)
    _normalize_logistics_shape(patch)

    msg_l = message.lower()
    event_focus = _is_event_focused_message(message)
    occasion_rehash = looks_like_occasion_rehash(message, memory)

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

    # ── Occasion: scrub invented season/setting/culture dumps ───────────────
    if "occasion" in patch and isinstance(patch["occasion"], dict):
        patch["occasion"] = _scrub_occasion_inventions(patch["occasion"], message, memory)
        # Drop no-op occasion patches that only restate known place/date
        mem_occ = memory.get("occasion") or {}
        new_occ = patch["occasion"]
        meaningful = False
        for key in ("place", "datePreference", "seasonPreference", "settingPreference",
                    "locationPreference", "destinationMode"):
            new_v = (new_occ.get(key) or "")
            old_v = (mem_occ.get(key) or "")
            if isinstance(new_v, str) and isinstance(old_v, str):
                if new_v.strip() and new_v.strip().lower() != old_v.strip().lower():
                    meaningful = True
                    break
            elif new_v != old_v and new_v:
                meaningful = True
                break
        if not meaningful and occasion_rehash:
            patch.pop("occasion", None)

    # ── Personality: never accept event names / cities as tags ──────────────
    if "personality" in patch:
        pers = dict(patch["personality"])
        if pers.get("tags"):
            pers["tags"] = _strip_event_names_from_tags(pers["tags"])
        if pers.get("culturalSignals"):
            pers["culturalSignals"] = [
                s for s in pers["culturalSignals"]
                if isinstance(s, str)
                and s.strip()
                and s.strip().lower() not in (
                    "delhi", "mumbai", "goa", "festive", "wedding", "spring",
                    "traditional", "big", "north indian wedding",
                )
            ]
        # Occasion rehash on S3: reject personality entirely
        if occasion_rehash and stage == StageId.S3_PERSONALITY.value:
            patch.pop("personality", None)
        elif not pers.get("tags") and not any(
            pers.get(k) for k in ("culturalSignals", "relationshipSignals", "lifestyleSignals")
        ):
            patch.pop("personality", None)
        else:
            patch["personality"] = pers

    # ── Vibe: must be a pool label — never cities / months / festivities alone ─
    if "vibe" in patch:
        vibe = dict(patch["vibe"])
        primary = (vibe.get("primaryVibe") or "").strip()
        if primary:
            normalized = normalize_primary_vibe(primary, message)
            if normalized and is_valid_primary_vibe(normalized):
                vibe["primaryVibe"] = normalized
            else:
                vibe.pop("primaryVibe", None)
        sec = vibe.get("secondaryVibes") or []
        vibe["secondaryVibes"] = [
            s for s in sec
            if isinstance(s, str) and is_valid_primary_vibe(s) and s.lower() not in _EVENT_NAMES
        ]
        # Occasion rehash on S4: never commit vibe from the pasted place/date line
        if occasion_rehash and stage == StageId.S4_VIBE.value:
            patch.pop("vibe", None)
        elif not vibe.get("primaryVibe") and not vibe.get("secondaryVibes"):
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
