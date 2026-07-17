"""
Stage configuration — the single source of truth for per-stage definitions.

Holds STAGE_CONFIG (goal / rules / advance condition per stage), the global
agent rules, and the prompt-facing accessors that assemble stage rules for the
conversation LLM. Pure data + string assembly; no I/O.
"""
from __future__ import annotations

from app.domain.enums import StageId


GLOBAL_AGENT_RULES = """
## AGENT MEMORY OWNERSHIP (all stages)
You decide what enters canonical memory via memoryPatch. Follow these invariants:

1. ONLY patch fields listed in this stage's MEMORY CONTRACT.
2. If the message is gibberish, random keystrokes, nonsense, or unintelligible
   (e.g. "Asdfasidfu asg", "akdjlkasdjlf"):
   - stageDecision.type = "request_clarification"
   - memoryPatch MUST be {} (empty object — NEVER copy garbage into tags or any field)
   - Warmly say you did not understand, then re-ask THIS stage's goal in fresh wording
     (vary every time — never a fixed script; never mention directions unless on s6).
2b. HELP / HOW-TO QUESTIONS (not gibberish):
   If they ask what a stage means, whether to pick chips or type their own, what
   "personality"/"vibe"/events mean, or how to answer:
   - intent is help — stageDecision.type = "stay"
   - memoryPatch = {}
   - Answer clearly and kindly; say chips are optional shortcuts AND custom text is welcome
   - End with one concrete invite to answer THIS stage
   - NEVER say they misunderstood or that there was a communication error
2c. MORE SUGGESTIONS REQUESTS:
   If they ask for more / other / different / alternative chips or ideas:
   - intent is more_suggestions — stageDecision.type = "stay"
   - memoryPatch = {}
   - Chip pools are REFERENCE only — invent 4–6 fresh short labels for THIS stage
   - Do NOT only reshuffle the same backend pool list
   - Fit ideas to memory (place, personality, culture) when possible
   - Invite them to pick one or type their own
3. If stageDecision.type is "request_clarification" OR "stay" because you did not
   understand → memoryPatch MUST be {}.
4. Never put cities, months, years, full sentences, or vibe adjectives into
   personality.tags.
5. Never put cities, months, or personality tags into vibe.primaryVibe.
6. Prefer chip-pool labels when a pool is provided; custom tags are welcome when they
   clearly describe the couple (e.g. "Foodies", "College sweethearts", "Sunset garden party").
   Chip pools are inspiration — never refuse a clear custom short answer.
   When they ask for more suggestions, invent new labels beyond the pool.
7. Propose advance only when THIS stage's advance condition is clearly met
   from committed meaning in the message and/or already-known memory.
8. When earlySignals has vibe/personality for a later stage, acknowledge it
   in plannerReply — do not silently ignore memory.
9. PAST DATE RULE (applies everywhere, especially s2_basics):
   If the user gives a date, month, or year that is already in the past
   (e.g. "March 2025", "January 2026", "2024", or a month that has already
   passed in the current year):
   - NEVER save it to memoryPatch.occasion.datePreference — leave that field
     out of memoryPatch entirely.
   - stageDecision.type = "stay" (a past wedding date cannot complete any stage).
   - plannerReply must warmly note that the date seems to have already passed
     and ask them to share a future month or year instead.
   Seasons (Winter, Summer, Monsoon, Spring) are NOT affected by this rule.

## VOICE (match your best S3/S4 energy on every stage)
- Short, warm, specific — celebrate what they share
- Name the couple when natural (from memory.identity)
- One clear question so they know exactly what to answer next
- On gibberish: gentle, human, varied — never the same sentence twice in a row
""".strip()


