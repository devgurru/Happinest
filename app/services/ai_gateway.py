"""
AI Gateway — Ollama adapter for Gemma3.
Calls Ollama, parses structured JSON response, handles timeouts and one safe retry.
No fallback responses — returns explicit error on failure.
"""
import json
import re
import time
from typing import Any

import httpx

from app.config import settings


class AIGatewayError(Exception):
    def __init__(self, code: str, message: str, http_status: int | None = None):
        self.code = code
        self.message = message
        self.http_status = http_status
        super().__init__(message)


def _extract_json(text: str) -> dict:
    """
    Extract the first valid JSON object from LLM text output.
    Handles:
    - Gemma3 <think>...</think> reasoning blocks
    - Markdown ```json ... ``` code fences
    - Trailing text after the JSON object
    """
    # Strip Gemma3 / Qwen thinking blocks
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)

    # Strip markdown code fences
    text = re.sub(r"```(?:json)?", "", text)
    text = text.strip().strip("`").strip()

    # Strip single-line JS-style comments inside the JSON (// ...)
    # so the brace-walker can find a parseable block
    text = re.sub(r"//[^\n]*", "", text)

    # Find first { ... } block
    brace_start = text.find("{")
    if brace_start == -1:
        raise ValueError(f"No JSON object found in response. Raw (first 300): {text[:300]}")

    # Walk to find matching closing brace
    in_string = False
    escape_next = False
    depth = 0
    for i, ch in enumerate(text[brace_start:], brace_start):
        if escape_next:
            escape_next = False
            continue
        if ch == "\\" and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                candidate = text[brace_start : i + 1]
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError as e:
                    raise ValueError(f"JSON parse failed after extraction: {e}. Candidate (first 300): {candidate[:300]}")

    raise ValueError(f"No complete JSON object found. Raw (first 500): {text[:500]}")


async def call_llm(
    messages: list[dict],
    stage: str,
    event_type: str,
) -> tuple[dict, dict]:
    """
    Call Ollama with the given messages.
    Returns (parsed_result, telemetry).
    Raises AIGatewayError on unrecoverable failure.
    """
    url = f"{settings.OLLAMA_BASE_URL}/api/chat"
    payload = {
        "model": settings.OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
        "options": {
            "temperature": 0.3,
            "top_p": 0.9,
            "num_predict": 2500,  # enough for full JSON response with memory patch
            "stop": [],
        },
    }

    telemetry: dict[str, Any] = {
        "model": settings.OLLAMA_MODEL,
        "http_status": None,
        "latency_ms": None,
        "input_tokens": None,
        "output_tokens": None,
        "provider_response_id": None,
    }

    last_error: Exception | None = None
    for attempt in range(1 + settings.OLLAMA_MAX_RETRIES):
        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=settings.OLLAMA_TIMEOUT_SECONDS) as client:
                resp = await client.post(url, json=payload)
            latency_ms = int((time.monotonic() - t0) * 1000)
            telemetry["latency_ms"] = latency_ms
            telemetry["http_status"] = resp.status_code

            if resp.status_code != 200:
                last_error = AIGatewayError(
                    code="OLLAMA_HTTP_ERROR",
                    message=f"Ollama returned HTTP {resp.status_code}",
                    http_status=resp.status_code,
                )
                continue

            data = resp.json()
            raw_text: str = data.get("message", {}).get("content", "")

            # When assistant prefill is used, the model returns the
            # continuation without the leading '{'. Prepend it.
            if raw_text and not raw_text.lstrip().startswith("{"):
                raw_text = "{" + raw_text

            # Capture token usage if available
            telemetry["input_tokens"] = data.get("prompt_eval_count")
            telemetry["output_tokens"] = data.get("eval_count")

            parsed = _extract_json(raw_text)
            return parsed, telemetry

        except (httpx.TimeoutException, httpx.ConnectError) as e:
            latency_ms = int((time.monotonic() - t0) * 1000)
            telemetry["latency_ms"] = latency_ms
            last_error = AIGatewayError(
                code="OLLAMA_TIMEOUT" if isinstance(e, httpx.TimeoutException) else "OLLAMA_CONNECT_ERROR",
                message=str(e),
            )
            if attempt < settings.OLLAMA_MAX_RETRIES:
                continue

        except (json.JSONDecodeError, ValueError) as e:
            # Log what we actually received to help debug
            print(f"[AI_GATEWAY] JSON parse failed on attempt {attempt+1}. Error: {e}")
            last_error = AIGatewayError(
                code="OLLAMA_JSON_PARSE_ERROR",
                message=f"Failed to parse JSON from LLM response: {e}",
            )
            # Retry once — truncation on first call can cause parse failures
            if attempt < settings.OLLAMA_MAX_RETRIES:
                continue
            break

    raise last_error or AIGatewayError("UNKNOWN", "Unknown AI gateway error")
