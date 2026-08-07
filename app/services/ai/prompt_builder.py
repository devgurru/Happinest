"""
Prompt Builder — assembles LLM messages for each pipeline call.

Call 1: build_data_extraction_prompt  → data_extraction.txt
Call 2: build_response_planner_prompt → conversation_planner.txt (simplified)

Synthesis prompts (unchanged):
  build_brief_synthesis_prompt
  build_direction_synthesis_prompt
  build_final_summary_prompt
"""
from __future__ import annotations

import copy
import json
import string
from pathlib import Path

from app.domain.chip_pools import format_chip_pool_for_prompt
from app.services.policy.stage_policy import STAGE_CONFIG

PROMPTS_DIR = Path(__file__).parent.parent.parent / "prompts"

# Stage → memory keys relevant to show in slim memory view
_STAGE_MEMORY_KEYS: dict[str, list[str]] = {
    "s2_basics": ["identity", "occasion", "earlySignals"],
    "s3_personality": ["identity", "occasion", "personality", "earlySignals"],
    "s4_vibe": ["identity", "occasion", "personality", "vibe", "earlySignals"],
    "s5_brief": ["identity", "occasion", "personality", "vibe"],
    "s6_directions": ["identity", "occasion", "personality", "vibe", "brief", "direction"],
    "s7_events": ["identity", "occasion", "personality", "vibe", "logistics", "earlySignals"],
    "s8_guests": ["identity", "occasion", "logistics", "earlySignals"],
    "s9_budget": ["identity", "occasion", "logistics", "earlySignals"],
    "s10_vendors": ["identity", "occasion", "logistics", "earlySignals"],
    "s11_summary": ["identity", "occasion", "personality", "vibe", "logistics"],
}


# ─── Internal helpers ──────────────────────────────────────────────────────────

def _slim_memory(memory: dict, stage: str) -> dict:
    keys = _STAGE_MEMORY_KEYS.get(stage, list(memory.keys()))
    return {k: memory[k] for k in keys if k in memory}


def _load_template(name: str) -> string.Template:
    path = PROMPTS_DIR / f"{name}.txt"
    if not path.exists():
        raise FileNotFoundError(f"Prompt template not found: {path}")
    return string.Template(path.read_text(encoding="utf-8"))


def _client_names(memory: dict) -> str:
    identity = memory.get("identity") or {}
    client = (identity.get("groomName") or "").strip()
    partner = (identity.get("brideName") or "").strip()
    if client and partner:
        return f"{client} & {partner}"
    return client or partner or "the couple"


def _history_and_last_reply(
    recent_messages: list[dict],
    *,
    limit: int = 8,
) -> tuple[str, str]:
    lines = []
    for msg in recent_messages[-limit:]:
        role_label = "Client" if msg.get("role") in ("client", "user") else "Planner"
        content = (msg.get("content") or "").strip()
        if content:
            lines.append(f"{role_label}: {content}")
    last_planner = "(none yet)"
    for msg in reversed(recent_messages):
        if msg.get("role") in ("planner", "assistant"):
            text = (msg.get("content") or "").strip()
            if text:
                last_planner = text
                break
    return ("\n".join(lines) if lines else "(first message)", last_planner)


def _image_turn_rules_block(image_context: str) -> str:
    """Returns image-turn behavioral rules block. Empty string when no images."""
    if not image_context:
        return ""
    return f"""\
## IMAGE TURN (images were uploaded this turn)
The couple shared inspiration images. Your plannerReply MUST:
1. Open with a SPECIFIC acknowledgement — name what you actually see.
2. Weave the stage question naturally from what you saw.
3. Keep image acknowledgement to 1-2 sentences, then ask ONE clear stage question.

Vision model's image summary: "{image_context}"
""".strip()


def _with_json_prefill(content: str) -> list[dict]:
    reminder = "[Respond ONLY with one valid JSON object. No markdown.]\n\n"
    return [
        {"role": "user", "content": reminder + content},
        {"role": "assistant", "content": "{"},
    ]


# ─── Call 1 — Data Extraction ─────────────────────────────────────────────────

