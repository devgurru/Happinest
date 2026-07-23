"""
Wedding Graph State — TypedDict for LangGraph state management.

This is the central state that flows through every node in the graph.
It replaces the ad-hoc dict passing in the old orchestrator.
"""
from __future__ import annotations

from typing import TypedDict


class WeddingState(TypedDict, total=False):
    """State passed between graph nodes. All fields optional (total=False)."""

    # ── Core identifiers ─────────────────────────────────────────────────────
    session_id: str
    request_id: str
    current_stage: str

    # ── Session/Memory data (loaded by load_session) ─────────────────────────
    session: object  # Session ORM object
    memory: dict
    memory_before: dict  # snapshot for correction detection
    memory_version: int
    db: object  # AsyncSession — passed through, not serialized

    # ── Turn input ───────────────────────────────────────────────────────────
    user_message: str
    images: list  # base64 image strings
    event_type: str

    # ── Image analysis output ────────────────────────────────────────────────
    image_patch: dict
    image_context: str
    image_telemetry: dict

    # ── Intent classification output (Call 1) ────────────────────────────────
    intent: dict  # {intentType, targetSections, decisionHint, summary}

    # ── Conversation turn output (Call 2) ────────────────────────────────────
    ai_result: dict  # raw AI response
    ai_telemetry: dict

    # ── Validation output ────────────────────────────────────────────────────
    validation_ok: bool
    validation_error: str | None

    # ── Stage decision output ────────────────────────────────────────────────
    decision_type: str
    to_stage: str
    decision_reason: str | None

    # ── Correction detection output ──────────────────────────────────────────
    correction: dict | None
    stale_sections: list
    correction_ack: str

    # ── Synthesis ────────────────────────────────────────────────────────────
    synthesis_type: str | None
    synthesis_result: dict | None

    # ── Final response assembly ──────────────────────────────────────────────
    planner_reply: str
    memory_patch: dict
    suggestions: list
    open_questions: list
    selected_chips: dict | None
    planner_notes_view: dict
    artifact_content: dict | None
    error_code: str | None

    # ── Retry tracking ───────────────────────────────────────────────────────
    retry_count: int
    response_source: str

    # ── History ──────────────────────────────────────────────────────────────
    recent_messages: list  # last N messages for context
    last_planner_reply: str
