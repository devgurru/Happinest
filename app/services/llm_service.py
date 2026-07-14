import json
import re
from pathlib import Path

import httpx

from app.config import settings

SYSTEM_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "system_prompt.txt"
EXTRACTION_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "extraction_prompt.txt"


def _load_prompt(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _build_chat_messages(
    system_prompt: str,
    history: list[dict],
    user_message: str,
) -> list[dict]:
    """Assemble Ollama-compatible message list."""
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_message})
    return messages


async def get_ai_response(
    user_message: str,
    conversation_history: list[dict],
    wedding_profile: dict,
) -> str:
    """
    Send the conversation to Ollama and return the assistant's reply.
    """
    raw_system_prompt = _load_prompt(SYSTEM_PROMPT_PATH)
    system_prompt = raw_system_prompt.replace(
        "{wedding_profile}", json.dumps(wedding_profile, indent=2, ensure_ascii=False)
    )

    messages = _build_chat_messages(system_prompt, conversation_history, user_message)

    payload = {
        "model": settings.OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
        "options": {
            "temperature": 0.7,
            "top_p": 0.9,
            "num_predict": 512,
        },
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(
            f"{settings.OLLAMA_BASE_URL}/api/chat",
            json=payload,
        )
        response.raise_for_status()
        data = response.json()

    return data["message"]["content"].strip()


async def extract_profile_updates(
    user_message: str,
    assistant_response: str,
) -> dict:
    """
    Ask the LLM to extract structured profile fields from the latest exchange.
    Returns a (possibly empty) dict of extracted fields.
    """
    raw_extraction_prompt = _load_prompt(EXTRACTION_PROMPT_PATH)

    conversation_snippet = (
        f"User: {user_message}\nAssistant: {assistant_response}"
    )
    extraction_prompt = raw_extraction_prompt.replace("{conversation}", conversation_snippet)

    payload = {
        "model": settings.OLLAMA_MODEL,
        "messages": [{"role": "user", "content": extraction_prompt}],
        "stream": False,
        "options": {
            "temperature": 0.0,   # deterministic for extraction
            "num_predict": 512,
        },
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(
            f"{settings.OLLAMA_BASE_URL}/api/chat",
            json=payload,
        )
        response.raise_for_status()
        data = response.json()

    raw_text = data["message"]["content"].strip()

    # Strip markdown code fences if the model wraps JSON in ```
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw_text, flags=re.MULTILINE)
    cleaned = re.sub(r"\s*```$", "", cleaned, flags=re.MULTILINE)

    try:
        extracted = json.loads(cleaned)
        if not isinstance(extracted, dict):
            return {}
        return extracted
    except json.JSONDecodeError:
        # If parsing fails, return empty — don't crash the chat flow
        return {}
