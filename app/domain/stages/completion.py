"""
Stage completion — deterministic, backend-owned checks for whether a stage is
done, plus the runtime gap guide that tells the LLM what this turn still needs.
"""
from __future__ import annotations

from app.domain.enums import StageId


def is_stage_complete(stage: str, memory: dict) -> bool:
    """Deterministic completion checks — backend owns stage movement."""
    try:
        stage_id = StageId(stage)
    except ValueError:
        return False

    if stage_id == StageId.S2_BASICS:
        from app.domain.text_extract import get_occasion_state
        return get_occasion_state(memory)["is_complete"]

    if stage_id == StageId.S3_PERSONALITY:
        from app.domain.text_extract import filter_tags
        p = memory.get("personality", {})
        tags = filter_tags(p.get("tags") or [])
        # Hard rule: culturalSignals alone (often from occasion paste) never unlock S3.
        # Need real personality tags — 2+, or 1 tag plus relationship/lifestyle (not culture-only).
        rel = len(p.get("relationshipSignals") or [])
        life = len(p.get("lifestyleSignals") or [])
        return len(tags) >= 2 or (len(tags) >= 1 and (rel + life) >= 1)

    if stage_id == StageId.S4_VIBE:
        from app.domain.memory_schema import resolve_primary_vibe
        # Cannot complete vibe (and brief) without a real personality stage fill
        if not is_stage_complete(StageId.S3_PERSONALITY.value, memory):
            return False
        return bool(resolve_primary_vibe(memory))

    if stage_id == StageId.S6_DIRECTIONS:
        direction = memory.get("direction", {})
        return bool((direction.get("selectedDirectionId") or "").strip())

    if stage_id == StageId.S7_EVENTS:
        logistics = memory.get("logistics", {}) or {}
        events = logistics.get("events") or []
        if len(events) < 1:
            return False
        return bool(logistics.get("eventsConfirmed"))

    if stage_id == StageId.S8_GUESTS:
        events = memory.get("logistics", {}).get("events") or []
        counts = memory.get("logistics", {}).get("guestCounts") or {}
        if not events or not isinstance(counts, dict):
            return False
        return all(
            isinstance(counts.get(ev), int) and counts.get(ev, 0) > 0
            for ev in events
        )

    if stage_id == StageId.S9_BUDGET:
        budget = memory.get("logistics", {}).get("budget") or {}
        return bool((budget.get("range") or budget.get("amount") or "").strip())

    if stage_id == StageId.S10_VENDORS:
        prefs = memory.get("logistics", {}).get("vendorPreferences") or {}
        return isinstance(prefs, dict) and len(prefs) >= 1

    return False


def get_stage_gap_guide(stage: str, memory: dict, user_message: str = "") -> str:
    """
    Tell the agent what is missing and what THIS message can fill,
    so stay/advance + question stay aligned.
    """
    from datetime import date
    today = date.today().isoformat()
    msg = (user_message or "").strip()

    if stage == StageId.S2_BASICS.value:
        from app.domain.text_extract import (
            extract_month_or_season,
            extract_place_from_message,
            get_occasion_state,
            is_past_date,
        )
        state = get_occasion_state(memory)
        place_in_msg = extract_place_from_message(msg) if msg else None
        timing_in_msg = extract_month_or_season(msg) if msg else {}
        raw_date = (timing_in_msg.get("datePreference") or "").strip()
        season_in_msg = (timing_in_msg.get("seasonPreference") or "").strip()
        date_past = bool(raw_date and is_past_date(raw_date))

        will_have_place = state["has_place"] or bool(place_in_msg)
        will_have_time = state["has_time"] or bool(season_in_msg) or (
            bool(raw_date) and not date_past
        )

        notes = [f"Today: {today}."]
        if place_in_msg:
            notes.append(f"This message includes place → patch occasion.place=\"{place_in_msg}\".")
        if raw_date and date_past:
            notes.append(
                f"This message includes \"{raw_date}\" but that is PAST — "
                f"do NOT put it in datePreference. Stay and ask for a FUTURE month/year."
            )
        elif raw_date:
            notes.append(
                f"This message includes future timing → patch occasion.datePreference=\"{raw_date}\"."
            )
        if season_in_msg:
            notes.append(f"Season named → patch seasonPreference=\"{season_in_msg}\".")

        if will_have_place and will_have_time:
            notes.append(
                "After this patch S2 will be COMPLETE → stageDecision.type=advance, "
                "acknowledge place+date, ask about the COUPLE (s3). Do not ask about setting."
            )
            return " ".join(notes)

        missing = []
        if not will_have_place:
            missing.append("place (city/region)")
        if not will_have_time:
            missing.append("future month+year (e.g. December 2026) or named season")
        notes.append(
            f"S2 still incomplete — need: {', '.join(missing)}. "
            f"stageDecision.type=stay. Ask ONLY for missing fields. "
            f"NEVER ask personality / relationship / vibe while on s2."
        )
        return " ".join(notes)

    if stage == StageId.S3_PERSONALITY.value:
        from app.domain.text_extract import filter_tags
        tags = filter_tags((memory.get("personality") or {}).get("tags") or [])
        if is_stage_complete(stage, memory):
            return "S3 COMPLETE — you may advance to s4_vibe and ask about vibe."
        return (
            f"Have tags: {tags or '(none)'}. "
            f"Need 2+ meaningful tags (or 1 + relationship/lifestyle). Stay; ask about the couple."
        )

    if stage == StageId.S4_VIBE.value:
        from app.domain.memory_schema import resolve_primary_vibe
        if resolve_primary_vibe(memory):
            return "S4 COMPLETE — you may advance (brief synthesis follows)."
        early = (memory.get("earlySignals") or {}).get("vibe") or []
        hint = f" earlySignals.vibe={early}." if early else ""
        return f"S4 INCOMPLETE — need vibe pool primaryVibe.{hint} Stay; ask vibe only."

    if is_stage_complete(stage, memory):
        return f"Stage {stage} complete in memory — you may propose advance if this turn confirms it."
    return f"Stage {stage} not complete — stay and ask only for what this stage still needs."
