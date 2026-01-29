"""API endpoints for odds data."""

from datetime import datetime
from fastapi import APIRouter, Query
from pydantic import BaseModel
from zoneinfo import ZoneInfo

from app.services.odds_fetcher import (
    OddsFetcher,
    build_bet_options,
    build_bet_example_text,
    calculate_profit,
)

router = APIRouter(prefix="/api/odds", tags=["odds"])


@router.get("/game")
async def get_game_odds(
    sport: str = Query("nfl", description="Sport code"),
    away_team: str = Query(..., description="Away team name or abbreviation"),
    home_team: str = Query(..., description="Home team name or abbreviation"),
    sportsbook: str = Query("draftkings", description="Sportsbook key"),
    week: str | None = Query(None, description="NFL week (e.g., 2025-reg-13)"),
    game_date: str | None = Query(None, description="Date for daily sports (YYYY-MM-DD)"),
):
    """Get odds for a specific game.

    Returns odds from multiple sportsbooks in a format suitable for UI display.
    """
    fetcher = OddsFetcher(sport=sport)

    target_date = None
    if game_date:
        try:
            target_date = datetime.strptime(game_date, "%Y-%m-%d").replace(tzinfo=ZoneInfo("America/New_York"))
        except ValueError:
            pass

    game = await fetcher.find_game_by_teams(
        away_team=away_team,
        home_team=home_team,
        week=week,
        target_date=target_date,
    )

    if not game:
        return {"error": "Game not found", "spreads": None, "moneylines": None, "totals": None}

    # Get odds from multiple books - use raw data for structured response
    available_books = fetcher.get_available_sportsbooks(game)
    spreads = {}
    moneylines = {}
    totals = {}

    for book in available_books[:6]:  # Limit to first 6 books
        spread_raw = fetcher.get_spread_odds(game, book)
        ml_raw = fetcher.get_moneyline_odds(game, book)
        total_raw = fetcher.get_total_odds(game, book)

        if spread_raw:
            # Format spread data for UI
            line = spread_raw.get("line", 0)
            favorite = spread_raw.get("favorite", "home")
            if favorite == "home":
                spreads[book] = {
                    "away_line": f"+{line}",
                    "home_line": f"-{line}",
                    "away_odds": spread_raw.get("away_odds"),
                    "home_odds": spread_raw.get("home_odds"),
                }
            else:
                spreads[book] = {
                    "away_line": f"-{line}",
                    "home_line": f"+{line}",
                    "away_odds": spread_raw.get("away_odds"),
                    "home_odds": spread_raw.get("home_odds"),
                }

        if ml_raw:
            moneylines[book] = {
                "away_odds": ml_raw.get("away_odds"),
                "home_odds": ml_raw.get("home_odds"),
            }

        if total_raw:
            totals[book] = {
                "total": total_raw.get("line"),
                "over_odds": total_raw.get("over_odds"),
                "under_odds": total_raw.get("under_odds"),
            }

    return {
        "game": {
            "away_team": game.get("away", {}).get("mascot", ""),
            "home_team": game.get("home", {}).get("mascot", ""),
            "away_key": game.get("away", {}).get("key", ""),
            "home_key": game.get("home", {}).get("key", ""),
        },
        "spreads": spreads,
        "moneylines": moneylines,
        "totals": totals,
        "available_sportsbooks": available_books,
    }


class BetExampleRequest(BaseModel):
    """Request model for bet example builder."""
    game: dict  # {away_team, home_team}
    odds: dict  # {spreads: {book: {...}}, moneylines: {...}, totals: {...}}
    bet_amount: float = 100.0
    bet_type: str = "spread"  # spread, moneyline, total
    team: str = "away"  # away, home
    sportsbook: str = "draftkings"


@router.post("/bet-example")
async def build_bet_example_post(request: BetExampleRequest):
    """Build a bet example text from game and odds data."""
    game = request.game
    odds_data = request.odds
    bet_amount = request.bet_amount
    bet_type = request.bet_type
    team = request.team
    book = request.sportsbook

    # Find the first available book with data
    if bet_type == "spread" and odds_data.get("spreads"):
        book_odds = odds_data["spreads"].get(book) or next(iter(odds_data["spreads"].values()), None)
        if book_odds:
            line = book_odds.get(f"{team}_line", "")
            american_odds = book_odds.get(f"{team}_odds", -110)
            team_name = game.get(f"{team}_team", team.title())
            selection = f"{team_name} {line}"
        else:
            return {"error": "No spread odds available", "example_text": ""}
    elif bet_type == "moneyline" and odds_data.get("moneylines"):
        book_odds = odds_data["moneylines"].get(book) or next(iter(odds_data["moneylines"].values()), None)
        if book_odds:
            american_odds = book_odds.get(f"{team}_odds", -110)
            team_name = game.get(f"{team}_team", team.title())
            selection = f"{team_name} ML"
        else:
            return {"error": "No moneyline odds available", "example_text": ""}
    elif bet_type == "total" and odds_data.get("totals"):
        book_odds = odds_data["totals"].get(book) or next(iter(odds_data["totals"].values()), None)
        if book_odds:
            total = book_odds.get("total", "")
            american_odds = book_odds.get("over_odds", -110) if team == "away" else book_odds.get("under_odds", -110)
            selection = f"Over {total}" if team == "away" else f"Under {total}"
        else:
            return {"error": "No total odds available", "example_text": ""}
    else:
        return {"error": "Invalid bet type or no odds data", "example_text": ""}

    # Build the example text
    profit = calculate_profit(bet_amount, american_odds)
    event_context = f"{game.get('away_team', 'Away')} vs {game.get('home_team', 'Home')}"

    example = build_bet_example_text(
        bet_amount=bet_amount,
        selection=selection,
        odds=american_odds,
        event_context=event_context,
    )

    return {
        "example_text": example,
        "bet_amount": bet_amount,
        "selection": selection,
        "odds": american_odds,
        "potential_profit": round(profit, 2),
    }


@router.get("/bet-example")
async def build_bet_example(
    bet_amount: float = Query(50.0, description="Bet amount"),
    selection: str = Query(..., description="Bet selection text"),
    odds: int = Query(..., description="American odds"),
    event_context: str = Query("", description="Event context string"),
):
    """Build a bet example text for prompts (GET version)."""
    example = build_bet_example_text(
        bet_amount=bet_amount,
        selection=selection,
        odds=odds,
        event_context=event_context,
    )
    profit = calculate_profit(bet_amount, odds)

    return {
        "example_text": example,
        "bet_amount": bet_amount,
        "selection": selection,
        "odds": odds,
        "potential_profit": round(profit, 2),
    }


@router.get("/sportsbooks")
async def list_sportsbooks():
    """Get list of supported sportsbooks."""
    return {
        "sportsbooks": [
            {"key": "draftkings", "name": "DraftKings"},
            {"key": "fanduel", "name": "FanDuel"},
            {"key": "betmgm", "name": "BetMGM"},
            {"key": "caesars", "name": "Caesars"},
            {"key": "bet365", "name": "Bet365"},
            {"key": "hardrock", "name": "Hard Rock"},
            {"key": "fanatics", "name": "Fanatics"},
            {"key": "espnbet", "name": "ESPN BET"},
        ]
    }