def build_data_extraction_prompt(
    stage: str,
    memory: dict,
    user_message: str,
) -> list[dict]:
    """AI Call 1 — extract and validate stage data from user message."""
    from datetime import date as _date

    template = _load_template("data_extraction")
    stage_config = STAGE_CONFIG.get(stage, {})
    extraction_rules = stage_config.get("extractionRules", "Extract relevant facts for this stage.")

    content = template.safe_substitute(
        stage=stage,
        current_date=_date.today().strftime("%B %Y"),
        client_names=_client_names(memory),
        memory_slim=json.dumps(_slim_memory(memory, stage), indent=2),
        stage_extraction_rules=extraction_rules,
        user_message=user_message,
    )
    return [
        {"role": "user", "content": content},
        {"role": "assistant", "content": "{"},
    ]


# ─── Call 2 — Response Planner ────────────────────────────────────────────────

def build_response_planner_prompt(
    stage: str,
    memory: dict,
    recent_messages: list[dict],
    extraction_summary: str,
    extraction_patch: dict,
    user_message: str,
    *,
    image_context: str = "",
    budget_feasibility: str = "",
) -> list[dict]:
    """
    AI Call 2 — agent decides stageDecision + writes plannerReply.

    No more TurnContext. The agent receives memory, extraction summary,
    budget feasibility, and makes all decisions.
    """
    from datetime import date as _date

    template = _load_template("conversation_planner")
    chip_pool_str = format_chip_pool_for_prompt(stage)
    history, _last = _history_and_last_reply(recent_messages, limit=8)

    system_content = template.safe_substitute(
        stage=stage,
        current_date=_date.today().strftime("%B %Y"),
        client_names=_client_names(memory),
        extraction_summary=extraction_summary or "(nothing specific captured this turn)",
        extraction_patch_json=json.dumps(extraction_patch, indent=2) if extraction_patch else "{}",
        memory_slim=json.dumps(_slim_memory(memory, stage), indent=2),
        history=history,
        budget_feasibility=budget_feasibility or "(not applicable for this stage)",
        chip_pool_reference=chip_pool_str or "None for this stage",
        image_block=_image_turn_rules_block(image_context),
    )

    json_reminder = "[Respond ONLY with one valid JSON object. No markdown.]\n\n"
    user_content = json_reminder + (user_message or "(user sent images without text)")

    return [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_content},
        {"role": "assistant", "content": "{"},
    ]


# ─── Synthesis prompts (unchanged) ────────────────────────────────────────────

def build_brief_synthesis_prompt(memory: dict, version_no: int) -> list[dict]:
    template = _load_template("brief_synthesis")
    prompt_memory = copy.deepcopy(memory)
    prompt_memory["brief"] = {
        "text": "", "quote": "", "status": "in_progress",
        "version": version_no, "generatedFromMemoryVersion": version_no,
    }
    return _with_json_prefill(template.safe_substitute(
        memory_json=json.dumps(prompt_memory, indent=2),
        version_no=version_no,
    ))


def build_direction_synthesis_prompt(
    memory: dict,
    brief_text: str,
    candidate_sites: list[dict],
    version_no: int,
) -> list[dict]:
    template = _load_template("direction_synthesis")
    prompt_memory = copy.deepcopy(memory)
    prompt_memory["direction"] = {
        "status": "in_progress", "options": [], "version": version_no,
        "selectedDirectionId": "", "generatedFromMemoryVersion": version_no,
    }
    candidates_text = ""
    for i, site in enumerate(candidate_sites, 1):
        p = site.get("profile_json", {})
        candidates_text += (
            f"\n{i}. **{site['name']}** (slug: {site['slug']})\n"
            f"   Type: {site.get('site_type', '')}\n"
            f"   Description: {site.get('short_description', '')}\n"
            f"   Style: {', '.join(p.get('styleTags', []))}\n"
            f"   Vibe: {', '.join(p.get('vibeTags', []))}\n"
        )
    return _with_json_prefill(template.safe_substitute(
        memory_json=json.dumps(prompt_memory, indent=2),
        brief_text=brief_text,
        candidate_sites=candidates_text.strip(),
        version_no=version_no,
    ))


def build_final_summary_prompt(memory: dict, version_no: int) -> list[dict]:
    template = _load_template("final_summary")
    prompt_memory = copy.deepcopy(memory)
    prompt_memory["summary"] = {
        "text": "", "status": "in_progress",
        "version": version_no, "generatedFromMemoryVersion": version_no,
    }
    return _with_json_prefill(template.safe_substitute(
        memory_json=json.dumps(prompt_memory, indent=2),
        version_no=version_no,
    ))
