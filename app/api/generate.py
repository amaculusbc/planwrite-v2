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
from app.services.bam_offers import get_offer_by_id_bam

router = APIRouter()


async def _stream_outline(request: OutlineRequest, db: AsyncSession) -> AsyncGenerator[str, None]:
    """Stream outline generation."""
    # Get offer details from BAM API
    offer = None
    if request.offer_id:
        offer = await get_offer_by_id_bam(request.offer_id)

    brand = offer.get("brand", "") if offer else ""
    offer_text = offer.get("offer_text", "") if offer else ""

    # Parse competitor URLs if provided
    competitor_context = ""
    if request.competitor_urls:
        competitor_context = f"Competitor URLs to reference: {', '.join(request.competitor_urls)}"

    try:
        async for update in generate_outline_streaming(
            keyword=request.keyword,
            title=request.title,
            offer_text=offer_text,
            brand=brand,
            state=request.state,
            competitor_context=competitor_context,
        ):
            yield f"data: {json.dumps(update)}\n\n"
    except Exception as e:
        yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"


async def _stream_draft(request: DraftRequest, db: AsyncSession) -> AsyncGenerator[str, None]:
    """Stream draft generation."""
    # Get offer details from BAM API
    offer_dict = None
    if request.offer_id:
        offer = await get_offer_by_id_bam(request.offer_id)
        if offer:
            offer_dict = {
                "brand": offer.get("brand", ""),
                "offer_text": offer.get("offer_text", ""),
                "bonus_code": offer.get("bonus_code", ""),
                "switchboard_link": offer.get("switchboard_link", ""),
                "terms": offer.get("terms", ""),
                "shortcode": offer.get("shortcode", ""),
            }

    try:
        async for update in generate_draft_streaming(
            outline_tokens=request.outline_tokens,
            keyword=request.keyword,
            title=request.title,
            offer=offer_dict,
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
    # Get offer details from BAM API
    offer = None
    if request.offer_id:
        offer = await get_offer_by_id_bam(request.offer_id)

    brand = offer.get("brand", "") if offer else ""
    offer_text = offer.get("offer_text", "") if offer else ""

    competitor_context = ""
    if request.competitor_urls:
        competitor_context = f"Competitor URLs: {', '.join(request.competitor_urls)}"

    tokens = await gen_outline(
        keyword=request.keyword,
        title=request.title,
        offer_text=offer_text,
        brand=brand,
        state=request.state,
        competitor_context=competitor_context,
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
    # Get offer details from BAM API
    offer_dict = None
    if request.offer_id:
        offer = await get_offer_by_id_bam(request.offer_id)
        if offer:
            offer_dict = {
                "brand": offer.get("brand", ""),
                "offer_text": offer.get("offer_text", ""),
                "bonus_code": offer.get("bonus_code", ""),
                "switchboard_link": offer.get("switchboard_link", ""),
                "terms": offer.get("terms", ""),
                "shortcode": offer.get("shortcode", ""),
            }

    draft = await gen_draft(
        outline_tokens=request.outline_tokens,
        keyword=request.keyword,
        title=request.title,
        offer=offer_dict,
        state=request.state,
    )

    return {"draft": draft, "word_count": len(draft.split())}


@router.post("/validate")
async def validate_content_endpoint(
    content: str = Body(..., embed=True),
    state: str = Body("ALL", embed=True),
):
    """Validate content for compliance issues."""
    result = validate_content_svc(content, state=state)
    return result.to_dict()


@router.post("/parse-outline")
async def parse_outline_endpoint(
    text: str = Body(..., embed=True),
):
    """Parse outline text into tokens."""
    tokens = parse_outline_tokens(text)
    return {"tokens": tokens}
