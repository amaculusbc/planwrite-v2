"""Offer management endpoints.

Uses BAM API as the primary source for offers.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.offer import Offer
from app.schemas.offer import OfferCreate, OfferResponse
from app.services.bam_offers import (
    get_offers_bam,
    get_offer_by_id_bam,
    get_all_brands,
    fetch_offers_from_bam,
)

router = APIRouter()


@router.get("/")
async def list_offers(
    state: str | None = None,
    brand: str | None = None,
    force_refresh: bool = False,
):
    """List all offers from BAM API with optional filters.

    Args:
        state: Filter by state code (e.g., "NJ", "PA")
        brand: Filter by brand name
        force_refresh: Bypass cache and fetch fresh data
    """
    offers = await get_offers_bam(state=state, brand=brand, force_refresh=force_refresh)
    return offers


@router.get("/brands/list")
async def list_brands():
    """Get unique list of brands from BAM API."""
    brands = await get_all_brands()
    return brands


@router.get("/states/list")
async def list_states():
    """Get list of supported states."""
    return [
        "ALL", "AZ", "CO", "CT", "DC", "IA", "IL", "IN", "KS", "KY",
        "LA", "MA", "MD", "MI", "NC", "NJ", "NY", "OH", "PA", "TN",
        "VA", "WV", "WY",
    ]


@router.post("/sync")
async def sync_offers_endpoint(force: bool = True):
    """Force refresh offers from BAM API."""
    try:
        offers = await fetch_offers_from_bam(force_refresh=force)
        return {"status": "success", "synced": len(offers)}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.get("/{offer_id}")
async def get_offer(offer_id: str):
    """Get a single offer by ID."""
    offer = await get_offer_by_id_bam(offer_id)

    if not offer:
        raise HTTPException(status_code=404, detail="Offer not found")

    return offer
