"""
Stage Policy Engine — enforces allowed transitions and stage decision rules.
Backend controls stage; AI only proposes. Policy validates and overrides using memory.
"""
from app.domain.enums import (
    ALLOWED_TRANSITIONS,
    AI_REQUIRED_STAGES,
    SYNTHESIS_STAGES,
    StageDecisionType,
    StageId,
    SynthesisType,
)


class StagePolicy:

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
    def get_stage_prompt_context(stage: str) -> dict:
        """Return stage-specific context hints for prompt builder."""
        stage_context = {
            StageId.S2_BASICS.value: {
                "goal": (
                    "Capture where and when. Warm, clear questions — vary wording on gibberish. "
                    "On advance, transition to personality (who the couple is), not venue setting."
                ),
                "memoryPatchHint": (
                    'Valid: {"occasion": {"place": "Goa", "datePreference": "December 2026", '
                    '"destinationMode": "destination"}}. '
                    "If they give place + future month in one message, patch BOTH and advance. "
                    "Past months/years must NOT enter datePreference — stay and ask for a future date. "
                    "Gibberish → {} + request_clarification. Never personality/vibe here."
                ),
                "advanceCondition": (
                    "place + concrete FUTURE month/season in memory; "
                    "advance reply asks about the COUPLE (s3), not setting"
                ),
                "stateless": True,
            },
            StageId.S3_PERSONALITY.value: {
                "goal": (
                    "Capture who the couple is. YOU validate tags — reject gibberish; "
                    "only meaningful personality/culture/relationship signals enter memoryPatch."
                ),
                "memoryPatchHint": (
                    'If valid: {"personality": {"tags": ["Foodies", "..."], '
                    '"culturalSignals": [], "relationshipSignals": [], "lifestyleSignals": []}}. '
                    "If gibberish or unclear: memoryPatch = {} and request_clarification."
                ),
                "advanceCondition": "2+ meaningful personality tags (never cities/dates/gibberish)",
                "stateless": False,
            },
            StageId.S4_VIBE.value: {
                "goal": (
                    "Confirm primary vibe from chip pool. Acknowledge earlySignals.vibe "
                    "from memory when present."
                ),
                "memoryPatchHint": (
                    'If valid: {"vibe": {"primaryVibe": "Big & festive", ...}}. '
                    "Never city/month as primaryVibe. Gibberish → {} + request_clarification."
                ),
                "advanceCondition": "primaryVibe is a pool label; personality already filled",
                "stateless": False,
            },
            StageId.S7_EVENTS.value: {
                "goal": (
                    "Confirm which wedding functions/events they want. Ask about events — "
                    "NOT colors, textures, or direction aesthetics. Offer event chips."
                ),
                "memoryPatchHint": (
                    'Patch logistics: {"events": ["Mehndi", "Sangeet", ...]}. '
                    "Set eventsConfirmed only when user says the list is final."
                ),
                "advanceCondition": (
                    "1+ events listed AND user confirmed the list is complete "
                    "(e.g. 'that's all' / 'only these')"
                ),
                "stateless": False,
            },
            StageId.S8_GUESTS.value: {
                "goal": (
                    "Capture guest count for EVERY selected event. "
                    "Do not ask about budget or vendors yet."
                ),
                "memoryPatchHint": (
                    'Patch logistics: {"guestCounts": {"Mehndi": 80, "Sangeet": 250}}'
                ),
                "advanceCondition": "guestCounts filled for all events in logistics.events",
                "stateless": False,
            },
            StageId.S9_BUDGET.value: {
                "goal": "Get a comfortable total budget range in INR lakhs.",
                "memoryPatchHint": 'Patch logistics: {"budget": {"range": "40-60 lakhs", "currency": "INR"}}',
                "advanceCondition": "budget.range filled",
                "stateless": False,
            },
            StageId.S10_VENDORS.value: {
                "goal": "Capture vendor category priorities per event day.",
                "memoryPatchHint": (
                    'Patch logistics: {"vendorPreferences": {"photography": "candid", '
                    '"entertainment": "Sufi + Bollywood DJ"}}'
                ),
                "advanceCondition": "vendorPreferences has at least one entry",
                "stateless": False,
            },
        }
        return stage_context.get(stage, {})
