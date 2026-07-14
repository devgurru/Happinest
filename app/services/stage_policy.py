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
            from app.services.text_extract import is_concrete_timing, sanitize_timing_fields
            occ = sanitize_timing_fields(memory.get("occasion", {}) or {})
            has_place = bool((occ.get("place") or occ.get("locationPreference") or "").strip())
            return has_place and is_concrete_timing(occ)

        if stage_id == StageId.S3_PERSONALITY:
            from app.services.text_extract import filter_tags
            p = memory.get("personality", {})
            tags = filter_tags(p.get("tags") or [])
            signal_count = (
                len(p.get("culturalSignals") or [])
                + len(p.get("relationshipSignals") or [])
                + len(p.get("lifestyleSignals") or [])
            )
            return len(tags) >= 2 or (len(tags) >= 1 and signal_count >= 1)

        if stage_id == StageId.S4_VIBE:
            from app.domain.memory_schema import resolve_primary_vibe
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

        # Clarification only while stage is incomplete
        if ai_decision_type == StageDecisionType.REQUEST_CLARIFICATION.value:
            if not StagePolicy.is_stage_complete(current_stage, memory):
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
                    "Understand occasion basics: where and when. Need place/setting PLUS a concrete "
                    "month or named season (Winter/Summer/Monsoon/Spring). Vague weather is not enough. "
                    "Note early personality hints in conversation but do not advance for them."
                ),
                "memoryPatchHint": (
                    'Patch occasion only: {"place": "...", "datePreference": "September", '
                    '"settingPreference": "beach", "destinationMode": "local|destination|unknown"}. '
                    "Do NOT write personality.tags or vibe here."
                ),
                "advanceCondition": (
                    "place (or setting) + concrete month OR named season "
                    "(not 'cold weather' / 'not sure')"
                ),
                "stateless": True,
            },
            StageId.S3_PERSONALITY.value: {
                "goal": "Capture personality tags and cultural/relationship/lifestyle signals from committed input.",
                "memoryPatchHint": (
                    'Patch personality: {"tags": ["..."], "culturalSignals": [], '
                    '"relationshipSignals": [], "lifestyleSignals": [], "plannerInterpretation": "..."}'
                ),
                "advanceCondition": "2+ personality tags or 1 tag + cultural signals",
                "stateless": False,
            },
            StageId.S4_VIBE.value: {
                "goal": "Confirm primary vibe, energy, formality, and family role.",
                "memoryPatchHint": (
                    'Patch vibe: {"primaryVibe": "...", "secondaryVibes": [], "energyLevel": "...", '
                    '"formality": "...", "familyRole": "...", "plannerInterpretation": "..."}'
                ),
                "advanceCondition": "primaryVibe confirmed",
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
