"""
Vendor Search Service — similarity-based vendor lookup using pgvector.

Builds a query embedding from user memory (occasion, vibe, personality, budget)
and finds top-k matching vendors per vendor type using cosine similarity.
"""
from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.ai.embedding_service import embed_text

logger = logging.getLogger(__name__)


# ─── Vendor Category → DB vendor_type mapping ─────────────────────────────────
# UI labels from EVENT_VENDOR_CHIPS → DB vendor_type values

VENDOR_CATEGORY_TO_DB_TYPE: dict[str, str] = {
    "photography": "Photographer",
    "photographer": "Photographer",
    "catering": "Caterer",
    "caterer": "Caterer",
    "venue": "Venue",
    "venue & decor": "Venue",
    "decor": "Venue",
    "decor": "Venue",
    "dj and entertainment": "DJ",
    "dj": "DJ",
    "dj / music": "DJ / Music",
    "entertainment": "DJ",
    "cake": "Cake",
    "invitation": "Invitation Vendor",
    "invitation vendor": "Invitation Vendor",
    "invitations": "Invitation Vendor",
    "gift": "Gift Vendor",
    "gift vendor": "Gift Vendor",
    "bar and beverages": "Bartender",
    "bartender": "Bartender",
    "florals": "Venue",
    "stage and sound": "DJ",
    "sangeet performers": "DJ / Music",
    "ring stage setup": "Venue",
    "stage setup": "Venue",
    "band & dhol": "DJ / Music",
    "lighting": "Venue",
}


def map_vendor_category_to_db_type(category: str) -> str | None:
    """Map a UI vendor category label to the corresponding DB vendor_type."""
    key = category.strip().lower()
    return VENDOR_CATEGORY_TO_DB_TYPE.get(key)


def extract_unique_db_vendor_types(vendor_prefs: dict) -> list[str]:
    """
    Extract unique DB vendor_types from vendorPreferences.
    vendorPreferences format: { "EventName": ["Category1", "Category2"] }
    Returns deduplicated list of DB vendor_type strings.
    """
    seen: set[str] = set()
    result: list[str] = []

    for event_name, categories in vendor_prefs.items():
        if not isinstance(categories, list):
            continue
        for cat in categories:
            if not isinstance(cat, str):
                continue
            db_type = map_vendor_category_to_db_type(cat)
            if db_type and db_type not in seen:
                seen.add(db_type)
                result.append(db_type)

    return result


# ─── Query Text Builder ───────────────────────────────────────────────────────

def build_vendor_query_text(memory: dict) -> str:
    """
    Build a search string from user memory for embedding-based vendor matching.
    Combines occasion, personality, vibe, budget, and event details.
    """
    parts: list[str] = []

    occasion = memory.get("occasion") or {}
    personality = memory.get("personality") or {}
    vibe = memory.get("vibe") or {}
    logistics = memory.get("logistics") or {}

    # Location context
    place = (occasion.get("place") or "").strip()
    if place:
        parts.append(f"Wedding in {place}.")
    setting = (occasion.get("settingPreference") or "").strip()
    if setting:
        parts.append(f"Setting: {setting}.")

    # Personality
    tags = personality.get("tags") or []
    if tags:
        parts.append(f"Couple personality: {', '.join(tags)}.")
    cultural = personality.get("culturalSignals") or []
    if cultural:
        parts.append(f"Cultural: {', '.join(cultural)}.")

    # Vibe
    primary_vibe = (vibe.get("primaryVibe") or "").strip()
    if primary_vibe:
        parts.append(f"Vibe: {primary_vibe}.")
    secondary = vibe.get("secondaryVibes") or []
    if secondary:
        parts.append(f"Secondary vibes: {', '.join(secondary)}.")
    formality = (vibe.get("formality") or "").strip()
    if formality:
        parts.append(f"Formality: {formality}.")

    # Events
    events = logistics.get("events") or []
    if events:
        parts.append(f"Events: {', '.join(events)}.")

    # Budget
    budget = logistics.get("budget") or {}
    budget_range = (budget.get("range") or "").strip()
    if budget_range:
        currency = (budget.get("currency") or "").strip()
        parts.append(f"Budget: {budget_range} {currency}.")

    # Guest counts
    counts = logistics.get("guestCounts") or {}
    if counts:
        total = sum(v for v in counts.values() if isinstance(v, (int, float)))
        if total > 0:
            parts.append(f"Total guests: ~{int(total)}.")

    return " ".join(parts) if parts else "Wedding celebration vendor search."


