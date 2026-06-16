"""API endpoints for on-demand prediction-market lookup."""

from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.services.prediction_markets import (
    PredictionMarketSearch,
    build_prediction_market_example,
    search_prediction_markets,
)

router = APIRouter(prefix="/api/prediction-markets", tags=["prediction-markets"])


@router.get("/search")
async def search_markets(
    sport: str = Query("", description="Sport code from the selected event"),
    away_team: str = Query("", description="Away team name"),
    home_team: str = Query("", description="Home team name"),
    event_name: str = Query("", description="Custom or selected event label"),
    event_date: str = Query("", description="Event date, YYYY-MM-DD"),
    start_time: str = Query("", description="Event start time ISO string"),
    provider: str = Query("all", description="Provider: all, kalshi, polymarket"),
    market_type: str = Query("event", description="Desired market type"),
    limit: int = Query(10, ge=1, le=25, description="Maximum returned matches"),
):
    """Return a small ranked list of markets for the selected event."""
    return await search_prediction_markets(
        PredictionMarketSearch(
            sport=sport,
            away_team=away_team,
            home_team=home_team,
            event_name=event_name,
            event_date=event_date,
            start_time=start_time,
            provider=provider,
            market_type=market_type,
            limit=limit,
        )
    )


class PredictionMarketExampleRequest(BaseModel):
    """Build prompt-ready example text from one selected prediction market."""

    market: dict
    position_amount: float = 25.0
    qualifying_amount: float | None = None
    reward_amount: float | None = None


@router.post("/example")
async def build_example(request: PredictionMarketExampleRequest):
    """Build the example text used in draft prompts."""
    return build_prediction_market_example(
        market=request.market,
        position_amount=request.position_amount,
        qualifying_amount=request.qualifying_amount,
        reward_amount=request.reward_amount,
    )
