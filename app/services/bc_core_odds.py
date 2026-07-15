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
US_BOOK_SUFFIXES = {
    "AZ", "CO", "COL", "CT", "DC", "IA", "IL", "IN", "KS", "KY", "LA", "MA",
    "MD", "MI", "MO", "MS", "NC", "NJ", "NV", "NY", "OH", "OR", "PA", "PR",
    "TN", "VA", "VT", "WV", "WY", "AR",
}

_GAME_PERIOD = "game"
_book_ids_cache: dict[str, list[int]] = {}


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
    book_label: str,
) -> dict[str, Any]:
    """Map BC Core markets into the ``/api/odds/game`` response shape."""
    moneylines: dict[str, Any] = {}
    spreads: dict[str, Any] = {}
    totals: dict[str, Any] = {}

    def side_of(outcome: dict) -> str:
        team_id = outcome.get("teamId")
        if team_id is None:
            return "draw" if str(outcome.get("optionType") or "").lower() == "draw" else ""
        if str(team_id) == str(away_team_id):
            return "away"
        if str(team_id) == str(home_team_id):
            return "home"
        return ""

    for market in markets or []:
        bet_type, line_type, period = _market_key(market)
        if period != _GAME_PERIOD or market.get("isLive"):
            continue
        outcomes = market.get("outcomes") or []

        # 1X2 is soccer's real match-result market (three-way, with a draw);
        # Matchup/Moneyline is the two-way equivalent used by US sports.
        if line_type == "moneyline" and bet_type in ("1x2", "matchup"):
            prices: dict[str, Any] = {}
            for outcome in outcomes:
                side = side_of(outcome)
                price = _american(outcome)
                if side and price is not None and f"{side}_odds" not in prices:
                    prices[f"{side}_odds"] = price
            if prices.get("away_odds") is not None and prices.get("home_odds") is not None:
                # Prefer 1X2 when both exist: it carries the draw price.
                if bet_type == "1x2" or not moneylines.get(book_label):
                    moneylines[book_label] = {**moneylines.get(book_label, {}), **prices}

        elif line_type == "spread" and bet_type == "matchup":
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
                spreads.setdefault(book_label, prices)

        elif line_type == "total" and bet_type == "matchup":
            prices = {}
            for outcome in outcomes:
                option = str(outcome.get("optionType") or "").lower()
                price = _american(outcome)
                if option in ("over", "under") and price is not None and f"{option}_odds" not in prices:
                    prices[f"{option}_odds"] = price
                    if outcome.get("line") is not None:
                        prices["total"] = outcome["line"]
            if prices.get("over_odds") is not None and prices.get("under_odds") is not None:
                totals.setdefault(book_label, prices)

    result: dict[str, Any] = {}
    if moneylines:
        result["moneylines"] = moneylines
    if spreads:
        result["spreads"] = spreads
    if totals:
        result["totals"] = totals
    return result


async def fetch_event_odds(
    *,
    event_id: Any,
    away_team_id: Any,
    home_team_id: Any,
    brand: str = "bet365",
) -> dict[str, Any]:
    """Return posted prices for a BC Core event, or {} when none are available."""
    if not event_id or not bc_core_configured():
        return {}
    book_ids = await get_us_book_ids(brand)
    if not book_ids:
        return {}
    try:
        payload = await fetch_bc_core_json(
            f"/events/{event_id}/markets",
            params={"sportsbookIds": ",".join(str(i) for i in book_ids)},
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
        book_label=_norm(brand) or "bet365",
    )