# Consolidated stage configuration — SINGLE SOURCE OF TRUTH per stage
# Each stage has: goal, rules, memoryPatchHint, advanceCondition, stateless flag
STAGE_CONFIG: dict[str, dict] = {
    StageId.S2_BASICS.value: {
        "goal": "Capture where and when. Warm, clear questions — vary wording on gibberish. On advance, transition to personality (who the couple is), not venue setting.",
        "rules": """
ACCEPT → memoryPatch.occasion only:
- place: real city/region (Delhi, Goa, Udaipur, …)
- datePreference: concrete month (optionally year) that is IN THE FUTURE, e.g. "December 2026"
- seasonPreference: only if they named a season (Winter/Summer/Monsoon/Spring)
- settingPreference: beach / palace / garden only when clearly said
- destinationMode: local | destination | unknown

REJECT (do not patch; stay or request_clarification):
- Vague timing alone: "cold weather", "nice weather", "not sure", "sometime"
- Past dates or years — see global rule 9 above.
- Gibberish / random text
- Personality or vibe words alone (park nothing in personality/vibe here)

EARLY HINTS: "big festive", "foodies", "north indian" may be mentioned in
plannerReply but go ONLY into earlySignals via conversation — do NOT put them
in personality.tags or vibe.primaryVibe on this stage. You may omit early
signals from memoryPatch (backend may park them); you must still NOT write
personality/vibe patches.

CRITICAL for stay vs advance:
- If message has city + future month (e.g. "Goa, December 2026") → patch BOTH place and
  datePreference, stageDecision.type = "advance", ask personality (s3) next.
- If message has city + PAST month/year (e.g. "Goa, March 2026" when today is later) →
  patch place only, do NOT put the past date in datePreference, stay, warmly explain
  that date seems to have passed, ask for a future month/year. Do NOT ask personality.
- If only place → stay, ask for month/season only.
- If only timing → stay, ask for place only.
- NEVER ask about "what you both love / relationship unique" while still on s2_basics.

GIBBERISH on s2: request_clarification + {}. Vary wording. Light humor OK once.
""".strip(),
        "memoryPatchHint": 'Valid: {"occasion": {"place": "Goa", "datePreference": "December 2026", "destinationMode": "destination"}}. If they give place + future month in one message, patch BOTH and advance. Past months/years must NOT enter datePreference — stay and ask for a future date. Gibberish → {} + request_clarification. Never personality/vibe here.',
        "advanceCondition": "place + concrete FUTURE month/season in memory; advance reply asks about the COUPLE (s3), not setting",
        "stateless": True,
    },

    StageId.S3_PERSONALITY.value: {
        "goal": "Capture who the couple is. YOU validate tags — reject gibberish; only meaningful personality/culture/relationship signals enter memoryPatch.",
        "rules": """
ACCEPT → memoryPatch.personality:
- tags: short meaningful labels from the chip pool OR clear custom phrases
  (e.g. "Foodies", "Travel lovers", "College sweethearts", "Punjabi family")
- culturalSignals / relationshipSignals / lifestyleSignals when clear
- Prefer 2+ tags before advancing

REJECT → memoryPatch MUST be {}:
- Gibberish / random keystrokes ("Asdfasidfu asg")
- Cities, months, years (Delhi, March, 2026)
- Occasion rehash ("Delhi, March 2026 — big festive…") — stay; ask about
  the couple, do not create personality tags from that paste
- Vibe-only phrases ("festive", "intimate") — note in reply, do not tag as personality
- Single meaningless word fragments

If you cannot interpret the message: request_clarification + empty memoryPatch.

HELP: If they ask what personality means or about chips vs free text — stay, empty patch,
explain briefly (who you are as a couple; chips optional; own words welcome), then invite
one answer. Do not treat that as misunderstanding.

CRITICAL: When advancing to S4, ask a CLEAR vibe question.
""".strip(),
        "memoryPatchHint": 'If valid: {"personality": {"tags": ["Foodies", "..."], "culturalSignals": [], "relationshipSignals": [], "lifestyleSignals": []}}. If gibberish or unclear: memoryPatch = {} and request_clarification.',
        "advanceCondition": "2+ meaningful personality tags (never cities/dates/gibberish)",
        "stateless": False,
    },

    StageId.S4_VIBE.value: {
        "goal": "Confirm primary vibe from chip pool. When you first land on S4 after advancing from S3, ask a CLEAR question: 'What's the vibe you're going for?' or 'What feeling/energy do you want?' Offer vibe chip suggestions. Acknowledge earlySignals.vibe from memory when present.",
        "rules": """
CRITICAL FOR FIRST TURN ON S4: When you just advanced from S3 to S4, your
plannerReply MUST end with a clear, direct question asking about the VIBE/FEELING
of the wedding. Examples:
- "Now — what's the vibe you're going for? Big and festive, intimate, traditional?"
- "Love it! What feeling do you want for the wedding? Pick a vibe or describe it."
- "Perfect! Now tell me — what energy are you imagining? Festive, elegant, laid-back?"
NEVER leave them guessing what to answer next. Always offer vibe chip suggestions.

ACCEPT → memoryPatch.vibe:
- primaryVibe: prefer a vibe chip-pool label
  (e.g. "Big & festive", "Intimate", "Traditional & rooted")
  OR a short custom vibe the user clearly commits to
  (e.g. "Sunset garden party", "Heritage glam")
- secondaryVibes, energyLevel, formality, familyRole when clear

REJECT → empty memoryPatch:
- Gibberish
- City / month as vibe ("Delhi", "March")
- Occasion rehash paste — ask vibe question; if earlySignals.vibe exists,
  ask "You mentioned Big & festive earlier — keep or change?"
- Personality chips in vibe fields

MEMORY LOOKUP: If earlySignals.vibe or vibe.primaryVibe already set, acknowledge
it in plannerReply instead of a blank "what's the vibe?"

MORE SUGGESTIONS: If they ask for more vibe options beyond the chips shown,
stay with empty memoryPatch and return fresh vibe labels in "suggestions"
(not the same pool reshuffled). Fit to their place/personality when you can.
""".strip(),
        "memoryPatchHint": 'If valid: {"vibe": {"primaryVibe": "Big & festive", ...}}. Never city/month as primaryVibe. Gibberish → {} + request_clarification.',
        "advanceCondition": "primaryVibe is a pool label; personality already filled",
        "stateless": False,
    },

    StageId.S5_BRIEF.value: {
        "goal": "Present / refine the couple brief. Synthesis owns brief text generation.",
        "rules": """
ACCEPT: light occasion/personality/vibe corrections via reanchor patches only
when the client explicitly corrects something.
Do not invent new brief fields in conversation_turn unless correcting.

If they ask for directions → that is a synthesis path (backend may divert).
Otherwise stay and confirm the brief.
""".strip(),
        "memoryPatchHint": "Reanchor patches only for corrections",
        "advanceCondition": "Brief confirmed or direction synthesis requested",
        "stateless": False,
    },

    StageId.S6_DIRECTIONS.value: {
        "goal": "User picks one design direction option.",
        "rules": """
ACCEPT → memoryPatch.direction:
- selectedDirectionId / selectedDirectionName matching a listed option

REJECT:
- Do NOT patch occasion.place with a direction name (e.g. "Delhi Rooftop")
- Do not rewrite personality/vibe from a direction pick
""".strip(),
        "memoryPatchHint": 'Patch direction: {"selectedDirectionId": "...", "selectedDirectionName": "..."}',
        "advanceCondition": "A listed direction is clearly selected → ask for wedding functions next",
        "stateless": False,
    },

    StageId.S7_EVENTS.value: {
        "goal": "Confirm which wedding functions/events they want. Ask about events — NOT colors, textures, or direction aesthetics. Offer event chips.",
        "rules": """
ACCEPT → memoryPatch.logistics.events (list of event names from pool when possible).
Set logistics.eventsConfirmed = true ONLY when they say the list is final
("that's all", "only these", "lock it in").

REJECT: personality/vibe patches; gibberish; aesthetic/color talk as events.
""".strip(),
        "memoryPatchHint": 'Patch logistics: {"events": ["Mehndi", "Sangeet", ...]}. Set eventsConfirmed only when user says the list is final.',
        "advanceCondition": "1+ events listed AND user confirmed the list is complete (e.g. 'that's all' / 'only these')",
        "stateless": False,
    },

    StageId.S8_GUESTS.value: {
        "goal": "Capture guest count for EVERY selected event. Do not ask about budget or vendors yet.",
        "rules": """
ACCEPT → memoryPatch.logistics.guestCounts: { "EventName": number }

REJECT: personality/vibe; missing counts for some events → stay and ask for missing ones.
""".strip(),
        "memoryPatchHint": 'Patch logistics: {"guestCounts": {"Mehndi": 80, "Sangeet": 250}}',
        "advanceCondition": "guestCounts filled for all events in logistics.events",
        "stateless": False,
    },

    StageId.S9_BUDGET.value: {
        "goal": "Get a comfortable total budget range in INR lakhs.",
        "rules": """
ACCEPT → memoryPatch.logistics.budget: { "range": "40-60 lakhs", "currency": "INR" }

REJECT: gibberish; vendor lists; vague "not sure" without a range → stay and clarify.
""".strip(),
        "memoryPatchHint": 'Patch logistics: {"budget": {"range": "40-60 lakhs", "currency": "INR"}}',
        "advanceCondition": "budget.range filled",
        "stateless": False,
    },

    StageId.S10_VENDORS.value: {
        "goal": "Capture vendor category priorities per event day.",
        "rules": """
ACCEPT → memoryPatch.logistics.vendorPreferences (keyed preferences).

REJECT: gibberish; rewriting earlier occasion/personality unless explicit correction.
""".strip(),
        "memoryPatchHint": 'Patch logistics: {"vendorPreferences": {"photography": "candid", "entertainment": "Sufi + Bollywood DJ"}}',
        "advanceCondition": "vendorPreferences has at least one entry",
        "stateless": False,
    },

    StageId.S11_SUMMARY.value: {
        "goal": "Confirm final summary. Synthesis owns summary text.",
        "rules": """
Conversation: minor corrections only; prefer reanchor + empty novelty.
""".strip(),
        "memoryPatchHint": "Reanchor patches only for corrections",
        "advanceCondition": "Summary confirmed",
        "stateless": False,
    },
}


