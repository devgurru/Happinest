"""
AI Gateway — multi-provider chat adapter.

Providers (switch via LLM_PROVIDER in .env):
  - ollama → local Ollama / Gemma chat
  - grok   → xAI Grok (api.x.ai) — keys usually look like xai-...
  - groq   → Groq cloud (api.groq.com) — keys usually look like gsk_...

Callers always use call_llm(...). Provider-specific details stay inside this module.
No fallback fabricated replies — raises AIGatewayError on failure.
"""
from __future__ import annotations

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
    Handles thinking blocks, markdown fences, and trailing prose.
    """
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    text = re.sub(r"```(?:json)?", "", text)
    text = text.strip().strip("`").strip()
    text = re.sub(r"//[^\n]*", "", text)

    brace_start = text.find("{")
    if brace_start == -1:
        raise ValueError(f"No JSON object found in response. Raw (first 300): {text[:300]}")

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
                    raise ValueError(
                        f"JSON parse failed after extraction: {e}. "
                        f"Candidate (first 300): {candidate[:300]}"
                    )

    raise ValueError(f"No complete JSON object found. Raw (first 500): {text[:500]}")


def _normalize_raw_text(raw_text: str) -> str:
    """Fix common assistant-prefill / fence quirks before JSON extract."""
    if raw_text and not raw_text.lstrip().startswith("{"):
        stripped = raw_text.lstrip()
        if stripped.startswith("```"):
            return raw_text
        return "{" + raw_text
    return raw_text


def _base_telemetry(model: str, provider: str) -> dict[str, Any]:
    return {
        "provider": provider,
        "model": model,
        "http_status": None,
        "latency_ms": None,
        "input_tokens": None,
        "output_tokens": None,
        "provider_response_id": None,
    }


def _openai_compatible_error_message(resp: httpx.Response, label: str) -> str:
    body = (resp.text or "")[:400]
    try:
        data = resp.json()
        err = data.get("error")
        if isinstance(err, dict):
            detail = err.get("message") or err.get("error") or str(err)
        elif isinstance(err, str):
            detail = err
        else:
            detail = data.get("message") or body
        return f"{label} HTTP {resp.status_code}: {detail}"
    except Exception:
        return f"{label} HTTP {resp.status_code}: {body}"


async def _call_ollama(messages: list[dict], telemetry: dict) -> dict:
    url = f"{settings.OLLAMA_BASE_URL}/api/chat"
    payload = {
        "model": settings.OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
        "options": {
            "temperature": 0.3,
            "top_p": 0.9,
            "num_predict": 2500,
            "stop": [],
        },
    }

    async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
        resp = await client.post(url, json=payload)

    telemetry["http_status"] = resp.status_code
    if resp.status_code != 200:
        raise AIGatewayError(
            code="OLLAMA_HTTP_ERROR",
            message=f"Ollama returned HTTP {resp.status_code}: {resp.text[:300]}",
            http_status=resp.status_code,
        )

    data = resp.json()
    raw_text: str = data.get("message", {}).get("content", "")
    raw_text = _normalize_raw_text(raw_text)
    telemetry["input_tokens"] = data.get("prompt_eval_count")
    telemetry["output_tokens"] = data.get("eval_count")
    return _extract_json(raw_text)


async def _call_openai_compatible(
    *,
    label: str,
    error_prefix: str,
    api_key: str,
    base_url: str,
    model: str,
    messages: list[dict],
    telemetry: dict,
    prefer_json_object: bool = True,
) -> dict:
    if not (api_key or "").strip():
        raise AIGatewayError(
            code=f"{error_prefix}_MISSING_API_KEY",
            message=f"{error_prefix}_API_KEY is empty. Set it in .env when LLM_PROVIDER uses this provider.",
        )

    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key.strip()}",
        "Content-Type": "application/json",
    }
    base_payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.3,
        "stream": False,
    }

    async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
        if prefer_json_object:
            resp = await client.post(
                url,
                headers=headers,
                json={**base_payload, "response_format": {"type": "json_object"}},
            )
            # Some models reject response_format — retry without it
            if resp.status_code == 400:
                body_l = (resp.text or "").lower()
                if "response_format" in body_l or "json_object" in body_l:
                    resp = await client.post(url, headers=headers, json=base_payload)
        else:
            resp = await client.post(url, headers=headers, json=base_payload)

    telemetry["http_status"] = resp.status_code
    if resp.status_code != 200:
        raise AIGatewayError(
            code=f"{error_prefix}_HTTP_ERROR",
            message=_openai_compatible_error_message(resp, label),
            http_status=resp.status_code,
        )

    data = resp.json()
    choices = data.get("choices") or []
    if not choices:
        raise AIGatewayError(
            code=f"{error_prefix}_EMPTY_RESPONSE",
            message=f"{label} returned no choices in chat completion.",
        )

    raw_text = (choices[0].get("message") or {}).get("content") or ""
    raw_text = _normalize_raw_text(raw_text)
    usage = data.get("usage") or {}
    telemetry["input_tokens"] = usage.get("prompt_tokens")
    telemetry["output_tokens"] = usage.get("completion_tokens")
    telemetry["provider_response_id"] = data.get("id")
    return _extract_json(raw_text)


async def _call_grok(messages: list[dict], telemetry: dict) -> dict:
    return await _call_openai_compatible(
        label="Grok/xAI",
        error_prefix="GROK",
        api_key=settings.GROK_API_KEY,
        base_url=settings.GROK_BASE_URL,
        model=settings.GROK_MODEL,
        messages=messages,
        telemetry=telemetry,
    )


async def _call_groq(messages: list[dict], telemetry: dict) -> dict:
    return await _call_openai_compatible(
        label="Groq",
        error_prefix="GROQ",
        api_key=settings.GROQ_API_KEY,
        base_url=settings.GROQ_BASE_URL,
        model=settings.GROQ_MODEL,
        messages=messages,
        telemetry=telemetry,
    )


async def call_llm(
    messages: list[dict],
    stage: str,
    event_type: str,
) -> tuple[dict, dict]:
    """
    Call the configured LLM provider with chat messages.
    Returns (parsed_result, telemetry).
    Raises AIGatewayError on unrecoverable failure.
    """
    provider = settings.llm_provider
    model = settings.active_chat_model
    telemetry = _base_telemetry(model, provider)

    last_error: Exception | None = None
    for attempt in range(1 + settings.llm_max_retries):
        t0 = time.monotonic()
        try:
            if provider == "grok":
                parsed = await _call_grok(messages, telemetry)
            elif provider == "groq":
                parsed = await _call_groq(messages, telemetry)
            else:
                parsed = await _call_ollama(messages, telemetry)
            telemetry["latency_ms"] = int((time.monotonic() - t0) * 1000)
            return parsed, telemetry

        except AIGatewayError as e:
            telemetry["latency_ms"] = int((time.monotonic() - t0) * 1000)
            last_error = e
            print(f"[AI_GATEWAY] {e.code}: {e.message}")
            if e.code.endswith("_MISSING_API_KEY"):
                break
            if attempt < settings.llm_max_retries:
                continue

        except (httpx.TimeoutException, httpx.ConnectError) as e:
            telemetry["latency_ms"] = int((time.monotonic() - t0) * 1000)
            prefix = {"grok": "GROK", "groq": "GROQ"}.get(provider, "OLLAMA")
            last_error = AIGatewayError(
                code=f"{prefix}_TIMEOUT" if isinstance(e, httpx.TimeoutException) else f"{prefix}_CONNECT_ERROR",
                message=str(e),
            )
            if attempt < settings.llm_max_retries:
                continue

        except (json.JSONDecodeError, ValueError) as e:
            telemetry["latency_ms"] = int((time.monotonic() - t0) * 1000)
            print(f"[AI_GATEWAY] JSON parse failed on attempt {attempt + 1} ({provider}/{model}): {e}")
            prefix = {"grok": "GROK", "groq": "GROQ"}.get(provider, "OLLAMA")
            last_error = AIGatewayError(
                code=f"{prefix}_JSON_PARSE_ERROR",
                message=f"Failed to parse JSON from LLM response: {e}",
            )
            if attempt < settings.llm_max_retries:
                continue
            break

    raise last_error or AIGatewayError("UNKNOWN", "Unknown AI gateway error")
