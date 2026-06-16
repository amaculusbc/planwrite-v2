"""On-demand prediction-market lookup for article examples.

This intentionally avoids a persistent market store. Provider responses are
filtered against the selected event and cached briefly in memory.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
import json
import re
import time
from typing import Any

import httpx

POLYMARKET_GAMMA_BASE_URL = "https://gamma-api.polymarket.com"
KALSHI_BASE_URL = "https://external-api.kalshi.com/trade-api/v2"

CACHE_TTL_SECONDS = 10 * 60
CACHE_MAX_KEYS = 128
DEFAULT_TIMEOUT_SECONDS = 4.0
MAX_PROVIDER_CANDIDATES = 80
KALSHI_MAX_PAGES = 3
KALSHI_DISCOVERY_PAGES = 1
KALSHI_PAGE_LIMIT = 1000

TEAM_CODE_ALIASES = {
    "argentina": {"arg"},
    "australia": {"aus"},
    "belgium": {"bel"},
    "brazil": {"bra"},
    "canada": {"can"},
    "colombia": {"col"},
    "croatia": {"cro"},
    "denmark": {"den"},
    "england": {"eng"},
    "france": {"fra"},
    "germany": {"ger"},
    "ghana": {"gha"},
    "iran": {"irn", "irq"},
    "iraq": {"irq"},
    "italy": {"ita"},
    "japan": {"jpn"},
    "mexico": {"mex"},
    "morocco": {"mar", "mor"},
    "netherlands": {"ned", "nld"},
    "norway": {"nor"},
    "poland": {"pol"},
    "portugal": {"por"},
    "senegal": {"sen"},
    "south africa": {"rsa", "saf"},
    "spain": {"esp"},
    "sweden": {"swe"},
    "switzerland": {"sui", "che"},
    "united states": {"usa", "us"},
    "usa": {"usa", "us"},
}

_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


@dataclass(frozen=True)
class PredictionMarketSearch:
    """Selected-event context used to search prediction-market providers."""

    sport: str = ""
    away_team: str = ""
    home_team: str = ""
    event_name: str = ""
    event_date: str = ""
    start_time: str = ""
    provider: str = "all"
    market_type: str = "event"
    limit: int = 10


@dataclass
class PredictionMarketCandidate:
    provider: str
    provider_market_id: str
    event_name: str
    market_title: str
    market_type: str
    selection: str
    side: str
    yes_price: float | None = None
    no_price: float | None = None
    implied_probability: float | None = None
    volume: float | None = None
    liquidity: float | None = None
    close_time: str = ""
    event_start_time: str = ""
    url: str = ""
    score: int = 0
    score_reasons: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["score_reasons"] = data.get("score_reasons") or []
        if self.implied_probability is not None:
            data["implied_probability_display"] = f"{round(self.implied_probability * 100)}%"
        else:
            data["implied_probability_display"] = ""
        return data


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _compact(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _tokens(value: str) -> set[str]:
    stop = {
        "fc",
        "cf",
        "sc",
        "the",
        "team",
        "club",
        "vs",
        "at",
        "and",
        "or",
        "will",
        "win",
        "wins",
        "winner",
        "beat",
        "beats",
        "picked",
        "round",
        "matchup",
        "first",
        "second",
        "third",
        "1st",
        "2nd",
        "3rd",
    }
    return {token for token in re.findall(r"[a-z0-9]+", _normalize(value)) if len(token) >= 3 and token not in stop}


def _team_alias_codes(team: str) -> set[str]:
    normalized = _normalize(team)
    compacted = _compact(team)
    aliases = set(TEAM_CODE_ALIASES.get(normalized, set()))
    if compacted and len(compacted) >= 3:
        aliases.add(compacted[:3])
    return {alias for alias in aliases if len(alias) >= 2}


def _parse_jsonish(value: Any) -> Any:
    if isinstance(value, (list, dict)):
        return value
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def _safe_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_dt(value: str) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        if len(raw) == 10 and raw[4] == "-" and raw[7] == "-":
            return datetime.fromisoformat(raw).replace(tzinfo=UTC)
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except ValueError:
        return None


def _event_dt(search: PredictionMarketSearch) -> datetime | None:
    return _parse_dt(search.start_time) or _parse_dt(search.event_date)


def _date_window_score(candidate_time: str, search: PredictionMarketSearch) -> tuple[int, str]:
    target = _event_dt(search)
    candidate = _parse_dt(candidate_time)
    if not target or not candidate:
        return 0, ""
    delta_hours = abs((candidate - target).total_seconds()) / 3600
    if delta_hours <= 12:
        return 12, "date-window"
    if delta_hours <= 48:
        return 5, "loose-date-window"
    return -10, "date-mismatch"


def _team_match_score(text: str, search: PredictionMarketSearch) -> tuple[int, list[str]]:
    haystack = _tokens(text)
    compact_text = _compact(text)
    away_tokens = _tokens(search.away_team)
    home_tokens = _tokens(search.home_team)
    event_tokens = _tokens(search.event_name)
    reasons: list[str] = []
    score = 0

    away_hit = bool(away_tokens and (away_tokens & haystack))
    home_hit = bool(home_tokens and (home_tokens & haystack))
    away_code_hit = any(code in compact_text for code in _team_alias_codes(search.away_team))
    home_code_hit = any(code in compact_text for code in _team_alias_codes(search.home_team))
    event_overlap = event_tokens & haystack
    event_overlap_ratio = (len(event_overlap) / len(event_tokens)) if event_tokens else 0.0

    if (away_hit or away_code_hit) and (home_hit or home_code_hit):
        score += 60
        reasons.append("both-teams")
    elif away_hit or home_hit or away_code_hit or home_code_hit:
        score += 20
        reasons.append("one-team")
    elif event_overlap and (len(event_overlap) >= 2 or event_overlap_ratio >= 0.35):
        score += min(45, 15 + (len(event_overlap) * 5))
        reasons.append("event-name")

    return score, reasons


def _classify_market(text: str) -> str:
    lowered = _normalize(text)
    if any(term in lowered for term in ["win", "winner", "beat", "defeat", "moneyline"]):
        return "winner"
    if any(term in lowered for term in ["total", "over", "under", "goals", "points"]):
        return "total"
    if any(term in lowered for term in ["spread", "handicap", "line"]):
        return "spread"
    if any(term in lowered for term in ["score", "goal", "player"]):
        return "prop"
    return "event"


def _score_candidate(candidate: PredictionMarketCandidate, search: PredictionMarketSearch) -> PredictionMarketCandidate:
    text = " ".join([candidate.event_name, candidate.market_title, candidate.selection])
    score, reasons = _team_match_score(text, search)
    date_scores = [
        _date_window_score(value, search)
        for value in [candidate.event_start_time, candidate.close_time]
        if str(value or "").strip()
    ]
    date_score, date_reason = max(date_scores, key=lambda item: item[0]) if date_scores else (0, "")
    score += date_score
    if date_reason:
        reasons.append(date_reason)
    if candidate.market_type in {"winner", "event"}:
        score += 5
        reasons.append("usable-market-type")
    if candidate.volume:
        score += min(8, int(candidate.volume // 1000))
        reasons.append("volume")
    if candidate.liquidity:
        score += min(8, int(candidate.liquidity // 1000))
        reasons.append("liquidity")
    candidate.score = score
    candidate.score_reasons = reasons
    return candidate


def _should_keep_candidate(candidate: PredictionMarketCandidate, search: PredictionMarketSearch) -> bool:
    if candidate.score < 35:
        return False
    if search.away_team and search.home_team:
        return "both-teams" in (candidate.score_reasons or [])
    return bool({"both-teams", "one-team", "event-name"} & set(candidate.score_reasons or []))


def _search_terms(search: PredictionMarketSearch) -> list[str]:
    terms: list[str] = []
    if search.away_team and search.home_team:
        terms.extend([
            f"{search.away_team} {search.home_team}",
            f"{search.home_team} {search.away_team}",
            f"{search.away_team} vs {search.home_team}",
        ])
    if search.event_name:
        terms.append(search.event_name)
    for item in [search.away_team, search.home_team]:
        if item:
            terms.append(item)
    deduped: list[str] = []
    for term in terms:
        normalized = " ".join(term.split())
        if normalized and normalized.lower() not in {x.lower() for x in deduped}:
            deduped.append(normalized)
    return deduped[:5]


def _cache_key(search: PredictionMarketSearch) -> str:
    return "|".join(
        [
            search.provider.lower(),
            search.sport.lower(),
            _normalize(search.away_team),
            _normalize(search.home_team),
            _normalize(search.event_name),
            search.event_date[:10],
            search.start_time[:16],
            search.market_type.lower(),
            str(search.limit),
        ]
    )


def _get_cached(key: str) -> dict[str, Any] | None:
    cached = _CACHE.get(key)
    if not cached:
        return None
    expires_at, payload = cached
    if expires_at < time.time():
        _CACHE.pop(key, None)
        return None
    return payload


def _set_cached(key: str, payload: dict[str, Any]) -> None:
    if len(_CACHE) >= CACHE_MAX_KEYS:
        oldest = min(_CACHE.items(), key=lambda item: item[1][0])[0]
        _CACHE.pop(oldest, None)
    _CACHE[key] = (time.time() + CACHE_TTL_SECONDS, payload)


def _polymarket_url(slug: str) -> str:
    return f"https://polymarket.com/event/{slug}" if slug else "https://polymarket.com"


def _normalize_polymarket_event(event: dict[str, Any]) -> list[PredictionMarketCandidate]:
    event_title = str(event.get("title") or event.get("ticker") or "").strip()
    event_slug = str(event.get("slug") or "").strip()
    event_start = str(event.get("startDate") or event.get("startDateIso") or "").strip()
    markets = event.get("markets") if isinstance(event.get("markets"), list) else []
    candidates: list[PredictionMarketCandidate] = []
    for market in markets:
        if market.get("closed") is True or market.get("active") is False:
            continue
        title = str(market.get("question") or market.get("title") or event_title).strip()
        outcomes = _parse_jsonish(market.get("outcomes")) or []
        prices = _parse_jsonish(market.get("outcomePrices")) or []
        if not isinstance(outcomes, list) or not outcomes:
            outcomes = ["Yes", "No"]
        if not isinstance(prices, list):
            prices = []
        market_slug = str(market.get("slug") or event_slug).strip()
        market_type = str(market.get("sportsMarketType") or market.get("marketType") or "").strip().lower()
        inferred_type = _classify_market(" ".join([title, event_title, market_type]))
        for idx, outcome in enumerate(outcomes[:4]):
            price = _safe_float(prices[idx] if idx < len(prices) else None)
            label = str(outcome or "").strip() or ("Yes" if idx == 0 else "No")
            candidates.append(
                PredictionMarketCandidate(
                    provider="polymarket",
                    provider_market_id=str(market.get("id") or market.get("conditionId") or market_slug),
                    event_name=event_title,
                    market_title=title,
                    market_type=inferred_type,
                    selection=label,
                    side=label.lower(),
                    yes_price=price if idx == 0 else None,
                    no_price=price if idx == 1 else None,
                    implied_probability=price,
                    volume=_safe_float(market.get("volumeNum") or market.get("volume")),
                    liquidity=_safe_float(market.get("liquidityNum") or market.get("liquidity")),
                    close_time=str(market.get("endDate") or market.get("endDateIso") or event.get("endDate") or "").strip(),
                    event_start_time=str(market.get("eventStartTime") or market.get("gameStartTime") or event_start).strip(),
                    url=_polymarket_url(market_slug),
                )
            )
    return candidates


async def _fetch_polymarket(search: PredictionMarketSearch, client: httpx.AsyncClient) -> list[PredictionMarketCandidate]:
    candidates: list[PredictionMarketCandidate] = []
    seen_ids: set[str] = set()
    for term in _search_terms(search)[:3]:
        response = await client.get(
            f"{POLYMARKET_GAMMA_BASE_URL}/public-search",
            params={
                "q": term,
                "limit_per_type": 8,
                "events_status": "active",
                "keep_closed_markets": 0,
                "search_profiles": "false",
                "search_tags": "false",
            },
        )
        response.raise_for_status()
        payload = response.json()
        events = payload.get("events") if isinstance(payload, dict) else []
        for event in events or []:
            if not isinstance(event, dict):
                continue
            for candidate in _normalize_polymarket_event(event):
                unique = f"{candidate.provider}:{candidate.provider_market_id}:{candidate.selection}"
                if unique in seen_ids:
                    continue
                seen_ids.add(unique)
                candidates.append(candidate)
                if len(candidates) >= MAX_PROVIDER_CANDIDATES:
                    return candidates
    return candidates


def _normalize_kalshi_market(market: dict[str, Any]) -> list[PredictionMarketCandidate]:
    title = str(market.get("title") or "").strip()
    subtitle = str(market.get("subtitle") or "").strip()
    event_ticker = str(market.get("event_ticker") or "").strip()
    market_title = title or subtitle or event_ticker
    yes_label = str(market.get("yes_sub_title") or "Yes").strip()
    no_label = str(market.get("no_sub_title") or "No").strip()
    if not no_label or no_label == yes_label:
        no_label = f"No on {market_title}"
    yes_price = _safe_float(market.get("yes_ask_dollars") or market.get("last_price_dollars") or market.get("yes_bid_dollars"))
    no_price = _safe_float(market.get("no_ask_dollars") or market.get("no_bid_dollars"))
    inferred_type = _classify_market(" ".join([market_title, yes_label, no_label]))
    base = {
        "provider": "kalshi",
        "provider_market_id": str(market.get("ticker") or ""),
        "event_name": event_ticker,
        "market_title": market_title,
        "market_type": inferred_type,
        "volume": _safe_float(market.get("volume_fp") or market.get("volume_24h_fp")),
        "liquidity": _safe_float(market.get("open_interest_fp")),
        "close_time": str(market.get("close_time") or market.get("latest_expiration_time") or "").strip(),
        "event_start_time": str(market.get("open_time") or "").strip(),
        "url": str(market.get("market_url") or market.get("contract_url") or "https://kalshi.com").strip(),
    }
    return [
        PredictionMarketCandidate(
            **base,
            selection=yes_label,
            side="yes",
            yes_price=yes_price,
            implied_probability=yes_price,
        ),
        PredictionMarketCandidate(
            **base,
            selection=no_label,
            side="no",
            no_price=no_price,
            implied_probability=no_price,
        ),
    ]


async def _fetch_kalshi(search: PredictionMarketSearch, client: httpx.AsyncClient) -> list[PredictionMarketCandidate]:
    base_params: dict[str, Any] = {
        "status": "open",
        "limit": KALSHI_PAGE_LIMIT,
        "mve_filter": "exclude",
    }
    param_sets: list[tuple[dict[str, Any], int]] = [(dict(base_params), KALSHI_DISCOVERY_PAGES)]
    target = _event_dt(search)
    if target:
        date_params = dict(base_params)
        date_params["min_close_ts"] = int((target - timedelta(days=1)).timestamp())
        date_params["max_close_ts"] = int((target + timedelta(days=2)).timestamp())
        param_sets.append((date_params, KALSHI_MAX_PAGES))

    candidates: list[PredictionMarketCandidate] = []
    seen: set[str] = set()
    for params, page_count in param_sets:
        cursor = ""
        for _ in range(page_count):
            page_params = dict(params)
            if cursor:
                page_params["cursor"] = cursor
            response = await client.get(f"{KALSHI_BASE_URL}/markets", params=page_params)
            response.raise_for_status()
            payload = response.json()
            markets = payload.get("markets") if isinstance(payload, dict) else []
            for market in markets or []:
                if not isinstance(market, dict):
                    continue
                for candidate in _normalize_kalshi_market(market):
                    unique = f"{candidate.provider}:{candidate.provider_market_id}:{candidate.side}"
                    if unique in seen:
                        continue
                    scored = _score_candidate(candidate, search)
                    if scored.score >= 25:
                        candidates.append(candidate)
                        seen.add(unique)
                if len(candidates) >= MAX_PROVIDER_CANDIDATES:
                    return candidates[:MAX_PROVIDER_CANDIDATES]
            cursor = str(payload.get("cursor") or "").strip() if isinstance(payload, dict) else ""
            if not cursor:
                break
    return candidates


async def search_prediction_markets(search: PredictionMarketSearch) -> dict[str, Any]:
    """Search providers on-demand and return a small ranked result set."""
    limit = max(1, min(int(search.limit or 10), 25))
    search = PredictionMarketSearch(**{**asdict(search), "limit": limit})
    key = _cache_key(search)
    cached = _get_cached(key)
    if cached:
        return {**cached, "cached": True}

    provider = search.provider.lower().strip() or "all"
    providers = ["polymarket", "kalshi"] if provider == "all" else [provider]
    errors: dict[str, str] = {}
    candidates: list[PredictionMarketCandidate] = []

    timeout = httpx.Timeout(DEFAULT_TIMEOUT_SECONDS)
    async with httpx.AsyncClient(timeout=timeout, headers={"user-agent": "planwrite-v2/2.0"}) as client:
        tasks = []
        if "polymarket" in providers:
            tasks.append(("polymarket", _fetch_polymarket(search, client)))
        if "kalshi" in providers:
            tasks.append(("kalshi", _fetch_kalshi(search, client)))
        results = await asyncio.gather(*(task for _, task in tasks), return_exceptions=True)

    for (provider_name, _), result in zip(tasks, results, strict=False):
        if isinstance(result, Exception):
            errors[provider_name] = str(result)
            continue
        candidates.extend(result)

    scored = [_score_candidate(candidate, search) for candidate in candidates]
    kept = [candidate for candidate in scored if _should_keep_candidate(candidate, search)]
    kept.sort(
        key=lambda item: (
            item.score,
            item.volume or 0,
            item.liquidity or 0,
            1 if item.side == "yes" else 0,
            item.implied_probability or 0,
        ),
        reverse=True,
    )
    payload = {
        "query": asdict(search),
        "markets": [candidate.to_dict() for candidate in kept[:limit]],
        "candidate_count": len(candidates),
        "matched_count": len(kept),
        "cached": False,
        "cache_ttl_seconds": CACHE_TTL_SECONDS,
        "errors": errors,
    }
    _set_cached(key, payload)
    return payload


def build_prediction_market_example(
    *,
    market: dict[str, Any],
    position_amount: float,
    qualifying_amount: float | None = None,
    reward_amount: float | None = None,
) -> dict[str, Any]:
    """Build prompt-ready market example text from a selected market."""
    amount = max(float(position_amount or 0), 1.0)
    price = _safe_float(market.get("implied_probability") or market.get("yes_price") or market.get("no_price")) or 0.5
    price = min(max(price, 0.01), 0.99)
    contracts = amount / price
    payout = contracts * 1.0
    profit = payout - amount
    selection = str(market.get("selection") or "Yes").strip()
    provider = str(market.get("provider") or "prediction market").strip().title()
    market_title = str(market.get("market_title") or market.get("event_name") or "the selected market").strip()
    qualifying_text = (
        f"I complete the ${float(qualifying_amount):.0f} qualifying action first. "
        if qualifying_amount
        else ""
    )
    reward_text = (
        f" The ${float(reward_amount):.0f} promo credit can then help cover a later eligible position."
        if reward_amount
        else ""
    )
    example = (
        f"{qualifying_text}Then I open a ${amount:.0f} {selection} position on {market_title} at {provider}. "
        f"At about ${price:.2f} per contract, that buys roughly {contracts:.0f} contracts. "
        f"If the contract settles at $1.00, the position returns about ${payout:.2f}, "
        f"or roughly ${profit:.2f} before fees.{reward_text}"
    )
    return {
        "example_text": example,
        "position_amount": amount,
        "entry_price": price,
        "settlement_price": 1.0,
        "contracts": round(contracts, 2),
        "potential_payout": round(payout, 2),
        "potential_profit": round(profit, 2),
        "selection": selection,
        "market_title": market_title,
        "provider": market.get("provider"),
        "provider_market_id": market.get("provider_market_id"),
        "url": market.get("url"),
        "event_context": market.get("event_name") or market_title,
        "prediction_market": market,
        "qualifying_amount": qualifying_amount,
        "reward_amount": reward_amount,
    }
