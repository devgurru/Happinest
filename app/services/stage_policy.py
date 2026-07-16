"""
Stage Policy Engine — SINGLE SOURCE OF TRUTH for all stage behavior.

This module owns:
1. Stage completion logic (backend enforcement via is_stage_complete)
2. Stage rules for LLM prompts (what the agent can/cannot do)
3. Transition validation (allowed stage movements)
4. Stage-specific context and hints

Backend controls stage advancement; AI only proposes. Policy validates and overrides.

HOW TO UPDATE STAGE BEHAVIOR:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
To modify any stage (e.g., S3_PERSONALITY or S4_VIBE):

1. UPDATE STAGE_CONFIG dict (lines ~95-300):
   - goal: What the stage is trying to accomplish
   - rules: What the LLM should accept/reject for memoryPatch
   - memoryPatchHint: Example of valid patch structure
   - advanceCondition: When the stage is ready to move forward
   - stateless: Whether this stage depends on prior memory

2. UPDATE is_stage_complete() method (lines ~450-520):
   - Backend enforcement: deterministic completion checks
   - This decides when the stage ACTUALLY advances (not just what LLM proposes)

3. UPDATE get_stage_gap_guide() method (lines ~525-625):
   - Runtime hints: tell LLM what's missing and what to ask for
   - Message-specific analysis (e.g., detecting place/date in user input)

4. For allowed transitions, update: app/domain/enums.py (ALLOWED_TRANSITIONS)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STAGE_CONFIG is the single point of truth for stage definitions!
All other methods (get_stage_rules, get_stage_prompt_context) read from it.
"""
from __future__ import annotations

from app.domain.enums import (
    ALLOWED_TRANSITIONS,
    AI_REQUIRED_STAGES,
    SYNTHESIS_STAGES,
    StageDecisionType,
    StageId,
    SynthesisType,
)


# ============================================================================
# STAGE RULES FOR LLM PROMPTS
# These are injected into the conversation agent so it knows what to accept/reject
# ============================================================================

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


# ============================================================================
# STAGE POLICY CLASS — Backend enforcement and LLM prompt generation
# ============================================================================


