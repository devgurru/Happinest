"""
Image Service — vision-model-powered image analysis for wedding planning.

Accepts up to 3 base64-encoded images per turn.
Extracts structured data and returns:
  - A memory patch (earlySignals.visualSignals merged with existing data)
  - A human-readable summary for the planner reply
  - Telemetry

Design principle:
  - MERGE new signals with existing visualSignals — never destroy previous data.
  - On repeat upload (same stage), planner note asks what's different.
  - imageCount is cumulative across all uploads in the session.
  - Images are NEVER stored. They exist in memory only for the duration of this call.
"""
from __future__ import annotations

import json

from app.services.ai.ai_gateway import AIGatewayError, call_vision_llm

# ---------------------------------------------------------------------------
# Extraction prompt
# ---------------------------------------------------------------------------

_IMAGE_ANALYSIS_PROMPT = """\
You are an expert wedding-planning visual analyst. The user has shared {image_count} image(s) \
to help describe their dream wedding. Analyse all images together as a combined mood board.

Your task is to extract ONLY what you can actually observe in the images. \
Do NOT invent or guess beyond what is visible.

Current planning stage: {stage}
Current known memory (partial): {memory_snippet}

Return a single JSON object with this exact structure (use empty string/array if not applicable):

{{
  "visualSignals": {{
    "colorPalette": [<up to 5 dominant/accent colors as short strings, e.g. "blush pink", "gold", "ivory">],
    "venueType": "<one of: garden, banquet hall, beach, rooftop, palace, haveli, farmhouse, resort, temple, church, outdoor, indoor, or empty string>",
    "settingType": "<one of: indoor, outdoor, destination, or empty string>",
    "styleKeywords": [<up to 5 visual style descriptors, e.g. "floral", "minimalist", "royal", "rustic", "opulent">],
    "identifiedLocation": "<name of specific recognizable location/landmark if clearly visible, else empty string>",
    "occasionCues": [<wedding event types visible/implied, e.g. "mehendi", "sangeet", "reception", "haldi", "pheras">],
    "imageCount": {image_count},
    "summary": "<one concise sentence describing what the images collectively show about the couple's wedding vision>"
  }},
  "occasionHints": {{
    "place": "<city or destination name if a famous landmark or location is recognisable, else empty string>",
    "settingPreference": "<indoor/outdoor/destination if clearly indicated, else empty string>",
    "locationPreference": "<e.g. Udaipur, Goa, beachfront, hill station, etc. if inferable, else empty string>"
  }},
  "vibeHints": [<1-3 vibe labels from this pool ONLY: "Big & festive", "Intimate", "Family-led", "Modern & sleek", "Traditional & rooted", "Whimsical & playful", "Royal & grand", "Warm & personal", "Minimalist", "Maximalist", "Relaxed & easy", "Dramatic & theatrical", "Bohemian" — empty array if unclear>],
  "personalityHints": [<1-3 personality trait labels that match the style, e.g. "Luxurious", "Nature-loving", "Classic", "Bold", "Creative" — empty array if unclear>],
  "eventHints": [<wedding event types if clearly shown or strongly implied — empty array if unclear>],
  "plannerNote": "<short, friendly one-sentence note the planner can include in reply to acknowledge the images, e.g. 'Love the garden vibes and floral palette in your inspiration images!'>"
}}

Rules:
- Only output the JSON. No markdown fences or prose.
- identifiedLocation: Only fill if you are HIGHLY CONFIDENT about a real named place (e.g. "Taj Lake Palace Udaipur", "The Leela Goa"). Avoid guessing.
- colorPalette: Use common color names, not hex codes.
- vibeHints: ONLY use labels from the provided pool. Max 3.
- If an image is a screenshot, text, or unrelated to weddings, note that in summary and leave all fields empty/minimal.
"""

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def analyse_images(
    image_b64_list: list[str],
    stage: str,
    memory: dict,
) -> tuple[dict, str, dict]:
    """
    Analyse up to 3 base64 images and extract wedding-planning signals.

    MERGE behaviour:
      - New visual signals are merged with existing earlySignals.visualSignals.
      - Lists (colorPalette, styleKeywords, occasionCues) are unioned.
      - Scalars (venueType, settingType) use new value if non-empty, else keep old.
      - imageCount is cumulative across all uploads in the session.
      - If existing visualSignals already has data, planner_note is written to
        acknowledge both old and new and ask what's different (repeat upload).

    Returns:
        (memory_patch, planner_note, telemetry)
    """
    images = image_b64_list[:3]
    if not images:
        return {}, "", {}

    # Check if this is a repeat upload (existing visualSignals already has data)
    existing_vs: dict = (
        (memory.get("earlySignals") or {}).get("visualSignals") or {}
    )
    is_repeat = bool(existing_vs.get("colorPalette") or existing_vs.get("venueType") or existing_vs.get("styleKeywords"))

    # Slim memory snippet for the vision model (exclude raw visualSignals blob)
    memory_snippet = json.dumps({
        "identity": memory.get("identity", {}),
        "occasion": memory.get("occasion", {}),
        "earlySignals": {
            k: v for k, v in (memory.get("earlySignals") or {}).items()
            if k != "visualSignals"
        },
    }, indent=2)

    prompt = _IMAGE_ANALYSIS_PROMPT.format(
        image_count=len(images),
        stage=stage,
        memory_snippet=memory_snippet[:1200],
    )

    try:
        result, telemetry = await call_vision_llm(prompt, images, stage)
    except AIGatewayError as e:
        print(f"[IMAGE_SERVICE] Vision call failed: {e.code}: {e.message}")
        return {}, "", {}
    except Exception as e:
        print(f"[IMAGE_SERVICE] Unexpected error: {e}")
        return {}, "", {}

    patch = _build_memory_patch(result, len(images), existing_vs)
    note = _build_planner_note(result, existing_vs, is_repeat)

    return patch, note, telemetry


