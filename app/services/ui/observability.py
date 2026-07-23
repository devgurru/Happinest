"""Observability — logs every AI turn to ai_turn_logs table."""
import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_turn_log import AiTurnLog


async def log_ai_turn(
    db: AsyncSession,
    request_id: uuid.UUID,
    session_id: uuid.UUID,
    stage: str,
    event_type: str,
    response_source: str,
    prompt_family: str | None = None,
    model: str | None = None,
    http_status: int | None = None,
    latency_ms: int | None = None,
    provider_response_id: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    validation_status: str | None = None,
    failure_code: str | None = None,
) -> AiTurnLog:
    log = AiTurnLog(
        request_id=request_id,
        session_id=session_id,
        stage=stage,
        event_type=event_type,
        prompt_family=prompt_family,
        model=model,
        response_source=response_source,
        http_status=http_status,
        latency_ms=latency_ms,
        provider_response_id=provider_response_id,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        validation_status=validation_status,
        failure_code=failure_code,
    )
    db.add(log)
    await db.flush()
    return log
