"""
Stage Policy Engine — SINGLE SOURCE OF TRUTH for all stage behavior.

This module owns:
1. STAGE_CONFIG — per-stage goals, extraction rules, advance conditions
2. Stage completion logic (backend enforcement via is_stage_complete)
3. Transition validation (allowed stage movements)
4. Final decision resolution (backend overrides AI proposals)

HOW THE NEW ARCHITECTURE WORKS:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  AI Call 1 (data_extractor.py) reads STAGE_CONFIG["extractionRules"] to know
  what to extract and how to validate it for each stage.

  context_builder.py calls is_stage_complete() and reads missingFieldsHint to
  determine stay/advance and what fields to ask for.

  AI Call 2 (response planner) receives a TurnContext with pre-computed
  decisions — it only writes the human reply and suggestion chips.

HOW TO UPDATE STAGE BEHAVIOR:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
To modify a stage:
  1. UPDATE STAGE_CONFIG (extractionRules, advanceCondition, missingFieldsHint)
  2. UPDATE is_stage_complete() for backend enforcement
  3. For allowed transitions, update: app/domain/enums.py (ALLOWED_TRANSITIONS)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
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
# STAGE CONFIGURATION — single source of truth per stage
#
# Each stage has:
#   goal             — What the stage is trying to accomplish (shown in prompts)
#   extractionRules  — Injected into AI Call 1 (data_extraction.txt)
#   requiredFields   — Field paths needed for completion (for context_builder)
#   missingFieldsHint— Human-readable missing field names (for prompts)
#   advanceCondition — When stage can advance (for documentation + prompt)
#   stateless        — Whether this stage depends on prior memory
# ============================================================================

STAGE_CONFIG: dict[str, dict] = {

    StageId.S2_BASICS.value: {
        "goal": "Capture where and when the wedding will be. Advance when location/timing is confirmed or kept flexible.",
        "extractionRules": """\
IMPORTANT: Even if metaIntent is "correction" (e.g. correcting names), STILL extract ALL S2 fields below.

SPECIFICITY LEVEL (output validationNotes.specificityLevel):
- L0: Junk / unusable (e.g. "kgjhgjkh ghjghj", "123123") → set metaIntent = "gibberish"
- IL1: Broad setting/timing (e.g. "beach destination in winter of 26", "royal palace wedding sometime next year")
- IL1_FLEXIBLE: Flexible consent / non-committal (e.g. "nothing finalized", "not sure", "keep it flexible", "decide later")
  → set validatedPatch.occasion.specificityLevel = "IL1_FLEXIBLE"
- IL2: Refined region & month (e.g. "East Asia in Dec 26", "Thailand or Bali in December 2026", "Rajasthan palace in Feb 2026")
- IL3: Exact city & dates (e.g. "Goa, Dec 18-20 2026", "Udaipur, Feb 12-14 2026", "Phuket first week of December 2026")

Extract into validatedPatch.occasion (only fields that are mentioned):
- place: real wedding destination — city, region, or venue name (e.g. "Delhi", "Goa", "East Asia", "Thailand or Bali", "Udaipur")
  → In validationNotes.resolvedCountry: identify the country
    Examples: "Lahore Fort" → Pakistan, "Delhi" → India, "Dubai" → UAE, "Goa" → India
- datePreference: future date (day+month+year, month+year, or year) — MUST preserve exact day/month when provided
  → TODAY IS: July 2026
  → "12 June 2028" → "12 June 2028"
  → "next year June" → "June 2027"
  → "this December" → "December 2026"
  → If resolved date is before July 2026: set validationNotes.isPastDate=true, exclude from patch
- seasonPreference: ONLY when user names a season ("Winter wedding", "Summer celebration", "Monsoon")
- settingPreference: beach / palace / garden / indoor / outdoor — only when explicitly stated
- destinationMode: "destination" (away from home) | "local" (same city) | "unknown"

Also extract into earlySignals (NOT validatedPatch):
- personality: ["Foodies", "College sweethearts", "Travel lovers", "Big family"]
- vibe: ["Big & festive", "Intimate", "Traditional", "Royal & grand"]
- events: ["Mehndi", "Barat", "Walima", "Reception", "Sangeet", "Haldi", "Engagement"]
- budget: { "range": "$25-30k", "currency": "USD" }
- guestCount: number (e.g. 2500)

EARLY SIGNALS CONFIRMATION: If earlySignals already in memory AND user confirms → extract into validatedPatch.

