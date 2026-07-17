"""
Prompt Builder — assembles stage-specific prompt messages for the LLM.
"""
import json
import string
from pathlib import Path

from app.domain.chip_pools import format_chip_pool_for_prompt
from app.services.stage_policy import StagePolicy

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
    client = (identity.get("groomName") or "").strip()
    partner = (identity.get("brideName") or "").strip()
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


def _early_signals_reminder(stage: str, memory: dict) -> str:
    """
    Build a direct reminder injected just before the user message.
    LLMs attend to end-of-context far more reliably than system prompt instructions.
    Only fires when early signals exist and the canonical field is still empty.
    """
    early = memory.get("earlySignals") or {}

    if stage == "s3_personality":
        tags = (memory.get("personality") or {}).get("tags") or []
        ep = early.get("personality") or []
        if ep and not tags:
            return (
                f"[REMINDER: earlySignals.personality = {ep} — "
                f"canonical personality.tags is EMPTY. "
                f"Your plannerReply MUST say: \"You mentioned you're {', '.join(ep)} earlier — "
                f"does that capture you two, or want to add more?\" "
                f"memoryPatch = {{}} (empty). stageDecision = stay.]"
            )

    if stage == "s4_vibe":
        from app.domain.memory_schema import resolve_primary_vibe
        ev = early.get("vibe") or []
        if ev and not resolve_primary_vibe(memory):
            return (
                f"[REMINDER: earlySignals.vibe = {ev} — "
                f"canonical vibe.primaryVibe is EMPTY. "
                f"Your plannerReply MUST say: \"You mentioned {', '.join(ev[:2])} earlier — "
                f"keep that or want something else?\" "
                f"memoryPatch = {{}} (empty). stageDecision = stay.]"
            )

    if stage == "s7_events":
        events = (memory.get("logistics") or {}).get("events") or []
        ee = early.get("events") or []
        if ee and not events:
            return (
                f"[REMINDER: earlySignals.events = {ee} — "
                f"canonical logistics.events is EMPTY. "
                f"Your plannerReply MUST say: \"You mentioned {', '.join(ee)} earlier — "
                f"are those the events you want, or want to change anything?\" "
                f"memoryPatch = {{}} (empty). stageDecision = stay.]"
            )

    if stage == "s9_budget":
        budget = (memory.get("logistics") or {}).get("budget") or {}
        eb = early.get("budget") or {}
        if eb and not budget.get("range"):
            rng = eb.get("range") or str(eb)
            return (
                f"[REMINDER: earlySignals.budget = {eb} — "
                f"canonical logistics.budget is EMPTY. "
                f"Your plannerReply MUST say: \"You mentioned a budget of {rng} earlier — "
                f"does that still work, or want to adjust?\" "
                f"memoryPatch = {{}} (empty). stageDecision = stay.]"
            )

    return ""


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
    intent: dict | None = None,
) -> list[dict]:
    """Call 2 — planner reply + memoryPatch using current + intent-driven stage rules."""
    template = _load_template("conversation_turn")
    stage_ctx = StagePolicy.get_stage_prompt_context(stage)
    chip_pool_str = format_chip_pool_for_prompt(stage)
    history, last_planner = _history_and_last_reply(
        recent_messages, limit=8 if stage == "s2_basics" else 12
    )

    intent = intent or {}
    target_sections = [
        s for s in (intent.get("targetSections") or []) if isinstance(s, str)
    ]
    stage_rules = StagePolicy.get_stage_rules_for_intent(
        stage,
        target_sections,
        intent_type=str(intent.get("intentType") or "normal"),
        intent_summary=str(intent.get("summary") or ""),
    )

    system_content = template.safe_substitute(
        stage=stage,
        stage_goal=stage_ctx.get("goal", "Gather information for this stage."),
        memory_patch_hint=stage_ctx.get("memoryPatchHint", "Patch only fields for this stage."),
        advance_condition=stage_ctx.get("advanceCondition", "When enough information is captured."),
        stage_gaps=StagePolicy.get_stage_gap_guide(stage, memory, user_message),
        stage_rules=stage_rules,
        intent_summary=str(intent.get("summary") or "Normal answer for current stage"),
        intent_type=str(intent.get("intentType") or "normal"),
        decision_hint=str(intent.get("decisionHint") or "stay"),
        target_sections=", ".join(target_sections) or "(current stage)",
        memory_json=json.dumps(_slim_memory(memory, stage), indent=2),
        chip_pool=chip_pool_str or "None for this stage",
        client_names=_client_names(memory),
        last_planner_reply=last_planner,
        history=history,
    )

    json_reminder = "[Respond ONLY with one valid JSON object. No markdown.]\n\n"
    early_reminder = _early_signals_reminder(stage, memory)
    user_content = f"{early_reminder}\n\n{json_reminder}{user_message}".strip() if early_reminder else f"{json_reminder}{user_message}"

    return [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_content},
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


def build_direction_synthesis_prompt(
    memory: dict,
    brief_text: str,
    candidate_sites: list[dict],
    version_no: int,
) -> list[dict]:
    template = _load_template("direction_synthesis")
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
        memory_json=json.dumps(memory, indent=2),
        brief_text=brief_text,
        candidate_sites=candidates_text.strip(),
        version_no=version_no,
    ))


def build_final_summary_prompt(memory: dict, version_no: int) -> list[dict]:
    template = _load_template("final_summary")
    return _with_json_prefill(template.safe_substitute(
        memory_json=json.dumps(memory, indent=2),
        version_no=version_no,
    ))
