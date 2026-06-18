"""Content generation endpoints with SSE streaming."""

import json
import asyncio
from datetime import datetime
from typing import AsyncGenerator
from zoneinfo import ZoneInfo

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
from app.services.internal_links import (
    get_operator_evergreen_link,
    get_picker_candidates,
    get_required_links_for_property,
    suggest_links_for_section,
)
from app.services.bc_core import (
    build_event_context as build_bc_core_event_context,
    build_operator_context,
    summarize_bc_core_context,
)
from app.services.expertise_context import build_expertise_context
from app.services.generation_artifacts import (
    build_source_facts,
    create_generation_run,
    load_generation_run,
)

router = APIRouter()


def _preferences_dict(preferences) -> dict:
    """Normalize optional preference payloads into plain dicts."""
    if not preferences:
        return {}
    try:
        return preferences.model_dump(exclude_none=True)
    except Exception:
        return dict(preferences or {})


def _normalize_game_time(start_time: str | None) -> str:
    """Normalize game time to readable ET text for prompts."""
    if not start_time:
        return ""

    value = start_time.strip()
    if not value:
        return ""

    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))

    dt_et = dt.astimezone(ZoneInfo("America/New_York"))
    hour = dt_et.strftime("%I").lstrip("0") or "12"
    return (
        f"{dt_et.strftime('%A')}, {dt_et.strftime('%B')} {dt_et.day} "
        f"at {hour}:{dt_et.strftime('%M')} {dt_et.strftime('%p')} ET"
    )


def _normalize_article_date(value: str | None) -> str:
    """Normalize an article/event date to long-form ET text when possible."""
    if not value:
        return ""

    raw = value.strip()
    if not raw:
        return ""

    try:
        if len(raw) == 10 and raw[4] == "-" and raw[7] == "-":
            dt = datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=ZoneInfo("America/New_York"))
        else:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=ZoneInfo("UTC"))
            dt = dt.astimezone(ZoneInfo("America/New_York"))
    except ValueError:
        return raw

    return f"{dt.strftime('%A')}, {dt.strftime('%B')} {dt.day}, {dt.year}"


def _build_game_context(game_context) -> tuple[str, str, dict, str]:
    """Build game context text, bet example text, and structured bet-example data."""
    if not game_context:
        return "", "", {}, ""

    parts: list[str] = []
    event_type = str(getattr(game_context, "event_type", "") or "").strip().lower()
    custom_event = str(getattr(game_context, "custom_event", "") or "").strip()
    article_date = _normalize_article_date(
        str(getattr(game_context, "event_date", "") or "").strip()
        or str(getattr(game_context, "start_time", "") or "").strip()
    )
    if custom_event:
        parts.append(f"Featured event: {custom_event}")
    elif game_context.headline and event_type in {"fight", "race", "tournament", "custom", "event"}:
        parts.append(f"Featured event: {game_context.headline}")
    elif game_context.headline:
        parts.append(f"Featured game: {game_context.headline}")
    elif game_context.away_team and game_context.home_team:
        parts.append(f"Featured game: {game_context.away_team} vs {game_context.home_team}")
    if game_context.start_time:
        parts.append(f"Game time: {_normalize_game_time(game_context.start_time)}")
    if game_context.network:
        parts.append(f"Network: {game_context.network}")

    return ". ".join(parts), game_context.bet_example or "", dict(game_context.bet_example_data or {}), article_date


def _serialize_game_context(game_context) -> dict:
    """Serialize structured game context for source-facts and BC Core matching."""
    if not game_context:
        return {}
    return {
        "event_type": str(getattr(game_context, "event_type", "") or "").strip(),
        "custom_event": str(getattr(game_context, "custom_event", "") or "").strip(),
        "event_date": str(getattr(game_context, "event_date", "") or "").strip(),
        "sport": str(getattr(game_context, "sport", "") or "").strip().lower(),
        "away_team": str(getattr(game_context, "away_team", "") or "").strip(),
        "home_team": str(getattr(game_context, "home_team", "") or "").strip(),
        "start_time": str(getattr(game_context, "start_time", "") or "").strip(),
        "network": str(getattr(game_context, "network", "") or "").strip(),
        "headline": str(getattr(game_context, "headline", "") or "").strip(),
    }


