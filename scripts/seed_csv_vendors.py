"""
Seed CSV Vendors Script — Importer and Embedder for vendor_data_form_db.csv.
Parses ~7,431 vendor records across all categories (Venue, Photographer, Caterer,
Invitation Vendor, DJ, Gift Vendor, Cake, etc.), structures profile_json, generates
768-dim vector embeddings in BATCHES (2000 per API call), and performs idempotent
upserts into PostgreSQL.

Run: PYTHONPATH=backend python backend/scripts/seed_csv_vendors.py
"""
import asyncio
import json
import os
import re
import sys
import uuid
from pathlib import Path

# Add backend directory to PYTHONPATH
BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.models.vendor import Vendor
from app.services.ai.embedding_service import embed_texts_batch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clean_str(val: object) -> str:
    if val is None or pd.isna(val):
        return ""
    s = str(val).strip()
    return "" if s.lower() in ("nan", "none", "unknown", "null") else s


def _clean_bool(val: object) -> bool:
    if val is None or pd.isna(val):
        return False
    if isinstance(val, bool):
        return val
    s = str(val).strip().lower()
    return s in ("true", "1", "yes", "y")


def _slugify(text: str) -> str:
    s = text.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_-]+", "-", s)
    s = s.strip("-")
    return s[:85].rstrip("-")


def parse_json_field(val: object) -> object:
    if val is None or pd.isna(val):
        return None
    if isinstance(val, (dict, list)):
        return val
    s = str(val).strip()
    if not s or s.lower() == "nan":
        return None
    try:
        return json.loads(s)
    except Exception:
        return s


# ---------------------------------------------------------------------------
# CSV Parser
# ---------------------------------------------------------------------------

def parse_vendor_csv(file_path: str, existing_map: dict = None, all_slugs_in_use: set = None) -> list[dict]:
    if existing_map is None:
        existing_map = {}
    if all_slugs_in_use is None:
        all_slugs_in_use = set()

    df = pd.read_csv(file_path)
    records = []
    seen_slugs: set[str] = set()

    for idx, row in df.iterrows():
        raw_v_id = row.get("vendor_id")
        if pd.isna(raw_v_id):
            continue
        try:
            raw_v_id = int(raw_v_id)
        except (ValueError, TypeError):
            continue

        raw_name = _clean_str(row.get("vendor_name")) or f"Vendor {raw_v_id}"
        cat_name = _clean_str(row.get("category_name")) or "Vendor"
        city = _clean_str(row.get("city")) or "Delhi NCR"
        state = _clean_str(row.get("state"))
        country = _clean_str(row.get("country")) or "India"

        # Combine region
        if state and state != city:
            region = f"{state}, {country}"
        else:
            region = country

        # Determine ID and Slug
        if (raw_v_id, cat_name) in existing_map:
            vendor_uuid, slug = existing_map[(raw_v_id, cat_name)]
        else:
            raw_slug = _clean_str(row.get("slug")) or _slugify(raw_name)
            cat_slug_part = cat_name.lower().replace(" ", "-").replace("/", "-")
            base_slug = _slugify(f"{raw_slug}-{cat_slug_part}")
            if not base_slug:
                base_slug = f"vendor-{raw_v_id}"

            slug = base_slug
            counter = 1
            while slug in all_slugs_in_use or slug in seen_slugs:
                slug = f"{base_slug[:80]}-{counter}"
                counter += 1
            seen_slugs.add(slug)

            # Deterministic UUID primary key
            vendor_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, f"vendor-{raw_v_id}-{slug}")

        desc = _clean_str(row.get("description")) or f"{raw_name} is a premier {cat_name} vendor serving {city}, {region}."
        is_premium = _clean_bool(row.get("is_premium"))

        # Parse JSON fields
        tags_data = parse_json_field(row.get("tags"))
        pricing_data = parse_json_field(row.get("pricing"))
        category_data = parse_json_field(row.get("category_data"))

        profile_json = {
            "vendor_id": raw_v_id,
            "tags": tags_data,
            "pricing": pricing_data,
            "category_data": category_data,
            "city": city,
            "state": state,
            "country": country,
        }

        # Build search text for vector embedding
        search_parts = [f"{cat_name}: {raw_name} in {city}, {region}."]
        if desc:
            search_parts.append(f"Description: {desc[:250]}")

        if isinstance(tags_data, list) and tags_data:
            search_parts.append(f"Tags: {', '.join(str(t) for t in tags_data)}.")

        if isinstance(pricing_data, dict):
            p_items = [f"{k}: {v}" for k, v in pricing_data.items() if v and str(v).strip()]
            if p_items:
                search_parts.append(f"Pricing: {', '.join(p_items[:4])}.")

        if isinstance(category_data, dict):
            c_items = []
            for k in ("usp", "address", "inhouse_catering", "outside_catering_allowed", "coreSpeciality"):
                if k in category_data and category_data[k] is not None:
                    c_items.append(f"{k}: {category_data[k]}")
            if c_items:
                search_parts.append(f"Details: {', '.join(c_items)}.")

        search_text = " ".join(search_parts)

        records.append({
            "id": vendor_uuid,
            "slug": slug,
            "name": raw_name,
            "vendor_type": cat_name,
            "primary_city": city,
            "primary_region": region,
            "short_description": desc,
            "profile_json": profile_json,
            "rating_summary_json": {"rating": 4.5, "review_count": 10},
            "is_preferred": is_premium,
            "is_active": True,
            "seed_version": "v1.0-csv",
            "search_text": search_text,
        })

    return records