# ─── Similarity Search ────────────────────────────────────────────────────────

async def find_matching_vendors(
    db: AsyncSession,
    query_vector: list[float],
    vendor_type: str,
    top_k: int = 3,
    offset: int = 0,
) -> list[dict]:
    """
    Find top-k vendors of a specific type using pgvector cosine similarity.
    Returns list of vendor dicts with similarity scores.
    """
    vector_str = "[" + ",".join(str(v) for v in query_vector) + "]"

    sql = text("""
        SELECT id, slug, name, vendor_type, primary_city, primary_region,
               short_description, profile_json, is_preferred,
               1 - (embedding <=> CAST(:vec AS vector)) AS similarity
        FROM vendors
        WHERE vendor_type = :vtype
          AND is_active = true
          AND embedding IS NOT NULL
        ORDER BY embedding <=> CAST(:vec AS vector)
        LIMIT :top_k OFFSET :offset
    """)

    result = await db.execute(sql, {
        "vec": vector_str,
        "vtype": vendor_type,
        "top_k": top_k,
        "offset": offset,
    })
    rows = result.mappings().all()

    vendors = []
    for r in rows:
        profile = r.get("profile_json") or {}
        pricing = profile.get("pricing") or {}

        # Build a compact pricing summary
        pricing_summary = ""
        if isinstance(pricing, dict):
            price_parts = []
            for k in ("veg_price", "non_veg_price", "startingPrice", "min"):
                val = pricing.get(k)
                if val and str(val).strip():
                    label = k.replace("_", " ").title()
                    price_parts.append(f"{label}: {val}")
            if price_parts:
                pricing_summary = ", ".join(price_parts[:2])

        vendors.append({
            "id": str(r["id"]),
            "slug": r["slug"],
            "name": r["name"],
            "vendorType": r["vendor_type"],
            "city": r["primary_city"] or "",
            "region": r["primary_region"] or "",
            "description": (r["short_description"] or "")[:150],
            "pricingSummary": pricing_summary,
            "isPreferred": r["is_preferred"] or False,
            "similarity": round(float(r["similarity"]), 3) if r["similarity"] else 0,
        })

    return vendors


# ─── Available DB Vendor Types ────────────────────────────────────────────────

async def get_available_vendor_types(db: AsyncSession) -> set[str]:
    """Return the set of vendor_type values that exist in the DB with embeddings."""
    sql = text("""
        SELECT DISTINCT vendor_type
        FROM vendors
        WHERE is_active = true AND embedding IS NOT NULL
    """)
    result = await db.execute(sql)
    return {row[0] for row in result.all()}


# ─── Orchestrator ─────────────────────────────────────────────────────────────

async def get_vendor_recommendations(
    db: AsyncSession,
    memory: dict,
    vendor_types: list[str],
    offsets: dict[str, int] | None = None,
    top_k: int = 3,
) -> dict:
    """
    Get top-k vendor recommendations for each vendor type.
    Returns: { "VendorType": { "suggestions": [...], "offset": N, "selected": null } }
    """
    offsets = offsets or {}

    # Build query embedding from user memory
    query_text = build_vendor_query_text(memory)
    query_vector = await embed_text(query_text)

    recommendations: dict = {}

    for vtype in vendor_types:
        offset = offsets.get(vtype, 0)
        try:
            vendors = await find_matching_vendors(
                db, query_vector, vtype, top_k=top_k, offset=offset,
            )
            recommendations[vtype] = {
                "suggestions": vendors,
                "offset": offset + len(vendors),
                "selected": None,
            }
        except Exception as e:
            logger.warning("Vendor search failed for %s: %s", vtype, e)
            recommendations[vtype] = {
                "suggestions": [],
                "offset": 0,
                "selected": None,
            }

    return recommendations