# ---------------------------------------------------------------------------
# Memory patch builder (merge-aware)
# ---------------------------------------------------------------------------

def _build_memory_patch(result: dict, new_image_count: int, existing_vs: dict) -> dict:
    """
    Convert vision model output into a safe memory patch.
    Lists are MERGED with existing data. Scalars prefer new non-empty value.
    imageCount is cumulative.
    """
    patch: dict = {}
    vs_raw = result.get("visualSignals") or {}

    # --- Merge list fields (union, deduplicate, preserve order) ---
    def _merge_lists(new: list[str], old: list[str], cap: int) -> list[str]:
        seen: dict[str, None] = {}
        for item in old:
            seen[item.lower()] = item
        for item in new:
            key = item.lower()
            if key not in seen:
                seen[key] = item
        return list(seen.values())[:cap]

    new_colors   = _safe_list(vs_raw.get("colorPalette"), max_items=5)
    new_keywords = _safe_list(vs_raw.get("styleKeywords"), max_items=5)
    new_cues     = _safe_list(vs_raw.get("occasionCues"), max_items=6)

    merged_colors   = _merge_lists(new_colors,   _safe_list(existing_vs.get("colorPalette")),   cap=8)
    merged_keywords = _merge_lists(new_keywords, _safe_list(existing_vs.get("styleKeywords")), cap=8)
    merged_cues     = _merge_lists(new_cues,     _safe_list(existing_vs.get("occasionCues")),   cap=8)

    # --- Scalar fields: prefer new non-empty, else keep old ---
    def _prefer_new(new_val: str, old_val: str) -> str:
        return new_val if new_val else old_val

    venue_type  = _prefer_new(_safe_str(vs_raw.get("venueType")),       _safe_str(existing_vs.get("venueType")))
    setting     = _prefer_new(_safe_str(vs_raw.get("settingType")),     _safe_str(existing_vs.get("settingType")))
    id_location = _prefer_new(_safe_str(vs_raw.get("identifiedLocation")), _safe_str(existing_vs.get("identifiedLocation")))
    summary     = _safe_str(vs_raw.get("summary"))  # always use latest summary

    # --- Cumulative imageCount ---
    prev_count = int(existing_vs.get("imageCount") or 0)
    total_count = prev_count + new_image_count

    # Assemble merged visualSignals
    visual: dict = {"imageCount": total_count}
    if merged_colors:   visual["colorPalette"]      = merged_colors
    if venue_type:      visual["venueType"]          = venue_type
    if setting:         visual["settingType"]        = setting
    if merged_keywords: visual["styleKeywords"]      = merged_keywords
    if id_location:     visual["identifiedLocation"] = id_location
    if merged_cues:     visual["occasionCues"]       = merged_cues
    if summary:         visual["summary"]            = summary

    patch["earlySignals"] = {"visualSignals": visual}

    # --- occasionHints → occasion ---
    occ_hints = result.get("occasionHints") or {}
    occ_patch: dict = {}
    place    = _safe_str(occ_hints.get("place"))
    setting2 = _safe_str(occ_hints.get("settingPreference"))
    loc_pref = _safe_str(occ_hints.get("locationPreference"))

    # Try to extract city from identified landmark "Taj Lake Palace, Udaipur" → "Udaipur"
    if not place and id_location:
        parts = [p.strip() for p in id_location.split(",")]
        if len(parts) >= 2:
            place = parts[-1]

    if place:              occ_patch["locationPreference"] = place
    if setting2:           occ_patch["settingPreference"]  = setting2
    if loc_pref and not place: occ_patch["locationPreference"] = loc_pref

    if occ_patch:
        patch["occasion"] = occ_patch

    # --- Merge list-based earlySignals (vibe, personality, events) ---
    early = (patch.get("earlySignals") or {})
    existing_early = {}
    try:
        from app.domain.memory_schema import fresh_memory
        # We only need existing earlySignals non-visual fields
        existing_early = {}
    except Exception:
        pass

    vibe_hints = _safe_list(result.get("vibeHints"), max_items=3)
    if vibe_hints:
        early["vibe"] = vibe_hints

    pers_hints = _safe_list(result.get("personalityHints"), max_items=3)
    if pers_hints:
        early["personality"] = pers_hints

    event_hints = _safe_list(result.get("eventHints"), max_items=6)
    if event_hints:
        early["events"] = event_hints

    patch["earlySignals"] = early
    return patch