async def _enrich_with_bc_core(
    *,
    source_facts: dict,
    event_context: str,
) -> tuple[dict, str]:
    """Attach BC Core context and merge prompt-facing notes into event context."""
    try:
        (operator_context, _), (bc_event_context, _) = await asyncio.gather(
            build_operator_context(source_facts),
            build_bc_core_event_context(source_facts),
        )
        expertise_context, _ = await build_expertise_context(
            source_facts,
            {
                "bc_core_event": bc_event_context,
                "source_urls": bc_event_context.get("source_urls", []),
            },
        )
        source_facts["bc_core"] = {
            "operator": operator_context,
            "event": bc_event_context,
            "expertise": expertise_context,
        }
        bc_notes = summarize_bc_core_context(
            operator_context=operator_context,
            event_context=bc_event_context,
            expertise_context=expertise_context,
        )
        if bc_notes:
            merged = (event_context or "").strip()
            merged = f"{merged}\n\n{bc_notes}" if merged else bc_notes
            return source_facts, merged
    except Exception as exc:
        source_facts["bc_core"] = {
            "operator": {"matched": False, "provider": "fallback", "reason": str(exc)},
            "event": {"matched": False, "provider": "fallback", "reason": str(exc)},
            "expertise": {"matched": False, "provider": "fallback", "reason": str(exc)},
        }
    return source_facts, event_context


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
        offer = await get_offer_by_id_bam(
            request.offer_id,
            property_key=request.offer_property,
            state=request.state,
            market=request.market,
        )
    for alt_id in request.alt_offer_ids or []:
        alt = await get_offer_by_id_bam(
            alt_id,
            property_key=request.offer_property,
            state=request.state,
            market=request.market,
        )
        if alt:
            alt_offers.append(alt)

    competitor_context = ""
    if request.competitor_urls:
        competitor_context = await scrape_competitors(request.competitor_urls, max_chars_per_url=1500)
    game_context_str, bet_example_str, _, article_date = _build_game_context(request.game_context)
    prefs = _preferences_dict(request.article_preferences)
    prefs["market"] = request.market
    source_facts = build_source_facts(
        keyword=request.keyword,
        title=request.title,
        state=request.state,
        market=request.market,
        offer_property=request.offer_property,
        offer=offer,
        alt_offers=alt_offers,
        event_context=game_context_str,
        article_date=article_date,
        bet_example=bet_example_str,
        game_context_data=_serialize_game_context(request.game_context),
        competitor_urls=request.competitor_urls,
        competitor_context=competitor_context,
        article_preferences=prefs,
    )
    source_facts, enriched_event_context = await _enrich_with_bc_core(
        source_facts=source_facts,
        event_context=game_context_str,
    )

    try:
        yield f"data: {json.dumps({'type': 'status', 'message': 'Generating structured outline...'})}\n\n"
        outline_structured = await generate_structured_outline(
            keyword=request.keyword,
            title=request.title,
            offer=offer or {},
            event_context=enriched_event_context,
            article_date=article_date,
            bet_example=bet_example_str,
            competitor_context=competitor_context,
            article_preferences=prefs,
        )
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
        offer_dict = await get_offer_by_id_bam(
            request.offer_id,
            property_key=request.offer_property,
            state=request.state,
            market=request.market,
        )
    for alt_id in request.alt_offer_ids or []:
        alt = await get_offer_by_id_bam(
            alt_id,
            property_key=request.offer_property,
            state=request.state,
            market=request.market,
        )
        if alt:
            alt_offers.append(alt)

    outline = _resolve_outline_from_request(request)
    game_context_str, bet_example_str, bet_example_data, article_date = _build_game_context(request.game_context)
    prefs = _preferences_dict(request.article_preferences)
    prefs["market"] = request.market
    source_facts = build_source_facts(
        keyword=request.keyword,
        title=request.title,
        state=request.state,
        market=request.market,
        offer_property=request.offer_property,
        offer=offer_dict,
        alt_offers=alt_offers,
        event_context=game_context_str,
        article_date=article_date,
        bet_example=bet_example_str,
        bet_example_data=bet_example_data,
        game_context_data=_serialize_game_context(request.game_context),
        article_preferences=prefs,
    )
    source_facts, enriched_event_context = await _enrich_with_bc_core(
        source_facts=source_facts,
        event_context=game_context_str,
    )

    try:
        async for update in generate_draft_from_outline_streaming(
            outline=outline,
            keyword=request.keyword,
            title=request.title,
            offer=offer_dict,
            alt_offers=alt_offers,
            state=request.state,
            offer_property=request.offer_property or "action_network",
            event_context=enriched_event_context,
            article_date=article_date,
            bet_example=bet_example_str,
            bet_example_data=bet_example_data,
            output_format="markdown",
            article_preferences=prefs,
            bc_core_context=source_facts.get("bc_core"),
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
        offer = await get_offer_by_id_bam(
            request.offer_id,
            property_key=request.offer_property,
            state=request.state,
            market=request.market,
        )
    for alt_id in request.alt_offer_ids or []:
        alt = await get_offer_by_id_bam(
            alt_id,
            property_key=request.offer_property,
            state=request.state,
            market=request.market,
        )
        if alt:
            alt_offers.append(alt)

    competitor_context = ""
    if request.competitor_urls:
        competitor_context = await scrape_competitors(request.competitor_urls, max_chars_per_url=1500)
    game_context_str, bet_example_str, _, article_date = _build_game_context(request.game_context)
    prefs = _preferences_dict(request.article_preferences)
    prefs["market"] = request.market
    source_facts = build_source_facts(
        keyword=request.keyword,
        title=request.title,
        state=request.state,
        market=request.market,
        offer_property=request.offer_property,
        offer=offer,
        alt_offers=alt_offers,
        event_context=game_context_str,
        article_date=article_date,
        bet_example=bet_example_str,
        game_context_data=_serialize_game_context(request.game_context),
        competitor_urls=request.competitor_urls,
        competitor_context=competitor_context,
        article_preferences=prefs,
    )
    source_facts, enriched_event_context = await _enrich_with_bc_core(
        source_facts=source_facts,
        event_context=game_context_str,
    )
    artifact_run = create_generation_run(
        keyword=request.keyword,
        title=request.title,
        state=request.state,
        market=request.market,
        offer_property=request.offer_property,
    )
    artifact_run.write_stage(
        "request",
        {
            "request_type": "outline",
            "payload": request.model_dump(exclude_none=True),
        },
        file_name="00_request.json",
    )
    artifact_run.write_stage("source_facts", source_facts, file_name="10_source_facts.json")

    outline_structured = await generate_structured_outline(
        keyword=request.keyword,
        title=request.title,
        offer=offer or {},
        event_context=enriched_event_context,
        article_date=article_date,
        bet_example=bet_example_str,
        competitor_context=competitor_context,
        article_preferences=prefs,
    )
    tokens = structured_to_tokens(outline_structured)
    outline_text = outline_to_text(outline_structured)
    artifact_run.write_stage(
        "outline",
        {
            "outline": tokens,
            "outline_text": outline_text,
            "outline_structured": outline_structured,
        },
        file_name="20_outline.json",
    )

    return {
        "outline": tokens,
        "outline_text": outline_text,
        "outline_structured": outline_structured,
        "source_facts": source_facts,
        **artifact_run.response_meta(),
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
        offer_dict = await get_offer_by_id_bam(
            request.offer_id,
            property_key=request.offer_property,
            state=request.state,
            market=request.market,
        )
    for alt_id in request.alt_offer_ids or []:
        alt = await get_offer_by_id_bam(
            alt_id,
            property_key=request.offer_property,
            state=request.state,
            market=request.market,
        )
        if alt:
            alt_offers.append(alt)

    outline = _resolve_outline_from_request(request)
    game_context_str, bet_example_str, bet_example_data, article_date = _build_game_context(request.game_context)
    prefs = _preferences_dict(request.article_preferences)
    prefs["market"] = request.market
    source_facts = build_source_facts(
        keyword=request.keyword,
        title=request.title,
        state=request.state,
        market=request.market,
        offer_property=request.offer_property,
        offer=offer_dict,
        alt_offers=alt_offers,
        event_context=game_context_str,
        article_date=article_date,
        bet_example=bet_example_str,
        bet_example_data=bet_example_data,
        game_context_data=_serialize_game_context(request.game_context),
        article_preferences=prefs,
    )
    source_facts, enriched_event_context = await _enrich_with_bc_core(
        source_facts=source_facts,
        event_context=game_context_str,
    )
    artifact_run = create_generation_run(
        keyword=request.keyword,
        title=request.title,
        state=request.state,
        offer_property=request.offer_property,
        run_id=request.run_id,
    )
    artifact_run.write_stage(
        "request",
        {
            "request_type": "draft",
            "payload": request.model_dump(exclude_none=True),
        },
        file_name="00_request_draft.json" if request.run_id else "00_request.json",
    )
    artifact_run.write_stage("source_facts", source_facts, file_name="10_source_facts.json")
    artifact_run.write_stage(
        "outline_input",
        {
            "outline_text": request.outline_text,
            "outline_structured": request.outline_structured,
            "outline_tokens": request.outline_tokens,
            "resolved_outline": outline,
        },
        file_name="20_outline.json",
    )

    draft = await generate_draft_from_outline(
        outline=outline,
        keyword=request.keyword,
        title=request.title,
        offer=offer_dict,
        alt_offers=alt_offers,
        state=request.state,
        offer_property=request.offer_property or "action_network",
        event_context=enriched_event_context,
        article_date=article_date,
        bet_example=bet_example_str,
        bet_example_data=bet_example_data,
        output_format="html",
        article_preferences=prefs,
        bc_core_context=source_facts.get("bc_core"),
    )
    artifact_run.write_stage(
        "draft",
        {"draft": draft, "word_count": len(draft.split())},
        file_name="30_draft.json",
    )

    return {
        "draft": draft,
        "word_count": len(draft.split()),
        "source_facts": source_facts,
        **artifact_run.response_meta(),
    }


@router.post("/validate")
async def validate_content_endpoint(
    content: str = Body(..., embed=True),
    market: str = Body("US", embed=True),
    state: str = Body("ALL", embed=True),
    keyword: str | None = Body(None, embed=True),
    offer_id: str | None = Body(None, embed=True),
    offer_property: str | None = Body(None, embed=True),
    run_id: str | None = Body(None, embed=True),
):
    """Validate content for compliance issues."""
    offer_dict = None
    if offer_id:
        offer_dict = await get_offer_by_id_bam(
            offer_id,
            property_key=offer_property,
            state=state,
            market=market,
        )

    result = validate_content_svc(
        content,
        state=state,
        keyword=keyword,
        offer=offer_dict,
    )
    payload = result.to_dict()
    if run_id:
        artifact_run = create_generation_run(
            keyword=keyword or "validation",
            title=keyword or "validation",
            state=state,
            offer_property=offer_property,
            run_id=run_id,
        )
        artifact_run.write_stage(
            "validation",
            {
                "state": state,
                "market": market,
                "keyword": keyword,
                "result": payload,
            },
            file_name="40_validation.json",
        )
        payload.update(artifact_run.response_meta())
    return payload


@router.get("/runs/{run_id}")
async def get_generation_run(run_id: str):
    """Return saved artifact metadata for a generation run."""
    manifest = load_generation_run(run_id)
    if not manifest:
        raise HTTPException(status_code=404, detail="Generation run not found")
    return manifest


@router.get("/link-options")
async def list_link_options(
    property: str | None = None,
    keyword: str = "",
    brand: str = "",
    market: str = "US",
    state: str = "ALL",
    limit: int = 16,
):
    """Return writer-selectable internal link options for the current property."""
    safe_limit = max(6, min(limit, 30))
    suggested = await suggest_links_for_section(
        title=keyword or brand or "article",
        must_include=[keyword, brand],
        k=safe_limit,
        property_key=property,
        brand=brand,
    )
    operator_link = get_operator_evergreen_link(property_key=property, brand=brand)
    required = get_required_links_for_property(property_key=property)
    picker_candidates = get_picker_candidates(property_key=property)

    def _url_key(value: str | None) -> str:
        clean = str(value or "").strip().lower()
        return clean.rstrip("/") if clean.endswith("/") else clean

    links: list[dict] = []
    seen_urls: set[str] = set()
    for link in [*( [operator_link] if operator_link else [] ), *suggested, *required, *picker_candidates]:
        url = str(link.url or "").strip()
        if (market or "").strip().upper() == "CA" and (property or "").strip().lower() == "goal_com":
            if "goal.com/en-ca/" not in url.lower():
                continue
        url_key = _url_key(url)
        if not url_key or url_key in seen_urls:
            continue
        seen_urls.add(url_key)
        links.append(link.to_dict())
        if len(links) >= safe_limit:
            break

    return {
        "property": property or "action_network",
        "count": len(links),
        "links": links,
    }


@router.post("/parse-outline")
async def parse_outline_endpoint(
    text: str = Body(..., embed=True),
):
    """Parse outline text into tokens."""
    tokens = parse_outline_tokens(text)
    return {"tokens": tokens}