_SECTION_TO_STAGE: dict[str, str] = {
    "identity": StageId.S1_NAMES.value,
    "occasion": StageId.S2_BASICS.value,
    "personality": StageId.S3_PERSONALITY.value,
    "vibe": StageId.S4_VIBE.value,
    "direction": StageId.S6_DIRECTIONS.value,
    "logistics": StageId.S7_EVENTS.value,
}


def get_stage_rules_for_intent(
    current_stage: str,
    target_sections: list[str] | None = None,
    *,
    intent_type: str = "normal",
    intent_summary: str = "",
) -> str:
    """
    Current-stage rules plus any upstream section rules when the user
    corrects earlier data from a later stage (e.g. S4 → occasion/S2).
    """
    stages = [current_stage]
    for section in target_sections or []:
        mapped = _SECTION_TO_STAGE.get(section)
        if mapped and mapped not in stages:
            stages.append(mapped)
        # logistics may need guests/budget/vendor rules when on those stages
        if section == "logistics" and current_stage in (
            StageId.S8_GUESTS.value,
            StageId.S9_BUDGET.value,
            StageId.S10_VENDORS.value,
        ):
            if current_stage not in stages:
                stages.append(current_stage)

    blocks = [GLOBAL_AGENT_RULES]
    if intent_summary or intent_type != "normal":
        blocks.append(
            f"## INTENT CONTEXT\n"
            f"intentType: {intent_type}\n"
            f"summary: {intent_summary or '(none)'}\n"
            f"targetSections: {', '.join(target_sections or []) or '(current stage only)'}\n"
            f"Honor this: gibberish → empty memoryPatch + request_clarification. "
            f"help → empty memoryPatch + stay; explain chips/process, invite an answer — "
            f"never say they misunderstood. "
            f"more_suggestions → empty memoryPatch + stay; invent fresh suggestion labels "
            f"beyond the reference chip pool. "
            f"If correction of earlier data → reanchor, patch those sections, ask current-stage question."
        )
    for sid in stages:
        config = STAGE_CONFIG.get(sid)
        if config:
            stage_block = f"""## STAGE RULES — {sid}
GOAL: {config['goal']}

{config['rules']}

ADVANCE when: {config['advanceCondition']}
""".strip()
            blocks.append(stage_block)
        else:
            blocks.append(f"## STAGE RULES — {sid}\nPatch only valid fields for this stage.")
    return "\n\n".join(blocks)


def get_stage_prompt_context(stage: str) -> dict:
    """
    Return stage-specific context hints for prompt builder.
    Uses the unified STAGE_CONFIG for consistency.
    """
    config = STAGE_CONFIG.get(stage)
    if not config:
        return {}
    
    return {
        "goal": config["goal"],
        "memoryPatchHint": config["memoryPatchHint"],
        "advanceCondition": config["advanceCondition"],
        "stateless": config.get("stateless", False),
    }
