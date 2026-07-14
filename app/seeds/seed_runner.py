"""
Seed runner — idempotent. Checks slug before inserting.
Run: python -m app.seeds.seed_runner
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[3]))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.models.event_site import EventSite
from app.models.vendor import Vendor
from app.seeds.event_sites_seed import EVENT_SITES
from app.seeds.vendors_seed import VENDORS


async def seed_event_sites(session: AsyncSession) -> None:
    inserted = skipped = 0
    for data in EVENT_SITES:
        existing = await session.execute(
            select(EventSite).where(EventSite.slug == data["slug"])
        )
        if existing.scalar_one_or_none():
            skipped += 1
            continue
        site = EventSite(**data)
        session.add(site)
        inserted += 1
    await session.flush()
    print(f"  EventSites: {inserted} inserted, {skipped} skipped")


async def seed_vendors(session: AsyncSession) -> None:
    inserted = skipped = 0
    for data in VENDORS:
        existing = await session.execute(
            select(Vendor).where(Vendor.slug == data["slug"])
        )
        if existing.scalar_one_or_none():
            skipped += 1
            continue
        vendor = Vendor(**data)
        session.add(vendor)
        inserted += 1
    await session.flush()
    print(f"  Vendors:    {inserted} inserted, {skipped} skipped")


async def run() -> None:
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    print("Running seed loader...")
    async with SessionLocal() as session:
        async with session.begin():
            await seed_event_sites(session)
            await seed_vendors(session)
    print("Seed complete.")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(run())
