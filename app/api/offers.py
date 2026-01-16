"""Offer management endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.offer import Offer
from app.schemas.offer import OfferCreate, OfferResponse

router = APIRouter()


@router.get("/", response_model=list[OfferResponse])
async def list_offers(
    db: AsyncSession = Depends(get_db),
    state: str | None = None,
    brand: str | None = None,
):
    """List all offers with optional filters."""
    query = select(Offer).order_by(Offer.brand)

    if brand:
        query = query.where(Offer.brand == brand)

    # State filtering handled in Python since states is JSON
    result = await db.execute(query)
    offers = result.scalars().all()

    if state and state != "ALL":
        offers = [
            o for o in offers
            if state in (o.states or []) or "ALL" in (o.states or [])
        ]

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
async def sync_offers(
    db: AsyncSession = Depends(get_db),
):
    """Sync offers from Google Sheets."""
    # TODO: Implement Google Sheets sync
    # This will be ported from v1's offers_layer.py
    return {"status": "sync not yet implemented"}


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
