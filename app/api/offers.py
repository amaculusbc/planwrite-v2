"""Offer management endpoints.

BAM API only.
"""

from fastapi import APIRouter, HTTPException, Query

from app.services.bam_offers import (
    fetch_offers_from_bam,
    get_all_brands,
    get_offer_by_id_bam,
    get_offers_bam,
    get_available_properties,
)

router = APIRouter()


@router.get("/")
async def list_offers(
    state: str | None = None,
    brand: str | None = None,
    force_refresh: bool = False,
    property: str | None = Query(None, description="BAM property key"),
):
    """List offers from BAM API with optional filters.

    Args:
        state: Filter by state code (e.g., "NJ", "PA")
        brand: Filter by brand name
        force_refresh: Bypass cache and fetch fresh data
        property: BAM property key (if source=bam)
    """
    return await get_offers_bam(
        state=state,
        brand=brand,
        force_refresh=force_refresh,
        property_key=property,
    )


@router.get("/brands/list")
async def list_brands_endpoint(
    property: str | None = Query(None, description="BAM property key"),
):
    """Get unique list of brands for BAM."""
    brands = await get_all_brands(property_key=property)
    return brands


@router.get("/properties/list")
async def list_properties_endpoint():
    """Get list of available BAM properties."""
    return {"properties": get_available_properties()}


@router.get("/states/list")
async def list_states():
    """Get list of supported states."""
    return [
        "ALL", "AZ", "CO", "CT", "DC", "IA", "IL", "IN", "KS", "KY",
        "LA", "MA", "MD", "MI", "NC", "NJ", "NY", "OH", "PA", "TN",
        "VA", "WV", "WY",
    ]


@router.post("/sync")
async def sync_offers_endpoint(
    force: bool = True,
    property: str | None = Query(None, description="BAM property key"),
):
    """Force refresh offers from BAM API."""
    try:
        offers = await fetch_offers_from_bam(force_refresh=force, property_key=property)
        return {"status": "success", "synced": len(offers)}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.get("/{offer_id}")
async def get_offer(
    offer_id: str,
    property: str | None = Query(None, description="BAM property key"),
):
    """Get a single offer by ID."""
    offer = await get_offer_by_id_bam(offer_id, property_key=property)

    if not offer:
        raise HTTPException(status_code=404, detail="Offer not found")

    return offer