# ---------------------------------------------------------------------------
# Seeder with BATCH embedding (2000 texts per API call)
# ---------------------------------------------------------------------------

async def seed_csv_vendors(csv_path: str | None = None, force_update: bool = False) -> dict:
    # ---- Resolve CSV path ----
    if not csv_path:
        candidates = []
        # Scan any .csv in vendor_seed_data folders
        for base in [BACKEND_DIR, BACKEND_DIR.parent]:
            seed_dir = os.path.join(base, "vendor_seed_data")
            if os.path.isdir(seed_dir):
                csv_files = [
                    os.path.join(seed_dir, f)
                    for f in os.listdir(seed_dir)
                    if f.endswith(".csv")
                ]
                # Sort alphabetically in reverse so "v2" comes before "form"
                csv_files.sort(key=lambda x: os.path.basename(x), reverse=True)
                candidates.extend(csv_files)

        # Fallback default candidates
        candidates.extend([
            os.path.join(BACKEND_DIR, "vendor_seed_data", "vendor_data_form_db.csv"),
            os.path.join(BACKEND_DIR.parent, "vendor_seed_data", "vendor_data_form_db.csv"),
            os.path.join(BACKEND_DIR.parent, "vendor_data_form_db.csv"),
        ])

        for cand in candidates:
            if os.path.exists(cand):
                csv_path = cand
                break

    if not csv_path or not os.path.exists(csv_path):
        print(f"ERROR: CSV file not found at {csv_path}", flush=True)
        return

    # ---- DB setup ----
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        from sqlalchemy import text
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
        await conn.execute(text("ALTER TABLE vendors ADD COLUMN IF NOT EXISTS embedding vector(768);"))

    SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # ---- Pre-fetch existing vendors and slugs for O(1) mapping and lookup ----
    async with SessionLocal() as session:
        result = await session.execute(select(Vendor.id, Vendor.slug, Vendor.profile_json, Vendor.vendor_type))
        rows = result.all()
        existing_ids = {row[0] for row in rows}
        
        existing_map = {}
        all_slugs_in_use = set()
        for row in rows:
            v_id = None
            if isinstance(row.profile_json, dict):
                v_id = row.profile_json.get("vendor_id")
            elif isinstance(row.profile_json, str):
                try:
                    p = json.loads(row.profile_json)
                    v_id = p.get("vendor_id")
                except Exception:
                    pass
            if v_id is not None:
                try:
                    v_id = int(v_id)
                    existing_map[(v_id, row.vendor_type)] = (row.id, row.slug)
                except (ValueError, TypeError):
                    pass
            if row.slug:
                all_slugs_in_use.add(row.slug)

    print(f"Parsing vendors from {csv_path}...", flush=True)
    vendors_data = parse_vendor_csv(csv_path, existing_map=existing_map, all_slugs_in_use=all_slugs_in_use)
    print(f"Successfully parsed {len(vendors_data)} CSV vendor records.", flush=True)

    # ---- Separate new vs existing, skip already-embedded ----
    to_insert = []
    to_update = []
    skipped = 0

    for data in vendors_data:
        v_id = data["id"]
        if v_id in existing_ids and not force_update:
            skipped += 1
        elif v_id in existing_ids:
            to_update.append(data)
        else:
            to_insert.append(data)

    print(f"  {len(to_insert)} to insert, {len(to_update)} to update, {skipped} skipped (already exist with embeddings).", flush=True)

    total_needing_embed = to_insert + to_update
    if not total_needing_embed:
        print(f"\nSUCCESS: Nothing to do! 0 inserted, 0 updated, {skipped} skipped.", flush=True)
        await engine.dispose()
        return {"total": len(vendors_data), "inserted": 0, "updated": 0, "skipped": skipped, "embedded": 0}

    # ---- BATCH embed all search texts at once (2000 per API call) ----
    search_texts = [d["search_text"] for d in total_needing_embed]
    BATCH_SIZE = 2000
    print(f"  Generating embeddings for {len(search_texts)} vendors in batches of {BATCH_SIZE}...", flush=True)

    all_embeddings = await embed_texts_batch(search_texts, batch_size=BATCH_SIZE)
    print(f"  ✓ All {len(all_embeddings)} embeddings generated!", flush=True)

    # ---- Assign embeddings back ----
    for data, emb in zip(total_needing_embed, all_embeddings):
        data["_embedding"] = emb

    # ---- Insert new vendors in DB batches ----
    inserted = 0
    DB_BATCH = 500

    if to_insert:
        print(f"  Inserting {len(to_insert)} new vendors into database...", flush=True)
        for batch_start in range(0, len(to_insert), DB_BATCH):
            batch = to_insert[batch_start : batch_start + DB_BATCH]
            async with SessionLocal() as session:
                async with session.begin():
                    for data in batch:
                        vendor = Vendor(
                            id=data["id"],
                            slug=data["slug"],
                            name=data["name"],
                            vendor_type=data["vendor_type"],
                            primary_city=data["primary_city"],
                            primary_region=data["primary_region"],
                            short_description=data["short_description"],
                            profile_json=data["profile_json"],
                            rating_summary_json=data["rating_summary_json"],
                            is_preferred=data["is_preferred"],
                            is_active=data["is_active"],
                            seed_version=data["seed_version"],
                            embedding=data["_embedding"],
                        )
                        session.add(vendor)
                        inserted += 1
            print(f"    Inserted {min(batch_start + DB_BATCH, len(to_insert))}/{len(to_insert)}...", flush=True)

    # ---- Update existing vendors ----
    updated = 0
    if to_update:
        print(f"  Updating {len(to_update)} existing vendors...", flush=True)
        for batch_start in range(0, len(to_update), DB_BATCH):
            batch = to_update[batch_start : batch_start + DB_BATCH]
            async with SessionLocal() as session:
                async with session.begin():
                    batch_ids = [d["id"] for d in batch]
                    result = await session.execute(select(Vendor).where(Vendor.id.in_(batch_ids)))
                    vendor_map = {v.id: v for v in result.scalars().all()}

                    for data in batch:
                        vendor = vendor_map.get(data["id"])
                        if not vendor:
                            continue
                        vendor.slug = data["slug"]
                        vendor.name = data["name"]
                        vendor.vendor_type = data["vendor_type"]
                        vendor.primary_city = data["primary_city"]
                        vendor.primary_region = data["primary_region"]
                        vendor.short_description = data["short_description"]
                        vendor.profile_json = data["profile_json"]
                        vendor.is_preferred = data["is_preferred"]
                        vendor.seed_version = data["seed_version"]
                        vendor.embedding = data["_embedding"]
                        updated += 1
            print(f"    Updated {min(batch_start + DB_BATCH, len(to_update))}/{len(to_update)}...", flush=True)

    await engine.dispose()
    print(f"\nSUCCESS: Seeding complete! {inserted} inserted, {updated} updated, {skipped} skipped (Total: {len(vendors_data)}).", flush=True)
    return {"total": len(vendors_data), "inserted": inserted, "updated": updated, "skipped": skipped, "embedded": len(all_embeddings)}


if __name__ == "__main__":
    asyncio.run(seed_csv_vendors())
