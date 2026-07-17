"""
Prompt Builder — assembles stage-specific prompt messages for the LLM.
"""
import json
import string
from pathlib import Path

from app.domain.chip_pools import format_chip_pool_for_prompt
from app.domain.intent import TurnIntent
from app.domain.stages import (
    get_stage_gap_guide,
    get_stage_prompt_context,
    get_stage_rules_for_intent,
)

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"

_STAGE_MEMORY_KEYS: dict[str, list[str]] = {
    "s2_basics": ["identity", "occasion", "earlySignals"],
    "s3_personality": ["identity", "occasion", "personality", "earlySignals"],
    "s4_vibe": ["identity", "occasion", "personality", "vibe", "earlySignals"],
    "s5_brief": ["identity", "occasion", "personality", "vibe"],
    "s6_directions": ["identity", "occasion", "personality", "vibe", "brief", "direction"],
    "s7_events": ["identity", "occasion", "personality", "vibe", "logistics"],
    "s8_guests": ["identity", "occasion", "logistics"],
    "s9_budget": ["identity", "occasion", "logistics"],
    "s10_vendors": ["identity", "occasion", "logistics"],
    "s11_summary": ["identity", "occasion", "personality", "vibe", "logistics"],
}


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
    client = (identity.get("clientName") or "").strip()
    partner = (identity.get("partnerName") or "").strip()
    if client and partner:
        return f"{client} & {partner}"
    return client or partner or "the couple"


def _history_and_last_reply(recent_messages: list[dict], *, limit: int) -> tuple[str, str]:
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


def build_turn_intent_prompt(
    stage: str,
    memory: dict,
    user_message: str,
) -> list[dict]:
    """Call 1 — classify intent / target sections (no planner copy)."""
    template = _load_template("turn_intent")
    content = template.safe_substitute(
        stage=stage,
        client_names=_client_names(memory),
        memory_json=json.dumps(_slim_memory(memory, stage), indent=2),
        user_message=user_message,
    )
    return [
        {"role": "user", "content": content},
        {"role": "assistant", "content": "{"},
    ]


def build_conversation_turn_prompt(
    stage: str,
    memory: dict,
    recent_messages: list[dict],
    user_message: str,
    *,
    intent: TurnIntent | None = None,
) -> list[dict]:
    """Call 2 — planner reply + memoryPatch using current + intent-driven stage rules."""
    template = _load_template("conversation_turn")
    stage_ctx = get_stage_prompt_context(stage)
    chip_pool_str = format_chip_pool_for_prompt(stage)
    history, last_planner = _history_and_last_reply(
        recent_messages, limit=8 if stage == "s2_basics" else 12
    )

    intent = intent or TurnIntent.default()
    target_sections = [s for s in intent.target_sections if isinstance(s, str)]
    stage_rules = get_stage_rules_for_intent(
        stage,
        target_sections,
        intent_type=intent.intent_type.value,
        intent_summary=intent.summary,
    )

    system_content = template.safe_substitute(
        stage=stage,
        stage_goal=stage_ctx.get("goal", "Gather information for this stage."),
        memory_patch_hint=stage_ctx.get("memoryPatchHint", "Patch only fields for this stage."),
        advance_condition=stage_ctx.get("advanceCondition", "When enough information is captured."),
        stage_gaps=get_stage_gap_guide(stage, memory, user_message),
        stage_rules=stage_rules,
        intent_summary=intent.summary or "Normal answer for current stage",
        intent_type=intent.intent_type.value,
        decision_hint=intent.decision_hint,
        target_sections=", ".join(target_sections) or "(current stage)",
        memory_json=json.dumps(_slim_memory(memory, stage), indent=2),
        chip_pool=chip_pool_str or "None for this stage",
        client_names=_client_names(memory),
        last_planner_reply=last_planner,
        history=history,
    )

    json_reminder = (
        "[Respond ONLY with one valid JSON object. No markdown.]\n\n"
    )
    return [
        {"role": "system", "content": system_content},
        {"role": "user", "content": json_reminder + user_message},
        {"role": "assistant", "content": "{"},
    ]


def _with_json_prefill(content: str) -> list[dict]:
    reminder = "[Respond ONLY with one valid JSON object. No markdown.]\n\n"
    return [
        {"role": "user", "content": reminder + content},
        {"role": "assistant", "content": "{"},
    ]


def build_brief_synthesis_prompt(memory: dict, version_no: int) -> list[dict]:
    template = _load_template("brief_synthesis")
    return _with_json_prefill(template.safe_substitute(
        memory_json=json.dumps(memory, indent=2),
        version_no=version_no,
    ))


def build_final_summary_prompt(memory: dict, version_no: int) -> list[dict]:
    template = _load_template("final_summary")
    return _with_json_prefill(template.safe_substitute(
        memory_json=json.dumps(memory, indent=2),
        version_no=version_no,
    ))
