"""
Stage rules for the conversation agent.

These are injected into the LLM prompt so the agent owns:
- what is valid input for this stage
- what may enter memoryPatch (canonical memory)
- when to stay / advance / request_clarification / reanchor

Backend applies the agent's memoryPatch as-is when the agent decided
to commit data. Clarification / gibberish turns must leave memoryPatch empty.
"""
from __future__ import annotations

from app.domain.enums import StageId

# Shared across every stage
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
3. If stageDecision.type is "request_clarification" OR "stay" because you did not
   understand → memoryPatch MUST be {}.
4. Never put cities, months, years, full sentences, or vibe adjectives into
   personality.tags.
5. Never put cities, months, or personality tags into vibe.primaryVibe.
6. Prefer chip-pool labels when a pool is provided; custom tags only when they
   clearly describe the couple (e.g. "Foodies", "College sweethearts").
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


STAGE_RULES: dict[str, str] = {
    StageId.S2_BASICS.value: """
## STAGE RULES — s2_basics (occasion)
GOAL: Capture where + when.

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

ADVANCE when: place (or clear setting) AND (concrete FUTURE month OR named season)
are both present in memory after this patch.

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

    StageId.S3_PERSONALITY.value: """
## STAGE RULES — s3_personality
GOAL: Who the couple is — real personality / culture / relationship signals.

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

ADVANCE when: at least 2 meaningful personality tags (or 1 tag + clear
relationship/lifestyle signal). Culture word alone is not enough.
""".strip(),

    StageId.S4_VIBE.value: """
## STAGE RULES — s4_vibe
GOAL: Confirm emotional direction / primary vibe.

ACCEPT → memoryPatch.vibe:
- primaryVibe: MUST be a vibe chip-pool label
  (e.g. "Big & festive", "Intimate", "Traditional & rooted")
- secondaryVibes, energyLevel, formality, familyRole when clear

REJECT → empty memoryPatch:
- Gibberish
- City / month as vibe ("Delhi", "March")
- Occasion rehash paste — ask vibe question; if earlySignals.vibe exists,
  ask "You mentioned Big & festive earlier — keep or change?"
- Personality chips in vibe fields

MEMORY LOOKUP: If earlySignals.vibe or vibe.primaryVibe already set, acknowledge
it in plannerReply instead of a blank "what's the vibe?"

ADVANCE when: primaryVibe is a valid pool label AND personality was already
filled in earlier turns (do not invent missing personality).
""".strip(),

    StageId.S5_BRIEF.value: """
## STAGE RULES — s5_brief
GOAL: Present / refine the couple brief. Synthesis owns brief text generation.

ACCEPT: light occasion/personality/vibe corrections via reanchor patches only
when the client explicitly corrects something.
Do not invent new brief fields in conversation_turn unless correcting.

If they ask for directions → that is a synthesis path (backend may divert).
Otherwise stay and confirm the brief.
""".strip(),

    StageId.S6_DIRECTIONS.value: """
## STAGE RULES — s6_directions
GOAL: User picks one design direction option.

ACCEPT → memoryPatch.direction:
- selectedDirectionId / selectedDirectionName matching a listed option

REJECT:
- Do NOT patch occasion.place with a direction name (e.g. "Delhi Rooftop")
- Do not rewrite personality/vibe from a direction pick

ADVANCE when: a listed direction is clearly selected → ask for wedding functions next.
""".strip(),

    StageId.S7_EVENTS.value: """
## STAGE RULES — s7_events
GOAL: Which functions/events they want.

ACCEPT → memoryPatch.logistics.events (list of event names from pool when possible).
Set logistics.eventsConfirmed = true ONLY when they say the list is final
("that's all", "only these", "lock it in").

REJECT: personality/vibe patches; gibberish; aesthetic/color talk as events.

ADVANCE when: ≥1 event AND eventsConfirmed.
""".strip(),

    StageId.S8_GUESTS.value: """
## STAGE RULES — s8_guests
GOAL: Guest count for EVERY event in logistics.events.

ACCEPT → memoryPatch.logistics.guestCounts: { "EventName": number }

REJECT: personality/vibe; missing counts for some events → stay and ask for missing ones.

ADVANCE when: every event has a positive integer count.
""".strip(),

    StageId.S9_BUDGET.value: """
## STAGE RULES — s9_budget
GOAL: Comfortable total budget range.

ACCEPT → memoryPatch.logistics.budget: { "range": "40-60 lakhs", "currency": "INR" }

REJECT: gibberish; vendor lists; vague "not sure" without a range → stay and clarify.

ADVANCE when: budget.range is set.
""".strip(),

    StageId.S10_VENDORS.value: """
## STAGE RULES — s10_vendors
GOAL: Vendor category priorities.

ACCEPT → memoryPatch.logistics.vendorPreferences (keyed preferences).

REJECT: gibberish; rewriting earlier occasion/personality unless explicit correction.

ADVANCE when: at least one vendor preference is committed.
""".strip(),

    StageId.S11_SUMMARY.value: """
## STAGE RULES — s11_summary
GOAL: Confirm final summary. Synthesis owns summary text.
Conversation: minor corrections only; prefer reanchor + empty novelty.
""".strip(),
}


def get_stage_rules(stage: str) -> str:
    """Full agent rule block for the current stage."""
    specific = STAGE_RULES.get(stage, "Patch only fields relevant to this stage. Reject gibberish.")
    return f"{GLOBAL_AGENT_RULES}\n\n{specific}"


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
            f"If correction of earlier data → reanchor, patch those sections, ask current-stage question."
        )
    for sid in stages:
        blocks.append(STAGE_RULES.get(sid, f"## STAGE RULES — {sid}\nPatch only valid fields for this stage."))
    return "\n\n".join(blocks)
