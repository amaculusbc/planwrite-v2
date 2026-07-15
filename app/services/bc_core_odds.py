"""Market prices sourced from BC Core.

The original odds feed (Charlotte) carries no soccer at all, which is why soccer
match breakdowns had no prices to argue from and fell back to injuries. BC Core
already backs soccer lineups and absences, and it does carry soccer markets under
the same event id we have resolved - including the soccer-native 1X2 market with
a draw price.

Markets are returned in the same shape as ``/api/odds/game`` so the existing
talking-point formatter consumes them unchanged.
"""

from __future__ import annotations

import re
from typing import Any

from app.services.bc_core import bc_core_configured, fetch_bc_core_json

# bet365 (and every other brand) has one BC Core book per territory: 17 US states
# plus ARG, BRA, CHL, Canada, DEU, GBR... Pulling markets without this filter
# returns German copy ("Wetten-Konfigurator"), the same international leak the
# BAM catalog had.
# Every US state code, plus DC/PR. An allowlist rather than a foreign blocklist
# because some suffixes are ambiguous - bet365's "COL" is Colombia, not Colorado
# (which is "CO"), so anything that is not a real state code stays out.
US_BOOK_SUFFIXES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI",
    "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN",
    "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH",
    "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA",
    "WV", "WI", "WY", "PR",
}

_GAME_PERIOD = "game"
_book_ids_cache: dict[str, list[int]] = {}
_books_by_id_cache: dict[str, dict[int, str]] = {}

# The books the app quotes, keyed as the odds API already labels them.
ODDS_BRANDS: tuple[str, ...] = (
    "draftkings", "fanduel", "betmgm", "caesars",
    "bet365", "hardrock", "fanatics", "espnbet",
)


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _is_us_book(name: str) -> bool:
    parts = str(name or "").strip().split()
    return bool(parts) and parts[-1].upper() in US_BOOK_SUFFIXES


async def get_us_book_ids(brand: str) -> list[int]:
    """Active US-state book ids for a brand, e.g. every bet365 US book."""
    key = _norm(brand)
    if not key:
        return []
    if key in _book_ids_cache:
        return _book_ids_cache[key]
    try:
        payload = await fetch_bc_core_json("/sportsbooks")
    except Exception:
        return []
    ids: list[int] = []
    for book in payload.get("results") or []:
        name = str(book.get("name") or "")
        if not book.get("isActive") or not _is_us_book(name):
            continue
        if key and key in _norm(name):
            try:
                ids.append(int(book["id"]))
            except (KeyError, TypeError, ValueError):
                continue
    _book_ids_cache[key] = ids
    return ids


async def get_us_books_by_id(brands: tuple[str, ...] | list[str] | None = None) -> dict[int, str]:
    """Map every US-state book id to its brand label, e.g. {3915: "bet365"}."""
    wanted = tuple(brands or ODDS_BRANDS)
    cache_key = "|".join(sorted(wanted))
    if _books_by_id_cache.get(cache_key):
        return _books_by_id_cache[cache_key]
    try:
        payload = await fetch_bc_core_json("/sportsbooks")
    except Exception:
        return {}
    mapping: dict[int, str] = {}
    for book in payload.get("results") or []:
        name = str(book.get("name") or "")
        if not book.get("isActive") or not _is_us_book(name):
            continue
        normalized = _norm(name)
        for brand in wanted:
            if _norm(brand) and _norm(brand) in normalized:
                try:
                    mapping[int(book["id"])] = brand
                except (KeyError, TypeError, ValueError):
                    pass
                break
    _books_by_id_cache[cache_key] = mapping
    return mapping


def _american(outcome: dict) -> Any:
    value = outcome.get("americanOdds")
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _market_key(market: dict) -> tuple[str, str, str]:
    return (
        str((market.get("betType") or {}).get("name") or "").strip().lower(),
        str((market.get("lineType") or {}).get("name") or "").strip().lower(),
        str((market.get("linePeriodType") or {}).get("name") or "").strip().lower(),
    )


