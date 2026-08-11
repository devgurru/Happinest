"""
Direction Service — S6 design direction workflow.

Extracted from app/graph/wedding_graph.py. Handles the full direction flow:
keyword detection, embedding search, option building, DB persistence, and response assembly.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import (
    ArtifactStatus, ArtifactType, EventType, MessageRole, MessageType,
    ResponseSource, StageDecisionType, StageId,
)
from app.graph.response_builder import make_error_response, response_dict
from app.models.event_site import EventSite
from app.models.generated_artifact import GeneratedArtifact
from app.models.session_event_site_recommendation import SessionEventSiteRecommendation
from app.services.ai.embedding_service import find_matching_event_sites
from app.services.session.memory_service import MemoryService
from app.services.session.session_service import SessionService
from app.services.ui.observability import log_ai_turn


def is_direction_request(message: str) -> bool:
    msg = (message or "").lower().strip()
    keywords = (
        "direction", "directions", "design direction", "show me direction",
        "show directions", "see direction", "rethink", "something different",
        "different option", "different options", "more option", "more options",
        "other option", "other options", "another option", "another direction",
        "new direction", "new options", "try something different",
        "none of these", "don't work", "dont work", "change direction",
    )
    return any(k in msg for k in keywords)


def direction_options_from_sites(sites: list[dict], offset: int = 0) -> list[dict]:
    """Build S6 directionOptions from embedding matches — supports rank offset for rethink."""
    options = []
    selected_sites = sites[offset : offset + 3]
    if not selected_sites:
        selected_sites = sites[:3]

    for i, site in enumerate(selected_sites, start=1):
        profile = site.get("profile_json") or {}
        slug = site.get("slug") or str(site.get("id") or f"option-{i}")
        name = site.get("name") or slug
        reason = (
            profile.get("plannerInterpretation")
            or site.get("short_description")
            or "Strong fit for your brief and setting."
        )
        sim = site.get("similarity")
        options.append({
            "id": slug,
            "name": name,
            "rankOrder": i,
            "fitScore": round(float(sim), 3) if sim is not None else None,
            "reasonText": reason,
            "siteType": site.get("site_type"),
            "shortDescription": site.get("short_description"),
            "profileJson": profile,
            "heroImageUrl": site.get("hero_image_url"),
            "galleryJson": site.get("gallery_json") or [],
            "isActive": site.get("is_active"),
            "seedVersion": site.get("seed_version"),
            "styleTags": profile.get("styleTags") or [],
            "vibeTags": profile.get("vibeTags") or [],
        })
    return options


def build_direction_planner_reply(
    options: list[dict],
    place: str,
    *,
    correction_ack: str = "",
) -> str:
    """Introduce directions with the top embedding match called out."""
    if not options:
        return "Here are design directions for your celebration."
    top = options[0]
    top_name = top.get("name") or "your top match"
    reason = (top.get("reasonText") or top.get("shortDescription") or "").strip()
    if len(reason) > 160:
        reason = reason[:157] + "..."
    intro = (
        f"My top recommendation for you is {top_name}"
        + (f" — {reason}" if reason else "")
        + ". I've included two more directions below that also fit well."
    )
    if correction_ack:
        return f"{correction_ack} {intro} Which one feels closest — or tell me what to tweak?"
    return (
        f"Based on what I know about you and {place}, {intro.lower()} "
        f"Which one feels closest — or tell me what to tweak?"
    )


async def persist_direction_recommendations(
    db: AsyncSession,
    session_id: uuid.UUID,
    options: list[dict],
    updated_version: int,
    request_id: uuid.UUID,
) -> None:
    batch_id = uuid.uuid4()
    for opt in options:
        result = await db.execute(
            select(EventSite.id).where(EventSite.slug == opt.get("id"))
        )
        site_id = result.scalar_one_or_none()
        if site_id:
            db.add(SessionEventSiteRecommendation(
                session_id=session_id,
                event_site_id=site_id,
                recommendation_batch_id=batch_id,
                rank_order=opt.get("rankOrder", 99),
                reason_text=opt.get("reasonText"),
                score=opt.get("fitScore"),
                generated_from_memory_version=updated_version,
                request_id=request_id,
            ))


async def execute_direction_from_embeddings(
    db: AsyncSession,
    session: Any,
    session_id: uuid.UUID,
    stage: str,
    request_id: uuid.UUID,
    *,
    save_planner_message: bool = True,
) -> dict:
    """Fast S6 path: embed canonical memory → top event sites → return top 3."""
    mem_version = await MemoryService.get_latest_memory(db, session_id)
    if not mem_version:
        raise ValueError(f"No memory for session {session_id}")
    memory = mem_version.memory_json

    try:
        candidates = await find_matching_event_sites(db, memory, top_k=30)
    except Exception as e:
        await log_ai_turn(
            db, request_id, session_id, stage,
            EventType.SYNTHESIS_REQUEST.value,
            ResponseSource.ERROR.value,
            prompt_family="direction_embedding",
            failure_code="EMBEDDING_FAILED",
            validation_status="rejected",
        )
        return make_error_response(
            request_id, session_id, stage, memory, "EMBEDDING_FAILED",
            message=f"Could not match directions right now ({e}). Please try again.",
        )

    direction_data = (memory.get("direction") or {}) if isinstance(memory, dict) else {}
    existing_options = direction_data.get("options") or []
    seen_ids = set(direction_data.get("seenOptionIds") or [])
    for opt in existing_options:
        if isinstance(opt, dict) and opt.get("id"):
            seen_ids.add(opt["id"])

    # Filter out candidate sites already shown to the user
    unseen_candidates = [
        c for c in candidates
        if c.get("slug") not in seen_ids and str(c.get("id") or "") not in seen_ids
    ]

    is_rethink = len(seen_ids) > 0
    if len(unseen_candidates) < 3:
        unseen_candidates = candidates
        seen_ids = set()

    options = direction_options_from_sites(unseen_candidates)
    if not options:
        return make_error_response(
            request_id, session_id, stage, memory, "NO_DIRECTION_CANDIDATES",
            message="I couldn't find matching directions yet. Please try again shortly.",
        )

    for opt in options:
        seen_ids.add(opt["id"])

    response_source = ResponseSource.RULE.value
    place = (memory.get("occasion") or {}).get("place") or "your celebration"
    if is_rethink:
        planner_reply = f"Here are alternative design direction concepts for your celebration in {place}:"
    else:
        planner_reply = build_direction_planner_reply(options, place)

    patch = {
        "direction": {
            "options": options,
            "seenOptionIds": list(seen_ids),
            "status": "ready",
            "selectedDirectionId": "",
        }
    }
    new_mem = await MemoryService.apply_patch(db, session, patch, request_id=request_id)
    memory = new_mem.memory_json
    updated_version = new_mem.version_no

    artifact_content = {"directionOptions": options}
    await persist_direction_recommendations(
        db, session_id, options, updated_version, request_id
    )
    db.add(GeneratedArtifact(
        session_id=session_id,
        artifact_type=ArtifactType.DIRECTION.value,
        status=ArtifactStatus.READY.value,
        content_json=artifact_content,
        generated_from_memory_version=updated_version,
        request_id=request_id,
    ))
    await db.flush()

    final_decision_type = StageDecisionType.ADVANCE.value
    final_stage = StageId.S6_DIRECTIONS.value
    if final_stage != session.current_stage:
        await SessionService.update_stage(
            db, session, new_stage=final_stage,
            decision_type=final_decision_type, request_id=request_id,
        )

    if save_planner_message:
        await SessionService.append_message(
            db, session_id=session_id,
            role=MessageRole.PLANNER.value,
            content=planner_reply,
            message_type=MessageType.SYNTHESIS_REQUEST.value,
            stage=final_stage,
            source=response_source,
            request_id=request_id,
            metadata={"artifactType": "direction", "artifactContent": artifact_content},
        )

    await log_ai_turn(
        db, request_id, session_id, stage,
        EventType.SYNTHESIS_REQUEST.value,
        response_source,
        prompt_family="direction_embedding",
        validation_status="accepted",
    )

    return response_dict(
        request_id, session_id, response_source, planner_reply, memory,
        memory_patch=patch,
        updated_version=updated_version,
        stage_decision={"type": final_decision_type, "stage": final_stage},
        suggestions=[],
        artifact_content=artifact_content,
    )
