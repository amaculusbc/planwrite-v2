"""Content generation endpoints with SSE streaming."""

import json
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, Body
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.outline import OutlineRequest, DraftRequest
from app.services.outline import (
    generate_structured_outline,
    outline_to_text,
    parse_outline_tokens,
    structured_to_tokens,
    text_to_outline,
)
from app.services.draft import (
    generate_draft_from_outline,
    generate_draft_from_outline_streaming,
)
from app.services.compliance import validate_content as validate_content_svc
from app.services.competitor_scraper import scrape_competitors
from app.services.bam_offers import get_offer_by_id_bam

router = APIRouter()


def _build_game_context(game_context) -> tuple[str, str]:
    """Build game context and bet example strings from request payload."""
    if not game_context:
        return "", ""

    parts: list[str] = []
    if game_context.headline:
        parts.append(f"Featured game: {game_context.headline}")
    elif game_context.away_team and game_context.home_team:
        parts.append(f"Featured game: {game_context.away_team} vs {game_context.home_team}")
    if game_context.start_time:
        parts.append(f"Game time: {game_context.start_time}")
    if game_context.network:
        parts.append(f"Network: {game_context.network}")

    return ". ".join(parts), game_context.bet_example or ""


def _inject_alt_shortcodes(outline: list[dict], alt_offer_count: int) -> list[dict]:
    """Insert [SHORTCODE_1]/[SHORTCODE_2] placeholders for multi-offer modules."""
    if alt_offer_count <= 0:
        return outline

    max_alt = min(alt_offer_count, 2)
    alt_sections = [
        {"level": f"shortcode_{idx}", "title": "", "talking_points": [], "avoid": []}
        for idx in range(1, max_alt + 1)
    ]

    result: list[dict] = []
    inserted = False
    for section in outline:
        result.append(section)
        if not inserted and str(section.get("level", "")) == "shortcode":
            result.extend(alt_sections)
            inserted = True

    if not inserted:
        intro_idx = next((i for i, s in enumerate(result) if str(s.get("level", "")) == "intro"), -1)
        insert_at = intro_idx + 1 if intro_idx >= 0 else 0
        for offset, section in enumerate(alt_sections):
            result.insert(insert_at + offset, section)

    return result


def _resolve_outline_from_request(request: DraftRequest) -> list[dict]:
    """Resolve structured outline from structured/text/token request formats."""
    if request.outline_text:
        parsed = text_to_outline(request.outline_text)
        if parsed:
            return parsed

    if request.outline_structured:
        normalized = []
        for section in request.outline_structured:
            level = str(section.get("level", "h2"))
            normalized.append({
                "level": level,
                "title": str(section.get("title", "")),
                "talking_points": [str(p) for p in section.get("talking_points", []) if str(p).strip()],
                "avoid": [str(a) for a in section.get("avoid", []) if str(a).strip()],
            })
        if normalized:
            return normalized

    if request.outline_tokens:
        parsed = text_to_outline("\n".join(request.outline_tokens))
        if parsed:
            return parsed

    raise HTTPException(
        status_code=422,
        detail="Provide one of: outline_structured, outline_text, or outline_tokens",
    )


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

    competitor_context = ""
    if request.competitor_urls:
        competitor_context = await scrape_competitors(request.competitor_urls, max_chars_per_url=1500)
    game_context_str, bet_example_str = _build_game_context(request.game_context)

    try:
        yield f"data: {json.dumps({'type': 'status', 'message': 'Generating structured outline...'})}\n\n"
        outline_structured = await generate_structured_outline(
            keyword=request.keyword,
            title=request.title,
            offer=offer or {},
            event_context=game_context_str,
            bet_example=bet_example_str,
            competitor_context=competitor_context,
        )
        outline_structured = _inject_alt_shortcodes(outline_structured, len(alt_offers))
        tokens = structured_to_tokens(outline_structured)
        outline_text = outline_to_text(outline_structured)

        for token in tokens:
            yield f"data: {json.dumps({'type': 'token', 'content': token})}\n\n"

        yield f"data: {json.dumps({'type': 'done', 'outline': tokens, 'outline_text': outline_text, 'outline_structured': outline_structured})}\n\n"
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

    outline = _resolve_outline_from_request(request)
    game_context_str, bet_example_str = _build_game_context(request.game_context)

    try:
        async for update in generate_draft_from_outline_streaming(
            outline=outline,
            keyword=request.keyword,
            title=request.title,
            offer=offer_dict,
            alt_offers=alt_offers,
            state=request.state,
            event_context=game_context_str,
            bet_example=bet_example_str,
            output_format="markdown",
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

    competitor_context = ""
    if request.competitor_urls:
        competitor_context = await scrape_competitors(request.competitor_urls, max_chars_per_url=1500)
    game_context_str, bet_example_str = _build_game_context(request.game_context)

    outline_structured = await generate_structured_outline(
        keyword=request.keyword,
        title=request.title,
        offer=offer or {},
        event_context=game_context_str,
        bet_example=bet_example_str,
        competitor_context=competitor_context,
    )
    outline_structured = _inject_alt_shortcodes(outline_structured, len(alt_offers))
    tokens = structured_to_tokens(outline_structured)
    outline_text = outline_to_text(outline_structured)

    return {
        "outline": tokens,
        "outline_text": outline_text,
        "outline_structured": outline_structured,
    }


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

    outline = _resolve_outline_from_request(request)
    game_context_str, bet_example_str = _build_game_context(request.game_context)

    draft = await generate_draft_from_outline(
        outline=outline,
        keyword=request.keyword,
        title=request.title,
        offer=offer_dict,
        alt_offers=alt_offers,
        state=request.state,
        event_context=game_context_str,
        bet_example=bet_example_str,
        output_format="html",
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
