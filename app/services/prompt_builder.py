"""
Prompt Builder — assembles stage-specific prompt messages for Ollama.
Loads templates from app/prompts/*.txt and injects runtime context.
Uses string.Template ($var) instead of str.format() to avoid conflicts
with JSON braces inside prompt examples.
"""
import json
import string
from pathlib import Path

from app.domain.chip_pools import format_chip_pool_for_prompt
from app.domain.stage_rules import get_stage_rules
from app.services.stage_policy import StagePolicy

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"

# Memory sections relevant per stage — reduces prompt token count
_STAGE_MEMORY_KEYS: dict[str, list[str]] = {
    "s2_basics":     ["identity", "occasion"],
    "s3_personality":["identity", "occasion", "personality"],
    "s4_vibe":       ["identity", "occasion", "personality", "vibe"],
    "s5_brief":      ["identity", "occasion", "personality", "vibe"],
    "s6_directions": ["identity", "occasion", "personality", "vibe", "brief"],
    "s7_events":     ["identity", "occasion", "personality", "vibe", "logistics"],
    "s8_guests":     ["identity", "occasion", "logistics"],
    "s9_budget":     ["identity", "occasion", "logistics"],
    "s10_vendors":   ["identity", "occasion", "logistics", "vendors"],
    "s11_summary":   ["identity", "occasion", "personality", "vibe", "logistics", "vendors"],
}


def _slim_memory(memory: dict, stage: str) -> dict:
    """Return only the memory sections needed for the current stage."""
    keys = _STAGE_MEMORY_KEYS.get(stage, list(memory.keys()))
    return {k: memory[k] for k in keys if k in memory}


def _load_template(name: str) -> string.Template:
    path = PROMPTS_DIR / f"{name}.txt"
    if not path.exists():
        raise FileNotFoundError(f"Prompt template not found: {path}")
    return string.Template(path.read_text(encoding="utf-8"))


def build_conversation_turn_prompt(
    stage: str,
    memory: dict,
    recent_messages: list[dict],
    user_message: str,
) -> list[dict]:
    """Build messages list for a standard conversation_turn AI call."""
    template = _load_template("conversation_turn")
    stage_ctx = StagePolicy.get_stage_prompt_context(stage)
    chip_pool_str = format_chip_pool_for_prompt(stage)

    # Include recent turns for all stages (S2 needs this to avoid repeating clarification)
    history_limit = 8 if stage == "s2_basics" else 12
    history_lines = []
    for msg in recent_messages[-history_limit:]:
        role_label = "Client" if msg["role"] in ("client", "user") else "Planner"
        content = (msg.get("content") or "").strip()
        if content:
            history_lines.append(f"{role_label}: {content}")

    last_planner = "(none yet — first reply on this stage)"
    for msg in reversed(recent_messages):
        if msg.get("role") in ("planner", "assistant"):
            text = (msg.get("content") or "").strip()
            if text:
                last_planner = text
                break

    identity = memory.get("identity") or {}
    client = (identity.get("clientName") or "").strip()
    partner = (identity.get("partnerName") or "").strip()
    if client and partner:
        client_names = f"{client} & {partner}"
    else:
        client_names = client or partner or "the couple"

    system_content = template.safe_substitute(
        stage=stage,
        stage_goal=stage_ctx.get("goal", "Gather information for this stage."),
        memory_patch_hint=stage_ctx.get("memoryPatchHint", "Patch only fields relevant to this stage."),
        advance_condition=stage_ctx.get("advanceCondition", "When enough information is captured."),
        stage_rules=get_stage_rules(stage),
        memory_json=json.dumps(_slim_memory(memory, stage), indent=2),
        chip_pool=chip_pool_str or "None defined for this stage",
        client_names=client_names,
        last_planner_reply=last_planner,
        history="\n".join(history_lines) if history_lines else "(first message on this stage)",
    )

    # Gemma3 doesn't reliably follow system-only JSON instructions.
    # Prepend a compact JSON reminder directly in the user message.
    json_reminder = (
        "[IMPORTANT: Respond ONLY with a single valid JSON object. "
        "No prose, no markdown, no explanations outside the JSON.]\n\n"
    )

    return [
        {"role": "system", "content": system_content},
        {"role": "user", "content": json_reminder + user_message},
        {"role": "assistant", "content": "{"},  # Prefill forces model to continue in JSON
    ]


def _with_json_prefill(content: str) -> list[dict]:
    """Gemma3 responds more reliably when assistant message prefills JSON."""
    reminder = (
        "[IMPORTANT: Respond ONLY with a single valid JSON object. "
        "No prose, no markdown, no explanations outside the JSON.]\n\n"
    )
    return [
        {"role": "user", "content": reminder + content},
        {"role": "assistant", "content": "{"},
    ]


def build_brief_synthesis_prompt(memory: dict, version_no: int) -> list[dict]:
    """Build messages for S5 brief synthesis."""
    template = _load_template("brief_synthesis")
    content = template.safe_substitute(
        memory_json=json.dumps(memory, indent=2),
        version_no=version_no,
    )
    return _with_json_prefill(content)


def build_direction_synthesis_prompt(
    memory: dict,
    brief_text: str,
    candidate_sites: list[dict],
    version_no: int,
) -> list[dict]:
    """Build messages for S6 direction synthesis."""
    template = _load_template("direction_synthesis")

    candidates_text = ""
    for i, site in enumerate(candidate_sites, 1):
        p = site.get("profile_json", {})
        style_tags = ", ".join(p.get("styleTags", []))
        vibe_tags = ", ".join(p.get("vibeTags", []))
        cultural = ", ".join(p.get("culturalSignals", []))
        candidates_text += (
            f"\n{i}. **{site['name']}** (slug: {site['slug']})\n"
            f"   Type: {site.get('site_type', '')}\n"
            f"   Description: {site.get('short_description', '')}\n"
            f"   Style: {style_tags}\n"
            f"   Vibe: {vibe_tags}\n"
            f"   Cultural signals: {cultural}\n"
        )

    content = template.safe_substitute(
        memory_json=json.dumps(memory, indent=2),
        brief_text=brief_text,
        candidate_sites=candidates_text.strip(),
        version_no=version_no,
    )
    return _with_json_prefill(content)


def build_final_summary_prompt(memory: dict, version_no: int) -> list[dict]:
    """Build messages for S11 final summary."""
    template = _load_template("final_summary")
    content = template.safe_substitute(
        memory_json=json.dumps(memory, indent=2),
        version_no=version_no,
    )
    return _with_json_prefill(content)

