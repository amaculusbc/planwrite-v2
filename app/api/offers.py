"""Offer management endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.offer import Offer
from app.schemas.offer import OfferCreate, OfferResponse
from app.services.offers import sync_offers_from_sheets, get_offers

router = APIRouter()


@router.get("/", response_model=list[OfferResponse])
async def list_offers(
    db: AsyncSession = Depends(get_db),
    state: str | None = None,
    brand: str | None = None,
):
    """List all offers with optional filters.

    Auto-syncs from Google Sheets if cache is expired.
    """
    offers = await get_offers(db, state=state, brand=brand)
    return offers


@router.get("/{offer_id}", response_model=OfferResponse)
async def get_offer(
    offer_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Get a single offer by ID."""
    result = await db.execute(select(Offer).where(Offer.id == offer_id))
    offer = result.scalar_one_or_none()

    if not offer:
        raise HTTPException(status_code=404, detail="Offer not found")

    return offer


@router.post("/", response_model=OfferResponse)
async def create_offer(
    offer: OfferCreate,
    db: AsyncSession = Depends(get_db),
):
    """Create a new offer."""
    db_offer = Offer(**offer.model_dump())
    db.add(db_offer)
    await db.flush()
    await db.refresh(db_offer)
    return db_offer


@router.post("/sync")
async def sync_offers_endpoint(
    db: AsyncSession = Depends(get_db),
    force: bool = False,
):
    """Sync offers from Google Sheets."""
    try:
        count = await sync_offers_from_sheets(db)
        await db.commit()
        return {"status": "success", "synced": count}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.get("/brands/list")
async def list_brands(
    db: AsyncSession = Depends(get_db),
):
    """Get unique list of brands."""
    result = await db.execute(select(Offer.brand).distinct())
    brands = [row[0] for row in result.all() if row[0]]
    return sorted(brands)


@router.get("/states/list")
async def list_states():
    """Get list of supported states."""
    return [
        "ALL", "AZ", "CO", "CT", "DC", "IA", "IL", "IN", "KS", "KY",
        "LA", "MA", "MD", "MI", "NC", "NJ", "NY", "OH", "PA", "TN",
        "VA", "WV",
    ]
