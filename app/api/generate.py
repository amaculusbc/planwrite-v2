"""Content generation endpoints with SSE streaming."""

import json
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, Body
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.outline import OutlineRequest, DraftRequest
from app.services.outline import (
    generate_outline as gen_outline,
    generate_outline_streaming,
    parse_outline_tokens,
)
from app.services.draft import (
    generate_draft as gen_draft,
    generate_draft_streaming,
)
from app.services.compliance import validate_content as validate_content_svc
from app.services.competitor_scraper import scrape_competitors
from app.services.bam_offers import get_offer_by_id_bam

router = APIRouter()


async def _stream_outline(request: OutlineRequest, db: AsyncSession) -> AsyncGenerator[str, None]:
    """Stream outline generation."""
    offer = None
    alt_offers: list[dict] = []
    if request.offer_id:
        offer = await get_offer_by_id_bam(request.offer_id, property_key=request.offer_property)
    for alt_id in request.alt_offer_ids or []:
        alt = await get_offer_by_id_bam(alt_id, property_key=request.offer_property)
        if alt:
            alt_offers.append(alt)

    brand = offer.get("brand", "") if offer else ""
    offer_text = offer.get("offer_text", "") if offer else ""

    # Parse competitor URLs if provided
    competitor_context = ""
    if request.competitor_urls:
        competitor_context = await scrape_competitors(request.competitor_urls, max_chars_per_url=1500)

    try:
        async for update in generate_outline_streaming(
            keyword=request.keyword,
            title=request.title,
            offer_text=offer_text,
            brand=brand,
            state=request.state,
            competitor_context=competitor_context,
            num_offers=(1 + len(alt_offers)) if offer else 1,
        ):
            yield f"data: {json.dumps(update)}\n\n"
    except Exception as e:
        yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"


async def _stream_draft(request: DraftRequest, db: AsyncSession) -> AsyncGenerator[str, None]:
    """Stream draft generation."""
    offer_dict = None
    alt_offers: list[dict] = []
    if request.offer_id:
        offer_dict = await get_offer_by_id_bam(request.offer_id, property_key=request.offer_property)
    for alt_id in request.alt_offer_ids or []:
        alt = await get_offer_by_id_bam(alt_id, property_key=request.offer_property)
        if alt:
            alt_offers.append(alt)

    try:
        async for update in generate_draft_streaming(
            outline_tokens=request.outline_tokens,
            keyword=request.keyword,
            title=request.title,
            offer=offer_dict,
            alt_offers=alt_offers,
            state=request.state,
        ):
            yield f"data: {json.dumps(update)}\n\n"
    except Exception as e:
        yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"


@router.post("/outline")
async def generate_outline_endpoint(
    request: OutlineRequest,
    db: AsyncSession = Depends(get_db),
):
    """Generate article outline with streaming SSE response."""
    return StreamingResponse(
        _stream_outline(request, db),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/outline/sync")
async def generate_outline_sync(
    request: OutlineRequest,
    db: AsyncSession = Depends(get_db),
):
    """Generate outline synchronously (non-streaming)."""
    offer = None
    alt_offers: list[dict] = []
    if request.offer_id:
        offer = await get_offer_by_id_bam(request.offer_id, property_key=request.offer_property)
    for alt_id in request.alt_offer_ids or []:
        alt = await get_offer_by_id_bam(alt_id, property_key=request.offer_property)
        if alt:
            alt_offers.append(alt)

    brand = offer.get("brand", "") if offer else ""
    offer_text = offer.get("offer_text", "") if offer else ""

    competitor_context = ""
    if request.competitor_urls:
        competitor_context = await scrape_competitors(request.competitor_urls, max_chars_per_url=1500)

    # Build game context string if provided
    game_context_str = ""
    if request.game_context:
        gc = request.game_context
        parts = []
        if gc.headline:
            parts.append(f"Featured game: {gc.headline}")
        elif gc.away_team and gc.home_team:
            parts.append(f"Featured game: {gc.away_team} vs {gc.home_team}")
        if gc.start_time:
            parts.append(f"Game time: {gc.start_time}")
        if gc.network:
            parts.append(f"Network: {gc.network}")
        if gc.bet_example:
            parts.append(f"Bet example: {gc.bet_example}")
        game_context_str = ". ".join(parts)

    tokens = await gen_outline(
        keyword=request.keyword,
        title=request.title,
        offer_text=offer_text,
        brand=brand,
        state=request.state,
        competitor_context=competitor_context,
        game_context=game_context_str,
        num_offers=(1 + len(alt_offers)) if offer else 1,
    )

    return {"outline": tokens}


@router.post("/draft")
async def generate_draft_endpoint(
    request: DraftRequest,
    db: AsyncSession = Depends(get_db),
):
    """Generate article draft with streaming SSE response."""
    return StreamingResponse(
        _stream_draft(request, db),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/draft/sync")
async def generate_draft_sync(
    request: DraftRequest,
    db: AsyncSession = Depends(get_db),
):
    """Generate draft synchronously (non-streaming)."""
    offer_dict = None
    alt_offers: list[dict] = []
    if request.offer_id:
        offer_dict = await get_offer_by_id_bam(request.offer_id, property_key=request.offer_property)
    for alt_id in request.alt_offer_ids or []:
        alt = await get_offer_by_id_bam(alt_id, property_key=request.offer_property)
        if alt:
            alt_offers.append(alt)

    # Build game context string if provided
    game_context_str = ""
    bet_example_str = ""
    if request.game_context:
        gc = request.game_context
        parts = []
        if gc.headline:
            parts.append(f"Featured game: {gc.headline}")
        elif gc.away_team and gc.home_team:
            parts.append(f"Featured game: {gc.away_team} vs {gc.home_team}")
        if gc.start_time:
            parts.append(f"Game time: {gc.start_time}")
        if gc.network:
            parts.append(f"Network: {gc.network}")
        game_context_str = ". ".join(parts)
        # Keep bet_example separate for use in "How to Claim" sections
        if gc.bet_example:
            bet_example_str = gc.bet_example

    draft = await gen_draft(
        outline_tokens=request.outline_tokens,
        keyword=request.keyword,
        title=request.title,
        offer=offer_dict,
        alt_offers=alt_offers,
        state=request.state,
        game_context=game_context_str,
        bet_example=bet_example_str,
    )

    return {"draft": draft, "word_count": len(draft.split())}


@router.post("/validate")
async def validate_content_endpoint(
    content: str = Body(..., embed=True),
    state: str = Body("ALL", embed=True),
    keyword: str | None = Body(None, embed=True),
    offer_id: str | None = Body(None, embed=True),
    offer_property: str | None = Body(None, embed=True),
):
    """Validate content for compliance issues."""
    offer_dict = None
    if offer_id:
        offer_dict = await get_offer_by_id_bam(offer_id, property_key=offer_property)

    result = validate_content_svc(
        content,
        state=state,
        keyword=keyword,
        offer=offer_dict,
    )
    return result.to_dict()


@router.post("/parse-outline")
async def parse_outline_endpoint(
    text: str = Body(..., embed=True),
):
    """Parse outline text into tokens."""
    tokens = parse_outline_tokens(text)
    return {"tokens": tokens}
