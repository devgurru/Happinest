"""
Correction Policy — upstream memory changes, invalidation, and stage decisions.

Per docs 05 + 06:
- Mark stale sections immediately when upstream assumptions change
- Prefer reanchor on the CURRENT stage (do not force UX backward)
- Support multi-section updates in one turn
- Regenerates are lazy or explicit (brief/direction when on those surfaces)
"""
from __future__ import annotations

import copy

from app.domain.enums import StageDecisionType, StageId
from app.services.memory_service import compute_stale_sections

_SECTION_TO_STAGE: dict[str, str] = {
    "identity": StageId.S1_NAMES.value,
    "occasion": StageId.S2_BASICS.value,
    "personality": StageId.S3_PERSONALITY.value,
    "vibe": StageId.S4_VIBE.value,
    "logistics": StageId.S7_EVENTS.value,
}

_STAGE_ORDER = [s.value for s in StageId.ordered()]

_CURRENT_STAGE_SECTION: dict[str, str] = {
    StageId.S1_NAMES.value: "identity",
    StageId.S2_BASICS.value: "occasion",
    StageId.S3_PERSONALITY.value: "personality",
    StageId.S4_VIBE.value: "vibe",
    StageId.S7_EVENTS.value: "logistics",
    StageId.S8_GUESTS.value: "logistics",
    StageId.S9_BUDGET.value: "logistics",
    StageId.S10_VENDORS.value: "logistics",
}


def _section_stage_index(section: str) -> int:
    stage = _SECTION_TO_STAGE.get(section)
    if not stage:
        return 999
    try:
        return _STAGE_ORDER.index(stage)
    except ValueError:
        return 999


def _section_changed(before: dict, after: dict, section: str) -> bool:
    return before.get(section) != after.get(section)


def detect_upstream_correction(
    patch: dict,
    memory_before: dict,
    memory_after: dict,
    current_stage: str,
) -> dict | None:
    """
    Detect when a turn updated memory in a way that should invalidate
    downstream derived artifacts (docs 05 + Example 3/4/5).

    First-time fills of the *current* stage section are NOT corrections.
    Upstream changes and multi-section updates ARE corrections → reanchor + stale.
    """
    if not patch:
        return None

    # S6 direction selection is normal stage completion — not an upstream correction
    if current_stage == StageId.S6_DIRECTIONS.value:
        before_dir = memory_before.get("direction") or {}
        after_dir = memory_after.get("direction") or {}
        new_pick = after_dir.get("selectedDirectionId") and (
            after_dir.get("selectedDirectionId") != before_dir.get("selectedDirectionId")
        )
        if new_pick or patch.get("direction", {}).get("selectedDirectionId"):
            return None

    try:
        current_idx = _STAGE_ORDER.index(current_stage)
    except ValueError:
        return None

    changed_sections: list[str] = []
    for section in ("identity", "occasion", "personality", "vibe"):
        if section in patch and _section_changed(memory_before, memory_after, section):
            changed_sections.append(section)

    # Logistics events change (e.g. adding Sangeet on S8)
    log_before = memory_before.get("logistics") or {}
    log_after = memory_after.get("logistics") or {}
    if log_before.get("events") != log_after.get("events"):
        if "logistics" not in changed_sections:
            changed_sections.append("logistics")

    if not changed_sections:
        return None

    stage_section = _CURRENT_STAGE_SECTION.get(current_stage)

    upstream = [
        s for s in changed_sections
        if _section_stage_index(s) < current_idx
    ]

    # Normal first fill of only the current stage's section → not a correction
    if (
        stage_section
        and changed_sections == [stage_section]
        and not upstream
    ):
        return None

    mapped = [s for s in changed_sections if s in _SECTION_TO_STAGE]
    if not mapped:
        return None

    earliest = min(mapped, key=lambda s: _section_stage_index(s))
    target_stage = _SECTION_TO_STAGE[earliest]
    stale = compute_stale_sections(patch, memory_before.get("staleSections", []))

    # If no stale sections produced and no upstream change, skip
    if not stale and not upstream and changed_sections == [stage_section]:
        return None

    return {
        "correctedSections": changed_sections,
        "upstreamSections": upstream,
        "targetStage": target_stage,
        "staleSections": stale,
        "decisionType": StageDecisionType.REANCHOR.value,
        # Brief regen only when user is ON the brief screen — not S6
        "shouldRegenerateBrief": (
            "brief" in stale
            and current_stage == StageId.S5_BRIEF.value
        ),
        "shouldRegenerateDirection": (
            "direction" in stale
            and current_stage == StageId.S6_DIRECTIONS.value
        ),
        # On S6 any upstream tweak should refresh directions (even if brief also stale)
        "shouldRefreshDirectionsOnS6": (
            current_stage == StageId.S6_DIRECTIONS.value
            and bool(upstream or changed_sections)
        ),
    }


def apply_stale_artifact_markers(patch: dict, stale_sections: list[str]) -> dict:
    """Mark downstream synthesized sections stale inside the memory patch."""
    patch = copy.deepcopy(patch or {})
    if "brief" in stale_sections:
        brief = dict(patch.get("brief") or {})
        brief["status"] = "stale"
        patch["brief"] = brief
    if "direction" in stale_sections:
        direction = dict(patch.get("direction") or {})
        direction["status"] = "stale"
        direction["selectedDirectionId"] = ""
        patch["direction"] = direction
    if "summary" in stale_sections:
        summary = dict(patch.get("summary") or {})
        summary["status"] = "stale"
        patch["summary"] = summary
    return patch


def resolve_correction_stage_decision(
    correction: dict,
    current_stage: str,
) -> tuple[str, str, str]:
    """
    Prefer reanchor on current stage (Example 3/5).

    JUMP is reserved for rare cases where product explicitly wants to move
    the guided shell — default path keeps the conversation continuous.
    """
    # Docs: keep conversational, do not force user backward
    return (
        StageDecisionType.REANCHOR.value,
        current_stage,
        "upstream_correction_reanchor",
    )
