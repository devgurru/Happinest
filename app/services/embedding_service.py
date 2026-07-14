"""
Embedding Service — generates text embeddings via Ollama nomic-embed-text.
Used at S6 (direction synthesis) to find matching event sites via cosine similarity.
"""
import json

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.event_site import EventSite


async def embed_text(text_input: str) -> list[float]:
    """Call Ollama nomic-embed-text and return the embedding vector."""
    url = f"{settings.OLLAMA_BASE_URL}/api/embeddings"
    payload = {
        "model": settings.OLLAMA_EMBEDDING_MODEL,
        "prompt": text_input,
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()
        return data["embedding"]


def build_memory_search_text(memory: dict) -> str:
    """
    Flatten relevant memory sections into a single search string
    for embedding-based event site matching.
    """
    parts = []

    identity = memory.get("identity", {})
    occasion = memory.get("occasion", {})
    personality = memory.get("personality", {})
    vibe = memory.get("vibe", {})

    if place := occasion.get("place"):
        parts.append(f"Wedding in {place}")
    if setting := occasion.get("settingPreference"):
        parts.append(f"Setting: {setting}")
    if destination := occasion.get("destinationMode"):
        parts.append(f"Destination mode: {destination}")

    if tags := personality.get("tags"):
        parts.append(f"Couple personality: {', '.join(tags)}")
    if cultural := personality.get("culturalSignals"):
        parts.append(f"Cultural background: {', '.join(cultural)}")
    if interp := personality.get("plannerInterpretation"):
        parts.append(interp)

    if primary_vibe := vibe.get("primaryVibe"):
        parts.append(f"Primary vibe: {primary_vibe}")
    if secondary := vibe.get("secondaryVibes"):
        parts.append(f"Secondary vibes: {', '.join(secondary)}")
    if energy := vibe.get("energyLevel"):
        parts.append(f"Energy: {energy}")
    if formality := vibe.get("formality"):
        parts.append(f"Formality: {formality}")
    if vibe_interp := vibe.get("plannerInterpretation"):
        parts.append(vibe_interp)

    return ". ".join(parts) if parts else "Wedding celebration"


async def find_matching_event_sites(
    db: AsyncSession,
    memory: dict,
    top_k: int = 5,
) -> list[dict]:
    """
    Use pgvector cosine similarity to find the top_k event sites
    that best match the current planner memory context.
    Falls back to returning top_k sites by insertion order if no embeddings exist.
    """
    search_text = build_memory_search_text(memory)
    query_vector = await embed_text(search_text)

    # Use pgvector cosine distance operator <=>
    vector_str = "[" + ",".join(str(v) for v in query_vector) + "]"
    sql = text(
        """
        SELECT id, slug, name, site_type, short_description, profile_json,
               1 - (embedding <=> CAST(:vec AS vector)) AS similarity
        FROM event_sites
        WHERE is_active = true AND embedding IS NOT NULL
        ORDER BY embedding  <=> CAST(:vec AS vector)
        LIMIT :top_k
        """
    )
    result = await db.execute(sql, {"vec": vector_str, "top_k": top_k})
    rows = result.mappings().all()

    if not rows:
        # Fallback: no embeddings yet — return first top_k active sites
        fallback = await db.execute(
            select(EventSite)
            .where(EventSite.is_active == True)
            .limit(top_k)
        )
        sites = list(fallback.scalars().all())
        return [
            {
                "id": str(s.id),
                "slug": s.slug,
                "name": s.name,
                "site_type": s.site_type,
                "short_description": s.short_description,
                "profile_json": s.profile_json,
                "similarity": None,
            }
            for s in sites
        ]

    return [dict(r) for r in rows]


async def generate_and_store_embeddings(db: AsyncSession) -> dict:
    """
    Generate and store embeddings for all event sites that don't have one yet.
    Called by admin endpoint or seed_embed script.
    """
    result = await db.execute(
        select(EventSite).where(EventSite.is_active == True)
    )
    sites = list(result.scalars().all())

    updated = 0
    skipped = 0
    errors = 0

    for site in sites:
        if site.embedding is not None:
            skipped += 1
            continue

        # Build search text from site profile
        p = site.profile_json or {}
        text_parts = [
            site.name,
            site.short_description,
            site.site_type,
            " ".join(p.get("styleTags", [])),
            " ".join(p.get("vibeTags", [])),
            " ".join(p.get("culturalSignals", [])),
            " ".join(p.get("audienceFit", [])),
            " ".join(p.get("narrativeSignals", [])),
            p.get("plannerInterpretation", ""),
        ]
        embed_input = ". ".join(t for t in text_parts if t)

        try:
            vector = await embed_text(embed_input)
            site.embedding = vector
            db.add(site)
            await db.flush()
            updated += 1
        except Exception as e:
            errors += 1
            print(f"  ERROR embedding {site.slug}: {e}")

    return {"updated": updated, "skipped": skipped, "errors": errors}
