"""Admin API — dev-only endpoints for seeding and embedding. Gated by DEBUG=true."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.database import get_db
from app.services.ai.embedding_service import generate_and_store_embeddings

router = APIRouter(prefix="/admin", tags=["Admin"])


def _require_debug():
    if not settings.DEBUG:
        raise HTTPException(status_code=403, detail="Admin endpoints are only available in DEBUG mode.")


@router.post("/seed")
async def seed_reference_data(db: AsyncSession = Depends(get_db)):
    """Re-run seed loader (idempotent). DEBUG only."""
    _require_debug()
    from app.seeds.seed_runner import seed_event_sites, seed_vendors
    async with db.begin():
        await seed_event_sites(db)
        await seed_vendors(db)
    return {"status": "ok", "message": "Seed complete"}


@router.post("/seed-csv-vendors")
async def seed_csv_vendors_endpoint(force_update: bool = False):
    """Re-run CSV Vendors importer (idempotent). from live db csv data """
    _require_debug()
    import sys
    from pathlib import Path
    backend_dir = Path(__file__).resolve().parents[2]
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    from scripts.seed_csv_vendors import seed_csv_vendors
    result = await seed_csv_vendors(force_update=force_update)
    return {"status": "ok", **result}


@router.post("/embed-sites")
async def embed_event_sites(db: AsyncSession = Depends(get_db)):
    """Generate and store embeddings for all event sites. DEBUG only."""
    _require_debug()
    result = await generate_and_store_embeddings(db)
    return {"status": "ok", **result}