Reject (do not include in validatedPatch, add to validationNotes.rejectedReasons):
- Past dates or years (before July 2026)
- Gibberish / random noise""",
        "requiredFields": ["occasion.place", "occasion.datePreference"],
        "missingFieldsHint": ["wedding destination (city or region)", "wedding date or season"],
        "advanceCondition": "Location & timing confirmed (IL2/IL3/IL1_FLEXIBLE) or broad location asked once (IL1 turn 2)",
        "stateless": True,
    },

    StageId.S3_PERSONALITY.value: {
        "goal": "Capture who the couple is — personality, lifestyle, relationship, cultural background.",
        "extractionRules": """\
Extract into validatedPatch.personality (only fields that are mentioned):
- tags: short meaningful labels (1-5 words each)
  VALID examples: "Foodies", "College sweethearts", "Travel lovers", "Bollywood lovers",
    "Sufi music fans", "Outdoor adventurers", "Homebodies", "Fitness enthusiasts",
    "Bookworms", "Tech geeks", "Childhood sweethearts", "Big family people"
  INVALID (reject these): cities ("Delhi"), months ("March"), years, full long sentences, gibberish
- culturalSignals: cultural background signals (e.g. ["Punjabi", "South Indian", "Marwari"])
- relationshipSignals: how they met or relationship type (e.g. ["College sweethearts", "Childhood friends"])
- lifestyleSignals: hobbies or lifestyle (e.g. ["Hikers", "Foodies", "Homebodies"])

EARLY SIGNALS CONFIRMATION: If earlySignals.personality has values in memory AND user confirms them
(says "yes" / "keep those" / "go with those" / "use earlier" / "that's right" / "don't update" / "perfect") →
extract earlySignals.personality values into validatedPatch.personality.tags.

Also extract into earlySignals:
- vibe: labels like ["Big & festive", "Intimate", "Traditional & rooted"]
- events: ["Mehndi", "Sangeet", "Haldi", "Reception"]
- budget: { "range": "...", "currency": "..." }

Reject (add to validationNotes.rejectedReasons):
- Cities, months, or years as personality tags
- Occasion rehash (user just repeating their location/date) → set metaIntent to "clarification"
- Random keystrokes → set metaIntent to "gibberish", validatedPatch must be {}
- Single meaningless word fragments""",
        "requiredFields": ["personality.tags"],
        "missingFieldsHint": ["personality traits or labels describing the couple (2+ needed)"],
        "advanceCondition": "2+ meaningful personality tags (never cities/dates/gibberish)",
        "stateless": False,
    },

    StageId.S4_VIBE.value: {
        "goal": "Confirm primary vibe/atmosphere of the wedding. Ask what feeling/energy they want.",
        "extractionRules": """\
Extract into validatedPatch.vibe:
- primaryVibe: one of these pool values → ["Big & festive", "Intimate & cozy", "Traditional & rooted",
    "Modern & chic", "Royal & grand", "Relaxed & easy", "Destination adventure"]
  OR a clear custom vibe the user clearly commits to (e.g. "Sunset garden party", "Heritage glam")
  NOT a city ("Goa"), NOT a personality tag ("Foodies"), NOT a month ("December")
- secondaryVibes: additional vibe labels if user mentions more than one (list)
- energyLevel: "high" | "medium" | "low" — only if user implies it
- formality: "formal" | "semi-formal" | "casual" | "traditional" — only if clearly stated
- familyRole: "extended-family-centered" | "nuclear" | "mixed" — only if mentioned

EARLY SIGNALS CONFIRMATION: If earlySignals.vibe has values in memory AND user confirms →
extract earlySignals.vibe[0] into validatedPatch.vibe.primaryVibe.
User confirmation signals: "yes" / "keep that" / "go with it" / "use earlier" / "that's right" / "perfect"

Also extract into earlySignals:
- events: ["Mehndi", "Haldi", "Sangeet", "Reception", "Engagement"]
- budget: { "range": "...", "currency": "..." }
- vendors: { "photography": "candid" }

Reject (add to rejectedReasons, do not include in validatedPatch):
- Cities or months as vibe (e.g. "Goa" is NOT a vibe)
- Personality tags in vibe fields
- Occasion rehash → clarification""",
        "requiredFields": ["vibe.primaryVibe"],
        "missingFieldsHint": ["wedding vibe or atmosphere (e.g. Big & festive, Intimate, Royal & grand)"],
        "advanceCondition": "primaryVibe is a pool label or clear custom vibe; personality already filled",
        "stateless": False,
    },

    StageId.S5_BRIEF.value: {
        "goal": "Present/refine the couple brief. Synthesis owns brief text generation.",
        "extractionRules": """\