# ---------------------------------------------------------------------------
# Planner note builder (repeat-aware)
# ---------------------------------------------------------------------------

def _build_planner_note(result: dict, existing_vs: dict, is_repeat: bool) -> str:
    """
    Build the image_context string passed to the text LLM prompt.

    - First upload → standard warm acknowledgement from vision model.
    - Repeat upload on same stage → note acknowledges both old and new,
      signals text LLM to ask what's different / what they want to keep.
    """
    vs_raw   = result.get("visualSignals") or {}
    new_note = _safe_str(result.get("plannerNote"))
    new_summary = _safe_str(vs_raw.get("summary"))
    new_note = new_note or new_summary or "I've captured the details from your images."

    if not is_repeat:
        return new_note

    # Repeat upload — reference old context and ask what's different
    old_colors  = _safe_list(existing_vs.get("colorPalette"))
    old_venue   = _safe_str(existing_vs.get("venueType"))
    old_style   = _safe_list(existing_vs.get("styleKeywords"))
    new_colors  = _safe_list(vs_raw.get("colorPalette"), max_items=5)
    new_venue   = _safe_str(vs_raw.get("venueType"))

    old_desc = ""
    if old_colors:
        old_desc = f"{', '.join(old_colors[:3])} {old_venue or 'style'}"
    elif old_venue:
        old_desc = old_venue

    new_desc = ""
    if new_colors:
        new_desc = f"{', '.join(new_colors[:3])} {new_venue or 'style'}"
    elif new_venue:
        new_desc = new_venue

    if old_desc and new_desc and old_desc.lower() != new_desc.lower():
        return (
            f"You've shared more inspiration! Earlier you showed {old_desc}, and these new images show {new_desc}. "
            f"I've kept all the details — what's different about this new set, or are you exploring a different style?"
        )
    elif old_desc:
        return (
            f"More inspiration — love it! These new images add to what you shared before ({old_desc}). "
            f"I've combined everything. Are these showing the same vibe, or a different direction you're considering?"
        )
    else:
        return (
            f"You've shared more images! I've merged these with your earlier uploads. "
            f"Are these showing the same vision, or something different you'd like to explore?"
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_str(value: object, max_len: int = 120) -> str:
    if not value or not isinstance(value, str):
        return ""
    return value.strip()[:max_len]


def _safe_list(value: object, max_items: int = 5) -> list[str]:
    if not isinstance(value, list):
        return []
    return [
        str(item).strip()
        for item in value[:max_items]
        if item and isinstance(item, str) and len(str(item).strip()) > 0
    ]
