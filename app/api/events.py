"""API endpoints for events/games."""

from datetime import datetime
from fastapi import APIRouter, Query
from zoneinfo import ZoneInfo

from app.services.event_fetcher import (
    get_games_for_date,
    get_featured_game,
    format_event_for_prompt,
    format_game_for_dropdown,
    format_game_start_time,
    get_available_sports,
)

router = APIRouter(prefix="/api/events", tags=["events"])


def _build_week_id(season_year: int | None, season_type: str | int | None, week: int | None) -> str | None:
    """Build week id for odds API (e.g., 2025-reg-13, 2025-post-4)."""
    if not season_year or not week:
        return None
    season_key = "reg"
    if isinstance(season_type, str):
        season_type_lower = season_type.lower()
        if "post" in season_type_lower:
            season_key = "post"
        elif "pre" in season_type_lower:
            season_key = "pre"
    elif isinstance(season_type, int):
        if season_type == 3:
            season_key = "post"
        elif season_type == 1:
            season_key = "pre"
    return f"{season_year}-{season_key}-{week}"


@router.get("/sports")
async def list_sports():
    """Get list of available sports."""
    return get_available_sports()


@router.get("/games")
async def list_games(
    sport: str = Query("nfl", description="Sport code (nfl, nba, mlb, nhl, ncaaf, ncaab)"),
    date: str | None = Query(None, description="Date in YYYY-MM-DD format (defaults to today)"),
):
    """Get games for a specific sport and date."""
    target_date = None
    if date:
        try:
            target_date = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=ZoneInfo("America/New_York"))
        except ValueError:
            target_date = None

    games = await get_games_for_date(sport, target_date)

    # Format for dropdown
    return [
        {
            "id": g["id"],
            "label": format_game_for_dropdown(g),
            "home_team": g["home_team"],
            "away_team": g["away_team"],
            "home_abbrev": g.get("home_abbrev", ""),
            "away_abbrev": g.get("away_abbrev", ""),
            "start_time": g["start_time"],
            "start_time_display": format_game_start_time(g),
            "network": g["network"],
            "sport": g["sport"],
            "week": g.get("week"),
            "season_type": g.get("season_type"),
            "season_year": g.get("season_year"),
            "week_id": _build_week_id(g.get("season_year"), g.get("season_type"), g.get("week")),
        }
        for g in games
    ]


@router.get("/featured")
async def get_featured(
    sport: str = Query("nfl", description="Sport code"),
    date: str | None = Query(None, description="Date in YYYY-MM-DD format"),
):
    """Get featured (prime time) game for a sport."""
    target_date = None
    if date:
        try:
            target_date = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=ZoneInfo("America/New_York"))
        except ValueError:
            target_date = None

    game = await get_featured_game(sport, target_date)
    if not game:
        return {"game": None, "context": ""}

    return {
        "game": {
            "id": game["id"],
            "label": format_game_for_dropdown(game),
            "home_team": game["home_team"],
            "away_team": game["away_team"],
            "home_abbrev": game.get("home_abbrev", ""),
            "away_abbrev": game.get("away_abbrev", ""),
            "start_time": game["start_time"],
            "start_time_display": format_game_start_time(game),
            "network": game["network"],
            "sport": game["sport"],
            "week": game.get("week"),
            "season_type": game.get("season_type"),
            "season_year": game.get("season_year"),
            "week_id": _build_week_id(game.get("season_year"), game.get("season_type"), game.get("week")),
        },
        "context": format_event_for_prompt(game),
    }