This is a synthesis stage — the brief has been generated by AI.
Only extract corrections if user explicitly corrects something:
- occasion corrections: place, date
- personality corrections: tags
- vibe corrections: primaryVibe
Set correctedSection to the section being corrected.
If user asks to see directions → set metaIntent to "normal" (direction request is handled separately).""",
        "requiredFields": [],
        "missingFieldsHint": [],
        "advanceCondition": "Brief confirmed or direction synthesis requested",
        "stateless": False,
    },

    StageId.S6_DIRECTIONS.value: {
        "goal": "User picks one design direction option from the presented list.",
        "extractionRules": """\
Extract into validatedPatch.direction:
- selectedDirectionId: the slug/id of the direction option the user picks
- selectedDirectionName: the name of the selected direction

Match against direction options in memory.direction.options.
If user describes a direction without naming it → match to closest option.
Do NOT extract place names as direction names.""",
        "requiredFields": ["direction.selectedDirectionId"],
        "missingFieldsHint": ["selected design direction (pick one from the options shown)"],
        "advanceCondition": "A listed direction is clearly selected",
        "stateless": False,
    },

    StageId.S7_EVENTS.value: {
        "goal": "Confirm which wedding functions/events they want. Ask about events specifically.",
        "extractionRules": """\
Extract into validatedPatch.logistics:
- events: list of wedding function names
  Normalize: "mehendi"→"Mehndi", "sangeet"→"Sangeet", "reception"→"Reception",
    "haldi"→"Haldi", "engagement"→"Engagement", "nikah"→"Nikah",
    "cocktail"→"Cocktail Party", "wedding ceremony"→"Wedding Ceremony"
- eventsConfirmed: 
    * true ONLY when user explicitly confirms that the event list is COMPLETE and FINAL ("yes that's all", "confirm", "only these", "just these", "done", "no more", "looks good", "perfect").
    * false whenever user wants to add, update, or change events ("yes I want to add more", "add Sangeet", "update events", "no", "nope", "not yet", "wait", "change").

EARLY SIGNALS CONFIRMATION: If earlySignals.events has values in memory AND user confirms →
extract earlySignals.events into validatedPatch.logistics.events AND set eventsConfirmed appropriately.

Also extract into earlySignals:
- budget: { "range": "...", "currency": "..." }
- vendors: { "photography": "candid" }

Reject (rejectedReasons):
- Colors, aesthetics, decor as events
- Personality or vibe data as events""",
        "requiredFields": ["logistics.events"],
        "missingFieldsHint": ["wedding functions/events"],
        "advanceCondition": "1+ events listed",
        "stateless": False,
    },

    StageId.S8_GUESTS.value: {
        "goal": "Capture guest count for EVERY selected event.",
        "extractionRules": """\