def map_markets_to_odds(
    markets: list[dict],
    *,
    away_team_id: Any,
    home_team_id: Any,
    book_by_id: dict[int, str] | None = None,
    book_label: str = "",
) -> dict[str, Any]:
    """Map BC Core markets into the ``/api/odds/game`` response shape.

    Outcomes are grouped by the brand behind their sportsbookId, so one call
    yields every book the way the odds feed already reports them. Passing
    ``book_label`` instead attributes everything to a single brand.
    """
    moneylines: dict[str, Any] = {}
    spreads: dict[str, Any] = {}
    totals: dict[str, Any] = {}
    ml_bet_type: dict[str, str] = {}

    def side_of(outcome: dict) -> str:
        team_id = outcome.get("teamId")
        if team_id is None:
            return "draw" if str(outcome.get("optionType") or "").lower() == "draw" else ""
        if str(team_id) == str(away_team_id):
            return "away"
        if str(team_id) == str(home_team_id):
            return "home"
        return ""

    def book_of(outcome: dict) -> str:
        if book_label:
            return book_label
        try:
            return (book_by_id or {}).get(int(outcome.get("sportsbookId")), "")
        except (TypeError, ValueError):
            return ""

    def by_book(outcomes: list[dict]) -> dict[str, list[dict]]:
        grouped: dict[str, list[dict]] = {}
        for outcome in outcomes:
            book = book_of(outcome)
            if book:
                grouped.setdefault(book, []).append(outcome)
        return grouped

    for market in markets or []:
        bet_type, line_type, period = _market_key(market)
        if period != _GAME_PERIOD or market.get("isLive"):
            continue
        grouped = by_book(market.get("outcomes") or [])

        # 1X2 is soccer's real match-result market (three-way, with a draw);
        # Matchup/Moneyline is the two-way equivalent used by US sports.
        if line_type == "moneyline" and bet_type in ("1x2", "matchup"):
            for book, outcomes in grouped.items():
                prices: dict[str, Any] = {}
                for outcome in outcomes:
                    side = side_of(outcome)
                    price = _american(outcome)
                    if side and price is not None and f"{side}_odds" not in prices:
                        prices[f"{side}_odds"] = price
                if prices.get("away_odds") is None or prices.get("home_odds") is None:
                    continue
                # Prefer 1X2 when both exist: only it carries the draw price.
                if book not in moneylines or (bet_type == "1x2" and ml_bet_type.get(book) != "1x2"):
                    moneylines[book] = prices
                    ml_bet_type[book] = bet_type

        elif line_type == "spread" and bet_type == "matchup":
            for book, outcomes in grouped.items():
                prices = {}
                for outcome in outcomes:
                    side = side_of(outcome)
                    price = _american(outcome)
                    if side in ("away", "home") and price is not None and f"{side}_odds" not in prices:
                        prices[f"{side}_odds"] = price
                        line = outcome.get("line")
                        if line is not None:
                            prices[f"{side}_line"] = f"{float(line):+g}"
                if prices.get("away_odds") is not None and prices.get("home_odds") is not None:
                    spreads.setdefault(book, prices)

        elif line_type == "total" and bet_type == "matchup":
            for book, outcomes in grouped.items():
                prices = {}
                for outcome in outcomes:
                    option = str(outcome.get("optionType") or "").lower()
                    price = _american(outcome)
                    if option in ("over", "under") and price is not None and f"{option}_odds" not in prices:
                        prices[f"{option}_odds"] = price
                        if outcome.get("line") is not None:
                            prices["total"] = outcome["line"]
                if prices.get("over_odds") is not None and prices.get("under_odds") is not None:
                    totals.setdefault(book, prices)

    result: dict[str, Any] = {}
    if moneylines:
        result["moneylines"] = moneylines
    if spreads:
        result["spreads"] = spreads
    if totals:
        result["totals"] = totals
    return result


async def fetch_odds_for_teams(
    *,
    sport: str,
    away_team: str,
    home_team: str,
    game_date: str = "",
    start_time: str = "",
    brand: str = "bet365",
) -> dict[str, Any]:
    """Resolve a BC Core event from team names, then return its posted prices.

    The odds endpoint only knows team names, while BC Core keys markets on an
    event id, so the event is matched here using the same scorer the generation
    flow uses.
    """
    # Imported lazily: bc_core imports settings at module load, and this keeps
    # the odds module usable in isolation.
    from app.services.bc_core import build_event_context

    if not bc_core_configured():
        return {}
    source_facts = {
        "event": {
            "sport": str(sport or "").strip().lower(),
            "away_team": away_team,
            "home_team": home_team,
            "event_date": game_date,
            "start_time": start_time or game_date,
        }
    }
    try:
        event, _reason = await build_event_context(source_facts)
    except Exception:
        return {}
    if not event.get("matched"):
        return {}
    return await fetch_event_odds(
        event_id=event.get("event_id"),
        away_team_id=event.get("away_team_id"),
        home_team_id=event.get("home_team_id"),
        brand=brand,
    )


async def fetch_event_odds(
    *,
    event_id: Any,
    away_team_id: Any,
    home_team_id: Any,
    brand: str = "",
) -> dict[str, Any]:
    """Posted prices for a BC Core event across every book we quote.

    ``brand`` only nudges which books are requested first; the response always
    keys each market by book, matching the odds feed it replaces.
    """
    if not event_id or not bc_core_configured():
        return {}
    brands = list(ODDS_BRANDS)
    brand_key = _norm(brand)
    if brand_key and not any(_norm(b) == brand_key for b in brands):
        brands.append(brand_key)
    book_by_id = await get_us_books_by_id(tuple(brands))
    if not book_by_id:
        return {}
    try:
        payload = await fetch_bc_core_json(
            f"/events/{event_id}/markets",
            params={"sportsbookIds": ",".join(str(i) for i in book_by_id)},
        )
    except Exception:
        return {}
    markets = payload.get("results") or []
    if not markets:
        return {}
    return map_markets_to_odds(
        markets,
        away_team_id=away_team_id,
        home_team_id=home_team_id,
        book_by_id=book_by_id,
    )
