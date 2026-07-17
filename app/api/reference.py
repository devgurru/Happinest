"""Reference data API — event sites and vendors catalog."""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.event_site import EventSite
from app.models.vendor import Vendor
from app.schemas.planner import EventSiteOut, VendorOut

router = APIRouter(prefix="/reference", tags=["Reference Data"])


@router.get("/event-sites", response_model=list[EventSiteOut])
async def list_event_sites(
    site_type: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    q = select(EventSite).where(EventSite.is_active == True)
    if site_type:
        q = q.where(EventSite.site_type == site_type)
    q = q.order_by(EventSite.name)
    result = await db.execute(q)
    return [EventSiteOut.from_orm(s) for s in result.scalars().all()]


@router.get("/event-sites/{site_id}", response_model=EventSiteOut)
async def get_event_site(site_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    site = await db.get(EventSite, site_id)
    if not site or not site.is_active:
        raise HTTPException(status_code=404, detail="Event site not found")
    return EventSiteOut.from_orm(site)


@router.get("/vendors", response_model=list[VendorOut])
async def list_vendors(
    vendor_type: str | None = Query(None),
    city: str | None = Query(None),
    preferred_only: bool = False,
    db: AsyncSession = Depends(get_db),
):
    q = select(Vendor).where(Vendor.is_active == True)
    if vendor_type:
        q = q.where(Vendor.vendor_type == vendor_type)
    if city:
        q = q.where(Vendor.primary_city.ilike(f"%{city}%"))
    if preferred_only:
        q = q.where(Vendor.is_preferred == True)
    q = q.order_by(Vendor.is_preferred.desc(), Vendor.name)
    result = await db.execute(q)
    return [VendorOut.from_orm(v) for v in result.scalars().all()]


@router.get("/vendors/{vendor_id}", response_model=VendorOut)
async def get_vendor(vendor_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    vendor = await db.get(Vendor, vendor_id)
    if not vendor or not vendor.is_active:
        raise HTTPException(status_code=404, detail="Vendor not found")
    return VendorOut.from_orm(vendor)