async def save_vendor_recommendations(
    db: AsyncSession,
    session_id: uuid.UUID,
    recommendations: dict,
    memory_version: int,
    request_id: uuid.UUID,
) -> None:
    """
    Save vendor recommendations to the session_vendor_recommendations database table.
    Deletes any existing recommendations for the same session and vendor types being updated.
    """
    import uuid
    from sqlalchemy import select, delete
    from app.models.vendor import Vendor
    from app.models.session_vendor_recommendation import SessionVendorRecommendation

    vtypes = list(recommendations.keys())
    if not vtypes:
        return

    # Delete existing recommendations for this session and the vendor types being updated
    delete_stmt = (
        delete(SessionVendorRecommendation)
        .where(SessionVendorRecommendation.session_id == session_id)
        .where(
            SessionVendorRecommendation.vendor_id.in_(
                select(Vendor.id).where(Vendor.vendor_type.in_(vtypes))
            )
        )
    )
    await db.execute(delete_stmt)

    # Insert new recommendations
    for vtype, data in recommendations.items():
        suggestions = data.get("suggestions") or []
        batch_id = uuid.uuid4()
        for rank, s in enumerate(suggestions):
            rec = SessionVendorRecommendation(
                session_id=session_id,
                vendor_id=uuid.UUID(s["id"]),
                recommendation_batch_id=batch_id,
                rank_order=rank,
                score=s.get("similarity"),
                generated_from_memory_version=memory_version,
                request_id=request_id,
            )
            db.add(rec)
    await db.flush()


async def has_session_vendor_recommendations(
    db: AsyncSession,
    session_id: uuid.UUID,
) -> bool:
    """Check if the session has any vendor recommendations saved in the database."""
    import uuid
    from sqlalchemy import select
    from app.models.session_vendor_recommendation import SessionVendorRecommendation

    stmt = select(SessionVendorRecommendation.id).where(SessionVendorRecommendation.session_id == session_id).limit(1)
    res = await db.execute(stmt)
    return res.scalar_one_or_none() is not None


async def load_vendor_recommendations(
    db: AsyncSession,
    session_id: uuid.UUID,
    memory: dict,
) -> dict:
    """
    Load vendor recommendations from the database for the given session.
    Groups suggestions by vendor_type and formats them for the frontend,
    merging offset and selected information from memory JSON.
    """
    import uuid
    from sqlalchemy import select
    from app.models.vendor import Vendor
    from app.models.session_vendor_recommendation import SessionVendorRecommendation

    stmt = (
        select(SessionVendorRecommendation, Vendor)
        .join(Vendor, SessionVendorRecommendation.vendor_id == Vendor.id)
        .where(SessionVendorRecommendation.session_id == session_id)
        .order_by(SessionVendorRecommendation.rank_order.asc())
    )
    result = await db.execute(stmt)
    rows = result.all()

    recommendations = {}
    for rec, vendor in rows:
        vtype = vendor.vendor_type
        if vtype not in recommendations:
            logistics = memory.get("logistics") or {}
            offset = logistics.get("vendorOffsets", {}).get(vtype, 0)
            selected = logistics.get("vendorSelections", {}).get(vtype)

            recommendations[vtype] = {
                "suggestions": [],
                "offset": offset,
                "selected": selected,
            }

        profile = vendor.profile_json or {}
        pricing = profile.get("pricing") or {}

        pricing_summary = ""
        if isinstance(pricing, dict):
            price_parts = []
            for k in ("veg_price", "non_veg_price", "startingPrice", "min"):
                val = pricing.get(k)
                if val and str(val).strip():
                    label = k.replace("_", " ").title()
                    price_parts.append(f"{label}: {val}")
            if price_parts:
                pricing_summary = ", ".join(price_parts[:2])

        recommendations[vtype]["suggestions"].append({
            "id": str(vendor.id),
            "slug": vendor.slug,
            "name": vendor.name,
            "vendorType": vendor.vendor_type,
            "city": vendor.primary_city or "",
            "region": vendor.primary_region or "",
            "description": (vendor.short_description or "")[:150],
            "pricingSummary": pricing_summary,
            "isPreferred": vendor.is_preferred or False,
            "similarity": round(float(rec.score), 3) if rec.score else 0,
        })

    return recommendations