class StagePolicy:
    """
    Single source of truth for stage behavior.
    
    This class provides:
    - Stage completion checks (backend enforcement)
    - Stage rules for LLM prompts (what agent can accept/reject)
    - Transition validation
    - Stage-specific context and hints
    """

    @staticmethod
    def is_ai_required(stage: str) -> bool:
        try:
            return StageId(stage) in AI_REQUIRED_STAGES
        except ValueError:
            return False

    @staticmethod
    def is_synthesis_stage(stage: str) -> bool:
        try:
            return StageId(stage) in SYNTHESIS_STAGES
        except ValueError:
            return False

    @staticmethod
    def validate_transition(from_stage: str, to_stage: str, decision_type: str) -> tuple[bool, str | None]:
        """
        Returns (is_valid, error_reason).
        Backend uses this to reject AI-proposed stage jumps that violate policy.
        """
        try:
            from_s = StageId(from_stage)
            to_s = StageId(to_stage)
        except ValueError as e:
            return False, f"Unknown stage: {e}"

        allowed = ALLOWED_TRANSITIONS.get(from_s, set())
        if to_s not in allowed:
            return False, f"Transition {from_stage}→{to_stage} not allowed"

        if decision_type == StageDecisionType.STAY.value and from_s != to_s:
            return False, "STAY decision must keep same stage"

        # REANCHOR must stay on current stage
        if decision_type == StageDecisionType.REANCHOR.value:
            if from_s == to_s:
                return True, None
            return False, "REANCHOR must keep same stage"

        # REQUEST_CLARIFICATION stays on current stage
        if decision_type == StageDecisionType.REQUEST_CLARIFICATION.value:
            if from_s == to_s:
                return True, None
            return False, "REQUEST_CLARIFICATION must keep same stage"

        # JUMP may go to any earlier or same stage (corrections from later stages)
        if decision_type == StageDecisionType.JUMP.value:
            order = StageId.ordered()
            try:
                from_idx = order.index(from_s)
                to_idx = order.index(to_s)
            except ValueError:
                return False, "Unknown stage in JUMP"
            if to_idx <= from_idx:
                return True, None
            return False, f"JUMP cannot go forward from {from_stage} to {to_stage}"

        if decision_type == StageDecisionType.ADVANCE.value:
            expected_next = from_s.next_stage()
            if to_s != expected_next:
                return False, f"ADVANCE must go to {expected_next}, not {to_s}"

        return True, None

    @staticmethod
    def is_stage_complete(stage: str, memory: dict) -> bool:
        """Deterministic completion checks — backend owns stage movement."""
        try:
            stage_id = StageId(stage)
        except ValueError:
            return False

        if stage_id == StageId.S2_BASICS:
            from app.services.text_extract import get_occasion_state
            return get_occasion_state(memory)["is_complete"]

        if stage_id == StageId.S3_PERSONALITY:
            from app.services.text_extract import filter_tags
            p = memory.get("personality", {})
            tags = filter_tags(p.get("tags") or [])
            # Hard rule: culturalSignals alone (often from occasion paste) never unlock S3.
            # Need real personality tags — 2+, or 1 tag plus relationship/lifestyle (not culture-only).
            rel = len(p.get("relationshipSignals") or [])
            life = len(p.get("lifestyleSignals") or [])
            return len(tags) >= 2 or (len(tags) >= 1 and (rel + life) >= 1)

        if stage_id == StageId.S4_VIBE:
            from app.domain.memory_schema import resolve_primary_vibe
            # Cannot complete vibe (and brief) without a real personality stage fill
            if not StagePolicy.is_stage_complete(StageId.S3_PERSONALITY.value, memory):
                return False
            return bool(resolve_primary_vibe(memory))

        if stage_id == StageId.S6_DIRECTIONS:
            direction = memory.get("direction", {})
            return bool((direction.get("selectedDirectionId") or "").strip())

        if stage_id == StageId.S7_EVENTS:
            logistics = memory.get("logistics", {}) or {}
            events = logistics.get("events") or []
            if len(events) < 1:
                return False
            return bool(logistics.get("eventsConfirmed"))

        if stage_id == StageId.S8_GUESTS:
            events = memory.get("logistics", {}).get("events") or []
            counts = memory.get("logistics", {}).get("guestCounts") or {}
            if not events or not isinstance(counts, dict):
                return False
            return all(
                isinstance(counts.get(ev), int) and counts.get(ev, 0) > 0
                for ev in events
            )

        if stage_id == StageId.S9_BUDGET:
            budget = memory.get("logistics", {}).get("budget") or {}
            return bool((budget.get("range") or budget.get("amount") or "").strip())

        if stage_id == StageId.S10_VENDORS:
            prefs = memory.get("logistics", {}).get("vendorPreferences") or {}
            return isinstance(prefs, dict) and len(prefs) >= 1

        return False

    @staticmethod
    def get_stage_gap_guide(stage: str, memory: dict, user_message: str = "") -> str:
        """
        Tell the agent what is missing and what THIS message can fill,
        so stay/advance + question stay aligned.
        """
        from datetime import date
        today = date.today().isoformat()
        msg = (user_message or "").strip()

        if stage == StageId.S2_BASICS.value:
            from app.services.text_extract import (
                extract_month_or_season,
                extract_place_from_message,
                get_occasion_state,
                is_past_date,
            )
            state = get_occasion_state(memory)
            place_in_msg = extract_place_from_message(msg) if msg else None
            timing_in_msg = extract_month_or_season(msg) if msg else {}
            raw_date = (timing_in_msg.get("datePreference") or "").strip()
            season_in_msg = (timing_in_msg.get("seasonPreference") or "").strip()
            date_past = bool(raw_date and is_past_date(raw_date))

            will_have_place = state["has_place"] or bool(place_in_msg)
            will_have_time = state["has_time"] or bool(season_in_msg) or (
                bool(raw_date) and not date_past
            )

            notes = [f"Today: {today}."]
            if place_in_msg:
                notes.append(f"This message includes place → patch occasion.place=\"{place_in_msg}\".")
            if raw_date and date_past:
                notes.append(
                    f"This message includes \"{raw_date}\" but that is PAST — "
                    f"do NOT put it in datePreference. Stay and ask for a FUTURE month/year."
                )
            elif raw_date:
                notes.append(
                    f"This message includes future timing → patch occasion.datePreference=\"{raw_date}\"."
                )
            if season_in_msg:
                notes.append(f"Season named → patch seasonPreference=\"{season_in_msg}\".")

            if will_have_place and will_have_time:
                notes.append(
                    "After this patch S2 will be COMPLETE → stageDecision.type=advance, "
                    "acknowledge place+date, ask about the COUPLE (s3). Do not ask about setting."
                )
                return " ".join(notes)

            missing = []
            if not will_have_place:
                missing.append("place (city/region)")
            if not will_have_time:
                missing.append("future month+year (e.g. December 2026) or named season")
            notes.append(
                f"S2 still incomplete — need: {', '.join(missing)}. "
                f"stageDecision.type=stay. Ask ONLY for missing fields. "
                f"NEVER ask personality / relationship / vibe while on s2."
            )
            return " ".join(notes)

        if stage == StageId.S3_PERSONALITY.value:
            from app.services.text_extract import filter_tags
            tags = filter_tags((memory.get("personality") or {}).get("tags") or [])
            if StagePolicy.is_stage_complete(stage, memory):
                return "S3 COMPLETE — you may advance to s4_vibe and ask about vibe."
            return (
                f"Have tags: {tags or '(none)'}. "
                f"Need 2+ meaningful tags (or 1 + relationship/lifestyle). Stay; ask about the couple."
            )

        if stage == StageId.S4_VIBE.value:
            from app.domain.memory_schema import resolve_primary_vibe
            if resolve_primary_vibe(memory):
                return "S4 COMPLETE — you may advance (brief synthesis follows)."
            early = (memory.get("earlySignals") or {}).get("vibe") or []
            hint = f" earlySignals.vibe={early}." if early else ""
            return f"S4 INCOMPLETE — need vibe pool primaryVibe.{hint} Stay; ask vibe only."

        if StagePolicy.is_stage_complete(stage, memory):
            return f"Stage {stage} complete in memory — you may propose advance if this turn confirms it."
        return f"Stage {stage} not complete — stay and ask only for what this stage still needs."

    @staticmethod
    def events_finalize_cue(message: str) -> bool:
        """User signals the event list is complete."""
        msg_l = message.lower()
        return any(
            cue in msg_l
            for cue in (
                "that's all", "thats all", "only these", "just these", "no other",
                "no others", "that's it", "thats it", "done with events",
                "these are the events", "only want", "just want these",
            )
        )

    @staticmethod
    def resolve_final_decision(
        ai_decision_type: str,
        ai_to_stage: str,
        current_stage: str,
    ) -> tuple[str, str]:
        """
        Given AI's proposed decision, returns the backend-validated (decision_type, to_stage).
        If AI proposal is invalid, defaults to STAY on current stage.
        """
        is_valid, _ = StagePolicy.validate_transition(
            current_stage, ai_to_stage, ai_decision_type
        )
        if is_valid:
            return ai_decision_type, ai_to_stage
        return StageDecisionType.STAY.value, current_stage

    @staticmethod
    def resolve_final_decision_with_memory(
        ai_decision_type: str,
        ai_to_stage: str,
        current_stage: str,
        memory: dict,
        *,
        open_questions: list | None = None,
    ) -> tuple[str, str, str | None]:
        """
        Backend-owned final stage decision after memory patch is applied.
        Returns (decision_type, to_stage, reason_code).
        """
        is_valid, _ = StagePolicy.validate_transition(
            current_stage, ai_to_stage, ai_decision_type
        )

        # Explicit jump (correction to earlier stage)
        if ai_decision_type == StageDecisionType.JUMP.value:
            jump_ok, _ = StagePolicy.validate_transition(
                current_stage, ai_to_stage, StageDecisionType.JUMP.value
            )
            if jump_ok:
                return ai_decision_type, ai_to_stage, "jump_correction"

        # Re-anchor stays on current stage but reframes
        if ai_decision_type == StageDecisionType.REANCHOR.value:
            return StageDecisionType.REANCHOR.value, current_stage, "reanchor"

        # Clarification: always honor — never auto-advance past agent rejection
        if ai_decision_type == StageDecisionType.REQUEST_CLARIFICATION.value:
            return (
                StageDecisionType.REQUEST_CLARIFICATION.value,
                current_stage,
                "need_clarification",
            )

        # Do not advance while the model still has open questions for this stage
        if open_questions and ai_decision_type == StageDecisionType.ADVANCE.value:
            return StageDecisionType.STAY.value, current_stage, "open_questions_block_advance"

        # Memory-complete stages advance automatically (backend owns movement)
        # S5 brief is advanced via auto-synthesis after S4, not conversation_turn
        if current_stage == StageId.S5_BRIEF.value:
            if is_valid and ai_decision_type == StageDecisionType.STAY.value:
                return ai_decision_type, ai_to_stage, "ai_stay"
            return StageDecisionType.STAY.value, current_stage, "awaiting_brief_synthesis"

        if StagePolicy.is_stage_complete(current_stage, memory):
            try:
                next_stage = StageId(current_stage).next_stage()
            except ValueError:
                next_stage = None
            if next_stage:
                ok, _ = StagePolicy.validate_transition(
                    current_stage,
                    next_stage.value,
                    StageDecisionType.ADVANCE.value,
                )
                if ok:
                    return (
                        StageDecisionType.ADVANCE.value,
                        next_stage.value,
                        "memory_complete",
                    )

        # Never honor model "advance" when this stage has a completeness check and fails it.
        # (Prevents S2 skipping on vague timing / early personality signals.)
        _gated = {
            StageId.S2_BASICS.value,
            StageId.S3_PERSONALITY.value,
            StageId.S4_VIBE.value,
            StageId.S6_DIRECTIONS.value,
            StageId.S7_EVENTS.value,
            StageId.S8_GUESTS.value,
            StageId.S9_BUDGET.value,
            StageId.S10_VENDORS.value,
        }
        if (
            current_stage in _gated
            and not StagePolicy.is_stage_complete(current_stage, memory)
            and ai_decision_type == StageDecisionType.ADVANCE.value
        ):
            return StageDecisionType.STAY.value, current_stage, "memory_incomplete_block_advance"

        # Honor a valid AI advance only for stages without tight completeness gates
        if is_valid and ai_decision_type == StageDecisionType.ADVANCE.value:
            return ai_decision_type, ai_to_stage, "ai_advance"

        if is_valid and ai_decision_type == StageDecisionType.STAY.value:
            return ai_decision_type, ai_to_stage, "ai_stay"

        return StageDecisionType.STAY.value, current_stage, "continue_gathering"

    @staticmethod
    def infer_synthesis_type(stage: str, memory: dict | None = None) -> str | None:
        """Map synthesis stages to synthesis type when client omits it."""
        from app.domain.memory_schema import resolve_primary_vibe

        memory = memory or {}

        # S4 complete → client may trigger brief synthesis explicitly
        if stage == StageId.S4_VIBE.value:
            if resolve_primary_vibe(memory):
                return SynthesisType.BRIEF.value
            return None

        if stage == StageId.S5_BRIEF.value and memory:
            brief = memory.get("brief", {})
            stale = memory.get("staleSections", [])
            if brief.get("status") != "ready" or "brief" in stale:
                return SynthesisType.BRIEF.value
            return SynthesisType.DIRECTION.value

        mapping = {
            StageId.S5_BRIEF.value: SynthesisType.BRIEF.value,
            StageId.S6_DIRECTIONS.value: SynthesisType.DIRECTION.value,
            StageId.S11_SUMMARY.value: SynthesisType.SUMMARY.value,
        }
        return mapping.get(stage)

    @staticmethod
    def get_stage_rules(stage: str) -> str:
        """
        Get full agent rule block for the current stage.
        This is injected into the LLM prompt.
        """
        config = STAGE_CONFIG.get(stage)
        if not config:
            return f"{GLOBAL_AGENT_RULES}\n\nPatch only fields relevant to this stage. Reject gibberish."
        
        stage_block = f"""## STAGE RULES — {stage}
GOAL: {config['goal']}

{config['rules']}

ADVANCE when: {config['advanceCondition']}
""".strip()
        
        return f"{GLOBAL_AGENT_RULES}\n\n{stage_block}"

    @staticmethod
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

    @staticmethod
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
