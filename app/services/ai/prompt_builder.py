"""
Prompt Builder — assembles LLM messages for each pipeline call.

Call 1: build_data_extraction_prompt  → data_extraction.txt
Call 2: build_response_planner_prompt → conversation_planner.txt

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
from typing import TYPE_CHECKING

from app.domain.chip_pools import format_chip_pool_for_prompt
from app.services.policy.stage_policy import STAGE_CONFIG

if TYPE_CHECKING:
    from app.services.policy.context_builder import TurnContext

PROMPTS_DIR = Path(__file__).parent.parent.parent / "prompts"

# Stage → memory keys relevant to show in slim memory view
_STAGE_MEMORY_KEYS: dict[str, list[str]] = {
    "s2_basics": ["identity", "occasion", "earlySignals"],
    "s3_personality": ["identity", "occasion", "personality", "earlySignals"],
    "s4_vibe": ["identity", "occasion", "personality", "vibe", "earlySignals"],
    "s5_brief": ["identity", "occasion", "personality", "vibe"],
    "s6_directions": ["identity", "occasion", "personality", "vibe", "brief", "direction"],
    "s7_events": ["identity", "occasion", "personality", "vibe", "logistics", "earlySignals"],
    "s8_guests": ["identity", "occasion", "logistics"],
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

    is_repeat = any(
        phrase in image_context.lower()
        for phrase in ("more inspiration", "more images", "shared more", "merged", "combined")
    )
    repeat_rule = ""
    if is_repeat:
        repeat_rule = """
7. REPEAT UPLOAD — the user already shared images earlier this session.
   Acknowledge BOTH previous and new images together.
   Ask what's different or if they want to keep both sets of signals."""

    return f"""\
## IMAGE TURN (images were uploaded this turn)
The couple shared inspiration images. Your plannerReply MUST:
0. NEVER say "I didn't receive an image" — an image WAS received and analyzed.
1. Open with a SPECIFIC acknowledgement — name what you actually see (colors, venue style).
   GOOD: "That opulent red-and-gold indoor stage with the floral archway is stunning!"
   BAD: "I see you've shared some images!"
2. Weave the stage question naturally from what you saw.
3. Keep image acknowledgement to 1-2 sentences, then ask ONE clear stage question.{repeat_rule}

Vision model's image summary (use as inspiration): "{image_context}"
""".strip()


def _already_selected_chips_str(memory: dict) -> str:
    """
    Build a comma-separated string of chips already selected/confirmed by the couple.
    AI uses this list to avoid repeating suggestions.
    """
    committed = memory.get("committedSelections") or {}
    selected: list[str] = []
    for key in ("personality", "vibe", "events"):
        selected.extend(committed.get(key) or [])

    # Also include canonical memory fields for current stage
    personality = memory.get("personality") or {}
    for tag in (personality.get("tags") or []):
        if tag and tag not in selected:
            selected.append(tag)

    vibe = memory.get("vibe") or {}
    pv = vibe.get("primaryVibe") or ""
    if pv and pv not in selected:
        selected.append(pv)
    for sv in (vibe.get("secondaryVibes") or []):
        if sv and sv not in selected:
            selected.append(sv)

    events = (memory.get("logistics") or {}).get("events") or []
    for ev in events:
        if ev and ev not in selected:
            selected.append(ev)

    return ", ".join(selected) if selected else "None yet"


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
    """
    AI Call 1 — extract and validate stage data from user message.
    Uses data_extraction.txt template with per-stage extraction rules from STAGE_CONFIG.
    """
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
    ctx: "TurnContext",
    user_message: str,
    *,
    image_context: str = "",
) -> list[dict]:
    """
    AI Call 2 — write the planner reply and generate suggestion chips.

    The TurnContext provides all behavioural decisions so the LLM only needs to:
    1. Write a warm, natural plannerReply
    2. Generate contextual suggestion chips
    3. Copy the provided memoryPatch and stageDecision exactly
    """
    from datetime import date as _date

    template = _load_template("conversation_planner")
    chip_pool_str = format_chip_pool_for_prompt(stage)
    history, _last = _history_and_last_reply(recent_messages, limit=8)

    # Missing fields — plain string, only when staying
    if ctx.stage_status == "past_date_rejected":
        missing_fields_str = (
            f"Date mentioned ('{ctx.rejected_date}') is in the past (today is {_date.today().strftime('%B %Y')}). "
            f"Tell them it has passed and ask for a future month/year or season instead."
        )
    elif ctx.missing_fields and ctx.stage_status in ("incomplete", "reanchor"):
        missing_fields_str = ", ".join(ctx.missing_fields)
    else:
        missing_fields_str = "(nothing — stage is complete or a meta turn)"

    # Early signal line — raw context for the AI to reason from naturally
    early_signal_line = ctx.early_signal_summary or "(none)"

    # Stage decision JSON — authoritative, passed as-is
    stage_decision_json = json.dumps(ctx.stage_decision)

    # Confirmed patch JSON — what was just extracted and committed
    confirmed_patch_json = json.dumps(ctx.confirmed_data, indent=2) if ctx.confirmed_data else "{}"

    system_content = template.safe_substitute(
        stage=stage,
        current_date=_date.today().strftime("%B %Y"),
        client_names=_client_names(memory),
        extraction_summary=ctx.extraction_summary or "(nothing specific captured this turn — meta or clarification turn)",
        early_signal_line=early_signal_line,
        confirmed_patch_json=confirmed_patch_json,
        missing_fields=missing_fields_str,
        memory_slim=json.dumps(_slim_memory(memory, stage), indent=2),
        history=history,
        stage_decision_json=stage_decision_json,
        chip_pool_reference=chip_pool_str or "None for this stage",
        already_selected_chips=_already_selected_chips_str(memory),
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
    # Clear stale brief text so LLM generates a 100% fresh brief from current canonical fields
    prompt_memory["brief"] = {
        "text": "",
        "quote": "",
        "status": "in_progress",
        "version": version_no,
        "generatedFromMemoryVersion": version_no,
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
        "status": "in_progress",
        "options": [],
        "version": version_no,
        "selectedDirectionId": "",
        "generatedFromMemoryVersion": version_no,
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
        "text": "",
        "status": "in_progress",
        "version": version_no,
        "generatedFromMemoryVersion": version_no,
    }
    return _with_json_prefill(template.safe_substitute(
        memory_json=json.dumps(prompt_memory, indent=2),
        version_no=version_no,
    ))

