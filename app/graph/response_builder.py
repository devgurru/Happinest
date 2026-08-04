"""
Response Builder — response dict factories for the wedding pipeline.

Extracted from app/graph/wedding_graph.py. These are pure functions that
build standardized response dicts — no DB or session dependencies.
"""
from __future__ import annotations

import re
import uuid
from typing import Any

from app.domain.enums import ResponseSource, StageId
from app.domain.memory_schema import build_planner_notes_view, build_selected_chips


# ─────────────────────────────────────────────────────────────────────────────
# Response helpers
# ─────────────────────────────────────────────────────────────────────────────

def extract_brief_artifact_if_present(stage: str, memory: dict, artifact_content: dict | None) -> dict | None:
    if artifact_content:
        return artifact_content
    stage_val = (stage or "").strip()
    if stage_val == StageId.S5_BRIEF.value:
        brief_data = (memory.get("brief") or {}) if isinstance(memory, dict) else {}
        brief_text = (brief_data.get("text") or "").strip()
        if brief_text:
            return {
                "briefText": brief_text,
                "briefQuote": brief_data.get("quote") or "",
            }
    elif stage_val == StageId.S6_DIRECTIONS.value:
        dir_data = (memory.get("direction") or {}) if isinstance(memory, dict) else {}
        options = dir_data.get("options") or []
        if options:
            return {
                "directionOptions": options,
            }
    elif stage_val == StageId.S8_GUESTS.value:
        logistics = (memory.get("logistics") or {}) if isinstance(memory, dict) else {}
        events = logistics.get("events") or []
        counts = logistics.get("guestCounts") or {}
        if events or counts:
            return {
                "events": events,
                "guestCounts": counts,
            }
    elif stage_val == StageId.S10_VENDORS.value:
        from app.services.ui.ui_hints import build_vendor_suggestions_by_event
        logistics = (memory.get("logistics") or {}) if isinstance(memory, dict) else {}
        events = logistics.get("events") or []
        counts = logistics.get("guestCounts") or {}
        budget = logistics.get("budget") or {}
        vendor_prefs = logistics.get("vendorPreferences") or {}
        event_suggestions = build_vendor_suggestions_by_event(events)
        return {
            "events": events,
            "guestCounts": counts,
            "budget": budget,
            "eventVendorSuggestions": event_suggestions,
            "vendorPreferences": vendor_prefs,
        }
    return None


def make_error_response(
    request_id: uuid.UUID | str,
    session_id: uuid.UUID | str,
    stage: str,
    memory: dict,
    error_code: str,
    message: str = "Something went wrong. Please try again.",
) -> dict:
    return {
        "requestId": str(request_id),
        "sessionId": str(session_id),
        "responseSource": ResponseSource.ERROR.value,
        "plannerReply": message,
        "updatedMemoryVersion": None,
        "stageDecision": {"type": "stay", "stage": stage},
        "openQuestions": [],
        "suggestions": [],
        "selectedChips": build_selected_chips(memory),
        "plannerNotesView": build_planner_notes_view(memory),
        "artifactContent": extract_brief_artifact_if_present(stage, memory, None),
        "errorCode": error_code,
    }


def response_dict(
    request_id: uuid.UUID | str,
    session_id: uuid.UUID | str,
    response_source: str,
    planner_reply: str,
    memory: dict,
    *,
    memory_patch: dict | None = None,
    updated_version: int | None = None,
    stage_decision: dict | None = None,
    stale_sections: list | None = None,
    open_questions: list | None = None,
    suggestions: list | None = None,
    artifact_content: dict | None = None,
    error_code: str | None = None,
) -> dict:
    # Normalize suggestions to list of clean label strings
    clean_suggs: list[str] = []
    for item in (suggestions or []):
        if isinstance(item, str) and item.strip():
            if item.strip() not in clean_suggs:
                clean_suggs.append(item.strip())
        elif isinstance(item, dict):
            lbl = item.get("label", "")
            if isinstance(lbl, str) and lbl.strip() and lbl.strip() not in clean_suggs:
                clean_suggs.append(lbl.strip())

    stage_val = stage_decision.get("stage") if isinstance(stage_decision, dict) else stage
    effective_artifact = extract_brief_artifact_if_present(str(stage_val), memory, artifact_content)

    return {
        "requestId": str(request_id),
        "sessionId": str(session_id),
        "responseSource": response_source,
        "plannerReply": planner_reply,
        "updatedMemoryVersion": updated_version,
        "stageDecision": stage_decision or {"type": "stay", "stage": "s2_basics"},
        "openQuestions": open_questions or [],
        "suggestions": clean_suggs,
        "selectedChips": build_selected_chips(memory, stage=stage_val),
        "plannerNotesView": build_planner_notes_view(memory),
        "artifactContent": effective_artifact,
        "errorCode": error_code,
    }