Extract into validatedPatch.logistics:
- guestCounts: { "EventName": number }
  Map each event name to a guest count number.
  Only extract counts for events listed in memory.logistics.events.
  If user gives a single total number without specifying events → leave guestCounts empty (can't distribute).
  Normalize: "200 people" → 200, "around 300" → 300, "500+" → 500

Also extract into earlySignals:
- budget: { "range": "...", "currency": "..." } — if user mentions budget while answering""",
        "requiredFields": ["logistics.guestCounts"],
        "missingFieldsHint": ["guest count for each wedding event"],
        "advanceCondition": "guestCounts filled for all events in logistics.events",
        "stateless": False,
    },

    StageId.S9_BUDGET.value: {
        "goal": "Get a comfortable total budget range in local country currency or requested currency.",
        "extractionRules": """\
Extract into validatedPatch.logistics:
- budget: {
    "range": "27 Million" or "$2.5 Million" or "5 Lakhs" or "AED 500,000",
    "currency": "PKR" / "INR" / "USD" / "AED" / "EUR" / "GBP" etc.,
    "userConfirmedOverride": true/false,
    "budgetFixed": true/false,
    "requirementsFixed": true/false
  }
  Confirmation / Override Rules:
  - If the user explicitly refuses to adjust or increase their budget (e.g. "i am not flexible with my budget", "i cannot increase my budget", "budget is fixed", "cannot increase budget more"):
    → set validatedPatch.logistics.budget.budgetFixed = true
  - If the user explicitly refuses to reduce or adjust their events/guests/destination (e.g. "don't want to reduce events", "keep guest count same", "cannot adjust guest count", "don't want to reduce guest count"):
    → set validatedPatch.logistics.budget.requirementsFixed = true
  - If the user insists on keeping everything as-is and refuses to adjust either budget or guest count/events (e.g. "this is my final budget", "don't want to change anything", "keep it as is", "no change", "keep guest count as it is", "keep them same", "no changes", "keep everything as is", "don't want to update anything"):
    → set validatedPatch.logistics.budget.userConfirmedOverride = true
  Currency & Unit Normalization Rules:
  - Default currency is inferred from the wedding country (Pakistan -> PKR, India -> INR, UAE -> AED, USA -> USD, UK -> GBP, Italy/Europe -> EUR, etc.).
  - User EXPLICIT requested currency (e.g. "USD", "$", "AED", "EUR") ALWAYS overrides the country default.
  - Unit Rules for ALL Currencies:
    - Amounts >= 10 Lakhs (or >= 1 Million): ALWAYS format in MILLIONS (M) or BILLIONS (B)! Do NOT leave as Crores or 100+ Lakhs!
      - 2.7 Crores (270 Lakhs) -> "27 Million"
      - 1 Crore (100 Lakhs) -> "10 Million"
      - 50 Lakhs -> "5 Million"
      - 25 Lakhs -> "2.5 Million"
      - 100 Crores -> "1 Billion"
      - $1.5M / €2.5M -> "$1.5 Million" / "€2.5 Million"
    - Amounts < 10 Lakhs (or < 1 Million): Format as Lakhs (for South Asian currencies e.g. "5 Lakhs") or Thousands/K (for global currencies e.g. "$500K" or "AED 500,000").
  - "not sure" or vague → do not extract, stay and clarify

EARLY SIGNALS CONFIRMATION: If earlySignals.budget has value in memory AND user confirms →
extract earlySignals.budget into validatedPatch.logistics.budget.

Also extract into earlySignals:
- vendors: { "photography": "candid" } — if mentioned""",
        "requiredFields": ["logistics.budget.range"],
        "missingFieldsHint": ["budget range"],
        "advanceCondition": "budget.range filled",
        "stateless": False,
    },

    StageId.S10_VENDORS.value: {
        "goal": "Capture vendor category priorities per event day.",
        "extractionRules": """\
Extract into validatedPatch.logistics:
- vendorPreferences: { "EventName": ["Vendor Category 1", "Vendor Category 2"] }
  Map each event name in memory.logistics.events to its list of selected vendor categories.
  Only include events listed in memory.logistics.events.
  Examples:
    {
      "Mehndi": ["Mehendi artist", "Catering", "Décor"],
      "Sangeet": ["Stage and sound", "Sangeet performers", "DJ and entertainment"],
      "Haldi": ["Haldi setup", "Catering", "Décor"]
    }

EARLY SIGNALS CONFIRMATION: If earlySignals.vendors has values in memory AND user confirms →
extract earlySignals.vendors into validatedPatch.logistics.vendorPreferences.""",
        "requiredFields": ["logistics.vendorPreferences"],
        "missingFieldsHint": ["vendor category preferences per wedding event"],
        "advanceCondition": "vendorPreferences has at least one entry",
        "stateless": False,
    },

    StageId.S11_SUMMARY.value: {
        "goal": "Confirm final summary. Synthesis owns summary text.",
        "extractionRules": """\
This is a synthesis stage — the summary has been generated by AI.
Only extract corrections if user explicitly corrects something.
Set correctedSection to the section being corrected.""",
        "requiredFields": [],
        "missingFieldsHint": [],
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
# STAGE POLICY CLASS
# ============================================================================

def format_cost(amount_usd: float, currency: str) -> str:
    if currency == "INR":
        amount_inr = amount_usd * 85
        if amount_inr >= 10000000:
            return f"{amount_inr / 10000000:.1f} Crore INR"
        elif amount_inr >= 100000:
            return f"{amount_inr / 100000:.1f} Lakhs"
        return f"{amount_inr:,.0f} INR"
    elif currency == "PKR":
        amount_pkr = amount_usd * 280
        if amount_pkr >= 10000000:
            return f"{amount_pkr / 10000000:.1f} Crore PKR"
        elif amount_pkr >= 100000:
            return f"{amount_pkr / 100000:.1f} Lakhs"
        return f"{amount_pkr:,.0f} PKR"
    elif currency == "AED":
        return f"AED {amount_usd * 3.67:,.0f}"
    elif currency == "GBP":
        return f"£{amount_usd * 0.78:,.0f}"
    elif currency == "EUR":
        return f"€{amount_usd * 0.92:,.0f}"
    else:
        # USD
        if amount_usd >= 1000:
            return f"${amount_usd / 1000:.0f}k"
        return f"${amount_usd:,.0f}"


def check_budget_feasibility(memory: dict) -> tuple[bool, str, float]:
    """
    Evaluates wedding budget feasibility based on place, events, and guest counts.
    Returns (is_feasible, formatted_estimated_min_budget, estimated_cost_usd).
    """
    import re
    occasion = memory.get("occasion") or {}
    logistics = memory.get("logistics") or {}
    
    place = (occasion.get("place") or occasion.get("locationPreference") or "").strip()
    events = logistics.get("events") or []
    guest_counts = logistics.get("guestCounts") or {}
    
    # 1. Determine Tier Pricing
    place_l = place.lower()
    # High-tier: Europe, USA, premium destinations
    high_tier_keywords = ["amalfi", "como", "hawaii", "maldives", "paris", "london", 
                          "new york", "switzerland", "swiss", "italy", "france", 
                          "usa", "uk", "united kingdom", "santorini", "greece"]
    
    # Mid-tier: resort destinations, premium South Asia
    mid_tier_keywords = ["goa", "phuket", "bali", "tulum", "krabi", "da nang", 
                         "udaipur", "jaipur", "jodhpur", "dubai", "uae"]
    
    cost_per_guest = 50.0  # Default standard/local
    cost_per_event = 2000.0
    
    is_high = any(k in place_l for k in high_tier_keywords)
    is_mid = any(k in place_l for k in mid_tier_keywords)
    
    if is_high:
        cost_per_guest = 400.0
        cost_per_event = 8000.0
    elif is_mid:
        cost_per_guest = 150.0
        cost_per_event = 4000.0
        
    # 2. Calculate Estimated Minimum Budget in USD
    total_guests = sum(guest_counts.values()) if isinstance(guest_counts, dict) else 0
    num_events = len(events)
    
    estimated_cost_usd = (total_guests * cost_per_guest) + (num_events * cost_per_event)
    estimated_cost_usd = max(5000.0, estimated_cost_usd)
    
    # 3. Get User Budget
    budget_obj = logistics.get("budget") or memory.get("earlySignals", {}).get("budget") or {}
    budget_str = (budget_obj.get("range") or "").strip()
    currency = (budget_obj.get("currency") or "").strip().upper()
    
    if not budget_str:
        return True, "", 0.0  # No budget to check yet
        
    if not currency:
        # Infer currency from place
        if any(k in place_l for k in ["delhi", "mumbai", "goa", "udaipur", "jaipur", "jodhpur", "india"]):
            currency = "INR"
        elif any(k in place_l for k in ["lahore", "karachi", "islamabad", "bhurban", "hunza", "pakistan"]):
            currency = "PKR"
        else:
            currency = "USD"
            
    # Parse User Budget Max Value
    nums = [float(s) for s in re.findall(r'\d+\.?\d*', budget_str)]
    if not nums:
        return True, "", 0.0
    max_val = max(nums)
    
    # Apply multipliers
    budget_val = budget_str.lower()
    multiplier = 1.0
    if "k" in budget_val:
        multiplier = 1000.0
    elif "lakh" in budget_val or "lac" in budget_val:
        multiplier = 100000.0
    elif "million" in budget_val or "m" in budget_val:
        multiplier = 1000000.0
    elif "crore" in budget_val or "cr" in budget_val:
        multiplier = 10000000.0
        
    user_budget_nominal = max_val * multiplier
    
    # Convert User Budget to USD for comparison
    user_budget_usd = user_budget_nominal
    if currency == "INR":
        user_budget_usd = user_budget_nominal / 85.0
    elif currency == "PKR":
        user_budget_usd = user_budget_nominal / 280.0
    elif currency == "AED":
        user_budget_usd = user_budget_nominal / 3.67
    elif currency == "GBP":
        user_budget_usd = user_budget_nominal / 0.78
    elif currency == "EUR":
        user_budget_usd = user_budget_nominal / 0.92
        
    # 4. Compare
    if user_budget_usd < estimated_cost_usd:
        # Infeasible! Format estimated cost in user's currency
        formatted_est = format_cost(estimated_cost_usd, currency)
        return False, formatted_est, estimated_cost_usd
        
    return True, "", estimated_cost_usd


class StagePolicy:
    """
    Backend enforcement and stage behavior.

    Provides:
    - Stage completion checks (deterministic, backend-owned)
    - Transition validation
    - Final stage decision resolution (backend overrides AI proposals)
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
        """Returns (is_valid, error_reason). Backend uses this to reject invalid stage moves."""
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

        if decision_type == StageDecisionType.REANCHOR.value:
            if from_s == to_s:
                return True, None
            return False, "REANCHOR must keep same stage"

        if decision_type == StageDecisionType.REQUEST_CLARIFICATION.value:
            if from_s == to_s:
                return True, None
            return False, "REQUEST_CLARIFICATION must keep same stage"

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
            from app.utils.validators import get_occasion_state
            return get_occasion_state(memory)["is_complete"]

        if stage_id == StageId.S3_PERSONALITY:
            from app.utils.validators import filter_tags
            p = memory.get("personality", {})
            tags = filter_tags(p.get("tags") or [])
            rel = len(p.get("relationshipSignals") or [])
            life = len(p.get("lifestyleSignals") or [])
            return len(tags) >= 2 or (len(tags) >= 1 and (rel + life) >= 1)

        if stage_id == StageId.S4_VIBE:
            from app.domain.memory_schema import resolve_primary_vibe
            if not StagePolicy.is_stage_complete(StageId.S3_PERSONALITY.value, memory):
                return False
            return bool(resolve_primary_vibe(memory))

        if stage_id == StageId.S6_DIRECTIONS:
            direction = memory.get("direction", {})
            return bool((direction.get("selectedDirectionId") or "").strip())

        if stage_id == StageId.S7_EVENTS:
            logistics = memory.get("logistics", {}) or {}
            events = logistics.get("events") or []
            return len(events) >= 1

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
            has_budget = bool((budget.get("range") or budget.get("amount") or "").strip())
            if not has_budget:
                return False
            
            # Evaluate feasibility
            is_feasible, _, _ = check_budget_feasibility(memory)
            if is_feasible:
                return True
                
            # If not feasible, check for overrides or if both budget and requirements are confirmed fixed
            override = budget.get("userConfirmedOverride", False)
            budget_fixed = budget.get("budgetFixed", False)
            reqs_fixed = budget.get("requirementsFixed", False)
            if override or (budget_fixed and reqs_fixed):
                return True
                
            return False

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
        Given AI's proposed decision, returns backend-validated (decision_type, to_stage).
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

        # Clarification: always honor
        if ai_decision_type == StageDecisionType.REQUEST_CLARIFICATION.value:
            return (
                StageDecisionType.REQUEST_CLARIFICATION.value,
                current_stage,
                "need_clarification",
            )

        # Do not advance while model still has open questions
        if open_questions and ai_decision_type == StageDecisionType.ADVANCE.value:
            return StageDecisionType.STAY.value, current_stage, "open_questions_block_advance"

        # S5 brief: advance via synthesis only
        if current_stage == StageId.S5_BRIEF.value:
            if is_valid and ai_decision_type == StageDecisionType.STAY.value:
                return ai_decision_type, ai_to_stage, "ai_stay"
            return StageDecisionType.STAY.value, current_stage, "awaiting_brief_synthesis"

        # Honor explicit AI STAY decision
        if is_valid and ai_decision_type == StageDecisionType.STAY.value:
            return ai_decision_type, ai_to_stage, "ai_stay_respected"

        # Auto-advance when memory is complete
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
                        "memory_complete_auto_advance",
                    )

        # Gated stages: never honor advance when stage is incomplete
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

        # Honor valid AI advance for non-gated stages
        if is_valid and ai_decision_type == StageDecisionType.ADVANCE.value:
            return ai_decision_type, ai_to_stage, "ai_advance"

        return StageDecisionType.STAY.value, current_stage, "continue_gathering"

    @staticmethod
    def infer_synthesis_type(stage: str, memory: dict | None = None) -> str | None:
        """Map synthesis stages to synthesis type when client omits it."""
        from app.domain.memory_schema import resolve_primary_vibe

        memory = memory or {}

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
        """Return stage-specific context hints for prompts (backward compat)."""
        config = STAGE_CONFIG.get(stage)
        if not config:
            return {}
        return {
            "goal": config["goal"],
            "advanceCondition": config["advanceCondition"],
            "stateless": config.get("stateless", False),
            "extractionRules": config.get("extractionRules", ""),
            "missingFieldsHint": config.get("missingFieldsHint", []),
        }
