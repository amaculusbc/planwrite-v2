"""Live odds boosts from BC Core.

GOAL asked for the promos block to carry an operator's recurring promotions
(parlay boosts, early payouts, injury insurance) rather than only the new-user
welcome offer. BAM has no inventory for those - its catalog is acquisition
offers - but BC Core publishes real boosts per sport, priced, with the parlay
legs attached.

Early payout and injury insurance are standing product features rather than
per-event data, so they live in ``operator_facts`` instead.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from app.services.bc_core import bc_core_configured, fetch_bc_core_json
from app.services.bc_core_odds import get_us_book_ids

# BC Core exposes boosts per sport under these paths.
BOOST_PATHS: dict[str, str] = {
    "mlb": "/mlb/oddsboost",
    "nba": "/nba/oddsboost",
    "nfl": "/nfl/oddsboost",
    "nhl": "/nhl/oddsboost",
    "ncaafb": "/ncaafb/oddsboost",
    "ncaamb": "/ncaamb/oddsboost",
    "wnba": "/wnba/oddsboost",
}


def _american(value: Any) -> str:
    try:
        return f"{int(value):+d}"
    except (TypeError, ValueError):
        return ""


def _legs(boost: dict) -> list[str]:
    raw = boost.get("oddsBoostPicks")
    if not raw:
        return []
    try:
        picks = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return []
    legs: list[str] = []
    for pick in picks or []:
        text = str((pick or {}).get("criteria_description") or "").strip()
        if text and text not in legs:
            legs.append(text)
    return legs


def _not_yet_cut_off(boost: dict, now: datetime) -> bool:
    raw = str(boost.get("cutOffDate") or "").strip()
    if not raw:
        return True
    try:
        cut_off = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return True
    return cut_off > now


def _is_for_event(boost: dict, event_id: Any) -> bool:
    if event_id in (None, ""):
        return False
    return any(str(eid) == str(event_id) for eid in boost.get("eventIds") or [])


def summarize_boosts(
    boosts: list[dict],
    *,
    limit: int = 3,
    now: datetime | None = None,
    event_id: Any = None,
) -> list[dict]:
    """Reader-facing boost summaries.

    Boosts on the article's own match come first - a boost on the game being
    previewed is worth more to the reader than one on another game in the slate -
    then the biggest price gain. The same national promo is published under
    several state books, so boosts are de-duplicated on their legs, not their id.
    """
    now = now or datetime.now(UTC)
    seen: set[str] = set()
    summaries: list[dict] = []
    for boost in boosts or []:
        if not _not_yet_cut_off(boost, now):
            continue
        legs = _legs(boost)
        original = _american(boost.get("originalOdds"))
        boosted = _american(boost.get("boostedOdds"))
        if not legs or not original or not boosted:
            continue
        key = " | ".join(legs).lower()
        if key in seen:
            continue
        seen.add(key)
        try:
            gain = int(boost["boostedOdds"]) - int(boost["originalOdds"])
        except (KeyError, TypeError, ValueError):
            gain = 0
        summaries.append({
            "legs": legs,
            "original_odds": original,
            "boosted_odds": boosted,
            "gain": gain,
            "market_description": str(boost.get("marketDescription") or "").strip(),
            "is_this_match": _is_for_event(boost, event_id),
        })
    summaries.sort(key=lambda item: (item["is_this_match"], item["gain"]), reverse=True)
    return summaries[:limit]


async def fetch_operator_boosts(
    *,
    sport: str,
    brand: str,
    limit: int = 3,
    event_id: Any = None,
) -> list[dict]:
    """Active, US-only boosts for an operator in a sport. Empty list when none."""
    path = BOOST_PATHS.get(str(sport or "").strip().lower())
    if not path or not bc_core_configured():
        return []
    book_ids = await get_us_book_ids(brand)
    if not book_ids:
        return []
    try:
        payload = await fetch_bc_core_json(
            path,
            params={
                "sportsbookIds": ",".join(str(i) for i in book_ids),
                "isActive": "true",
            },
        )
    except Exception:
        return []
    return summarize_boosts(payload.get("results") or [], limit=limit, event_id=event_id)
