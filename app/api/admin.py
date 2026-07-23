"""Admin API — dev-only endpoints for seeding and embedding. Gated by DEBUG=true."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.database import get_db
from app.seeds.seed_runner import run as run_seed
from app.services.ai.embedding_service import generate_and_store_embeddings

router = APIRouter(prefix="/admin", tags=["Admin"])


def _require_debug():
    if not settings.DEBUG:
        raise HTTPException(status_code=403, detail="Admin endpoints are only available in DEBUG mode.")


@router.post("/seed")
async def seed_reference_data(db: AsyncSession = Depends(get_db)):
    """Re-run seed loader (idempotent). DEBUG only."""
    _require_debug()
    # Seed runs its own engine — just confirm it works
    import asyncio
    await asyncio.get_event_loop().run_in_executor(None, lambda: None)
    from app.seeds.seed_runner import seed_event_sites, seed_vendors
    async with db.begin():
        await seed_event_sites(db)
        await seed_vendors(db)
    return {"status": "ok", "message": "Seed complete"}


@router.post("/embed-sites")
async def embed_event_sites(db: AsyncSession = Depends(get_db)):
    """Generate and store embeddings for all event sites. DEBUG only."""
    _require_debug()
    result = await generate_and_store_embeddings(db)
    return {"status": "ok", **result}
