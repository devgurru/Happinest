"""
Turn Intent Classifier — rule-based middle layer before AI.

Purpose (per docs 02 / 05 / 06):
- Detect EXPLICIT corrections / multi-updates that target upstream memory
- Propose a memory patch for those cases
- Propose stageDecision type: reanchor (preferred) | jump (rare) | stay
- NEVER treat a normal answer to the current stage as a correction

Normal stage progression is owned by StagePolicy + memory completeness —
this layer must not block advance.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.domain.enums import StageDecisionType, StageId

_SECTION_TO_STAGE: dict[str, str] = {
    "identity": StageId.S1_NAMES.value,
    "occasion": StageId.S2_BASICS.value,
    "personality": StageId.S3_PERSONALITY.value,
    "vibe": StageId.S4_VIBE.value,
}

_STAGE_ORDER = [s.value for s in StageId.ordered()]

# Explicit correction / override language only (not stage vocabulary)
_CORRECTION_CUES = (
    "actually", "oh sorry", "sorry,", "sorry ", "i meant", "meant to",
    "change ", "update ", "wrong", "mistake", "correct ", "instead",
    "replace", "not ", "rather than",
)

_ADDITION_CUES = (
    "also want", "also add", "add ", "along with", "as well as",
    "i also", "plus ",
)

_IDENTITY_EXPLICIT = (
    "partner name", "my name", "her name", "his name", "client name",
    "spelled", "spelling", "rename", "name wrong", "name is wrong",
)

_OCCASION_EXPLICIT = (
    "location", "place", "city", "destination", "venue", "on a beach",
    "beach wedding", "move it to", "date to", "in december", "in january",
    "in february", "in march", "in april", "in may", "in june",
    "in july", "in august", "in september", "in october", "in november",
)

_PERSONALITY_EXPLICIT = (
    "personality", "to the personality", "our tags", "who we are",
)

_VIBE_EXPLICIT = (
    "the vibe", "our vibe", "change the vibe", "update the vibe",
    "feeling to", "more intimate", "more festive", "make it more",
)


@dataclass
class TurnIntent:
    target_sections: list[str] = field(default_factory=list)
    target_stage: str | None = None
    # None = no opinion — StagePolicy decides advance/stay
    decision_type: str | None = None
    is_correction: bool = False
    is_addition: bool = False
    suggested_patch: dict = field(default_factory=dict)
    confidence: str = "low"  # low | medium | high


def _stage_index(stage: str) -> int:
    try:
        return _STAGE_ORDER.index(stage)
    except ValueError:
        return len(_STAGE_ORDER)


def _section_index(section: str) -> int:
    stage = _SECTION_TO_STAGE.get(section)
    if not stage:
        return 999
    return _STAGE_ORDER.index(stage)


def _has_any(msg: str, cues: tuple[str, ...]) -> bool:
    return any(c in msg for c in cues)


def _extract_name_change(message: str) -> dict | None:
    patterns = [
        r"(?:change|update|correct|fix|rename).*?from\s+['\"]?(\w+)['\"]?\s+to\s+['\"]?(\w+)['\"]?",
        r"(?:should be|is actually|is really)\s+['\"]?(\w+)['\"]?\s+not\s+['\"]?(\w+)['\"]?",
        r"partner(?:'s)?\s+name\s+(?:is|should be)\s+['\"]?(\w+)['\"]?",
    ]
    for pattern in patterns:
        m = re.search(pattern, message.strip(), re.IGNORECASE)
        if not m:
            continue
        groups = m.groups()
        if len(groups) == 2:
            return {"old": groups[0], "new": groups[1]}
        if len(groups) == 1:
            return {"new": groups[0]}
    return None


def _extract_added_tags(message: str, pool: list[str]) -> list[str]:
    """Extract comma-separated and natural-language tags including custom ones."""
    from app.services.text_extract import MONTHS, filter_tags, is_junk_tag
    from app.services.ui_hints import chips_mentioned_in_message

    found: list[str] = list(chips_mentioned_in_message(message, pool))
    msg_l = message.lower().strip()

    # Pure date answers must not become personality tags
    if re.fullmatch(
        r"(i\s+think\s+in\s+|in\s+|around\s+|maybe\s+)?"
        r"(january|february|march|april|may|june|july|august|september|october|november|december)"
        r"(\s+\d{4})?\.?",
        msg_l,
    ):
        return filter_tags(found)

    for segment in re.split(r"[,;/]|\band\b", message):
        segment = segment.strip().strip(".")
        if not segment or len(segment) < 2 or len(segment.split()) > 5:
            continue
        seg_l = segment.lower()
        if any(m in seg_l for m in MONTHS):
            continue
        if any(c in seg_l for c in ("actually", "want to", "add ", "change", "sorry", "along with", "update")):
            add_m = re.search(
                r"(?:add|update)\s+(.+?)(?:\s+to\s+(?:the\s+)?personality|\s+along|\s*$)",
                segment,
                re.I,
            )
            if add_m:
                segment = add_m.group(1).strip()
            else:
                continue
        if any(segment.lower() == p.lower() for p in found):
            continue
        matched = False
        for p in pool:
            if p.lower() == segment.lower() or p.lower() in segment.lower():
                if p not in found:
                    found.append(p)
                matched = True
                break
        if not matched and not is_junk_tag(segment):
            custom = segment[0].upper() + segment[1:] if segment else segment
            if custom not in found:
                found.append(custom)
    return filter_tags(found)


def _extract_occasion_from_message(message: str, memory: dict) -> dict:
    """Pull place / date / setting signals for multi-update and corrections."""
    from app.services.text_extract import extract_month_or_season, sanitize_timing_fields
    from app.services.turn_interpreter import find_direction_option_in_message

    if find_direction_option_in_message(message, memory):
        return sanitize_timing_fields(dict(memory.get("occasion") or {}))

    occasion = dict(memory.get("occasion") or {})
    msg_l = message.lower()
    for city in (
        "delhi", "mumbai", "udaipur", "jaipur", "goa", "bangalore",
        "chennai", "hyderabad", "kolkata", "agra", "jodhpur",
    ):
        if city in msg_l:
            occasion["place"] = city.title()
            break
    if "beach" in msg_l:
        occasion["locationPreference"] = "beach"
        occasion["settingPreference"] = "beach"
    occasion.update(extract_month_or_season(message))
    if "destination" in msg_l:
        occasion["destinationMode"] = "destination"
    return sanitize_timing_fields(occasion)


def classify_turn_intent(
    message: str,
    current_stage: str,
    memory: dict,
) -> TurnIntent:
    """
    Classify whether this turn is a normal answer or an explicit correction/multi-update.
    For normal answers: returns is_correction=False and decision_type=None
    so StagePolicy can advance freely.
    """
    msg_l = message.lower().strip()
    intent = TurnIntent()
    current_idx = _stage_index(current_stage)

    has_correction_cue = _has_any(msg_l, _CORRECTION_CUES)
    has_addition_cue = _has_any(msg_l, _ADDITION_CUES)
    intent.is_addition = has_addition_cue

    # Without explicit correction/addition language, treat as normal stage answer.
    # Stage vocabulary alone (festive, foodies, intimate) must NOT block advance.
    if not has_correction_cue and not has_addition_cue:
        return intent

    intent.is_correction = True
    sections: list[str] = []

    # ── Identity ────────────────────────────────────────────────────────────
    if _has_any(msg_l, _IDENTITY_EXPLICIT) or _extract_name_change(message):
        sections.append("identity")
        name_change = _extract_name_change(message)
        if name_change and name_change.get("new"):
            identity = dict(memory.get("identity", {}))
            new_name = name_change["new"].title()
            if "partner" in msg_l or "her name" in msg_l or "his name" in msg_l:
                identity["partnerName"] = new_name
            elif "my name" in msg_l or "client" in msg_l:
                identity["clientName"] = new_name
            else:
                identity["partnerName"] = new_name
            c = identity.get("clientName", "")
            p = identity.get("partnerName", "")
            identity["displayName"] = f"{c} & {p}" if c and p else c or p
            intent.suggested_patch["identity"] = identity
            intent.confidence = "high"

    # ── Occasion ────────────────────────────────────────────────────────────
    from app.services.turn_interpreter import find_direction_option_in_message
    if (
        not find_direction_option_in_message(message, memory)
        and (_has_any(msg_l, _OCCASION_EXPLICIT) or "beach" in msg_l)
    ):
        sections.append("occasion")
        occ = _extract_occasion_from_message(message, memory)
        # Only keep keys that actually changed
        before = memory.get("occasion") or {}
        delta = {k: v for k, v in occ.items() if v and v != before.get(k)}
        if delta:
            intent.suggested_patch["occasion"] = delta
            intent.confidence = "high" if intent.confidence != "high" else "high"

    # ── Personality ─────────────────────────────────────────────────────────
    personality_mentioned = (
        _has_any(msg_l, _PERSONALITY_EXPLICIT)
        or ("personality" in msg_l and "vibe" not in msg_l)
    )
    logistics_stages = {
        StageId.S7_EVENTS.value,
        StageId.S8_GUESTS.value,
        StageId.S9_BUDGET.value,
        StageId.S10_VENDORS.value,
        StageId.S11_SUMMARY.value,
    }
    from app.services.patch_sanitizer import _is_event_focused_message

    if personality_mentioned and not (
        current_stage in logistics_stages and _is_event_focused_message(message)
    ):
        from app.domain.chip_pools import get_chip_pool
        pool = get_chip_pool(StageId.S3_PERSONALITY.value)
        tags = _extract_added_tags(message, pool)
        if tags or "personality" in msg_l:
            sections.append("personality")
            if tags:
                existing = memory.get("personality", {}).get("tags") or []
                merged: list[str] = []
                seen: set[str] = set()
                for t in existing + tags:
                    key = t.lower().strip()
                    if key in seen:
                        continue
                    seen.add(key)
                    merged.append(t.strip())
                intent.suggested_patch["personality"] = {"tags": merged}
                intent.confidence = "high"

    # ── Vibe ────────────────────────────────────────────────────────────────
    vibe_mentioned = _has_any(msg_l, _VIBE_EXPLICIT) or (
        has_correction_cue and "vibe" in msg_l
    )
    if vibe_mentioned and not (
        current_stage in logistics_stages and _is_event_focused_message(message) and "vibe" not in msg_l
    ):
        sections.append("vibe")
        from app.domain.chip_pools import get_chip_pool
        from app.services.ui_hints import chips_mentioned_in_message
        pool = get_chip_pool(StageId.S4_VIBE.value)
        found = chips_mentioned_in_message(message, pool)
        vibe = dict(memory.get("vibe") or {})
        primary = vibe.get("primaryVibe") or ""
        secondary = list(vibe.get("secondaryVibes") or [])

        # "add light music" / "update vibe to add X" → secondary, keep primary
        add_vibe_m = re.search(
            r"(?:add|include)\s+(.+?)(?:\s+as well|\s+too|\s+also|\s*$)",
            message,
            re.I,
        )
        if add_vibe_m and ("vibe" in msg_l or has_addition_cue):
            extra = add_vibe_m.group(1).strip().strip(".")
            if extra and extra.lower() not in {s.lower() for s in secondary}:
                if extra.lower() not in primary.lower():
                    secondary.append(extra[0].upper() + extra[1:] if extra else extra)
        elif found:
            if has_addition_cue and primary and found[0].lower() != primary.lower():
                for f in found:
                    if f.lower() not in {s.lower() for s in secondary} and f.lower() != primary.lower():
                        secondary.append(f)
            else:
                primary = found[0]
        elif "intimate" in msg_l:
            primary = "Intimate"
        elif "festive" in msg_l:
            primary = "Big & festive"

        patch_vibe: dict = {}
        if primary:
            patch_vibe["primaryVibe"] = primary
        if secondary:
            patch_vibe["secondaryVibes"] = secondary
        if patch_vibe:
            intent.suggested_patch["vibe"] = patch_vibe
            if intent.confidence == "low":
                intent.confidence = "medium" if not found else "high"

    # ── Events (logistics) on S7+ when user adds functions ──────────────────
    if current_stage in logistics_stages and _is_event_focused_message(message):
        from app.domain.chip_pools import get_chip_pool
        from app.services.ui_hints import chips_mentioned_in_message
        pool = get_chip_pool(StageId.S7_EVENTS.value)
        found = chips_mentioned_in_message(message, pool)
        if "reception" in msg_l and "Reception" not in found:
            found.append("Reception")
        if found:
            existing = memory.get("logistics", {}).get("events") or []
            merged_ev: list[str] = list(existing)
            seen_ev = {e.lower() for e in merged_ev}
            for e in found:
                if e.lower() not in seen_ev:
                    merged_ev.append(e)
                    seen_ev.add(e.lower())
            intent.suggested_patch["logistics"] = {"events": merged_ev}
            if "logistics" not in sections:
                sections.append("logistics")
            intent.confidence = "high"

    intent.target_sections = sections

    if not sections and not intent.suggested_patch:
        # Correction language but no section resolved — let AI interpret; don't force stay
        intent.is_correction = True
        intent.decision_type = StageDecisionType.REANCHOR.value
        intent.target_stage = current_stage
        intent.confidence = "low"
        return intent

    # Earliest upstream section (for jump target metadata only)
    upstream = [s for s in sections if s in _SECTION_TO_STAGE and _section_index(s) < current_idx]
    if upstream:
        earliest = min(upstream, key=_section_index)
        intent.target_stage = _SECTION_TO_STAGE[earliest]
    else:
        intent.target_stage = current_stage

    # Doc rule (Example 3 / 5): corrections reanchor on CURRENT stage —
    # do not force the user backward through screens.
    intent.decision_type = StageDecisionType.REANCHOR.value
    if intent.confidence == "low" and intent.suggested_patch:
        intent.confidence = "medium"

    return intent
