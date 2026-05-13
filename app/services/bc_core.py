from __future__ import annotations

from datetime import UTC, datetime
import atexit
import re
from typing import Any

import httpx

from app.config import get_settings

settings = get_settings()
_shared_client: httpx.AsyncClient | None = None

DEFAULT_BC_CORE_BASE_URL = "https://core-external-api.actionnetwork.com"

SPORT_ENDPOINTS = {
    "nba": "/nba/events",
    "nfl": "/nfl/events",
    "mlb": "/mlb/events",
    "nhl": "/nhl/events",
    "ncaaf": "/ncaafb/events",
    "ncaab": "/ncaamb/events",
    "ncaawb": "/ncaawb/events",
    "wnba": "/wnba/events",
    "ufc": "/ufc/events",
    "mma": "/ufc/events",
    "golf": "/golf/events",
}

SPORT_LEAGUE_IDS = {
    "nba": 2,
    "wnba": 8,
    "ncaab": 12,
    "ncaawb": 17,
    "nfl": 9,
    "ufc": 10,
    "mma": 10,
}


def _base_url() -> str:
    return (settings.bc_core_base_url or DEFAULT_BC_CORE_BASE_URL).rstrip("/")


def get_bc_core_base_url() -> str:
    return _base_url()


def _headers() -> dict[str, str]:
    if settings.bc_core_api_key:
        return {"x-api-key": settings.bc_core_api_key}
    return {}


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def _tokenize(value: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9']+", (value or "").lower()) if len(token) >= 2}


def _results_list(payload: Any) -> list[dict]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        results = payload.get("results")
        if isinstance(results, list):
            return [item for item in results if isinstance(item, dict)]
    return []


def _exc_text(exc: Exception) -> str:
    text = str(exc).strip()
    return text or exc.__class__.__name__


def _season_type_from_schedule(schedule_type: str) -> str:
    normalized = (schedule_type or "").strip().lower()
    if "post" in normalized:
        return "Post"
    if "pre" in normalized:
        return "Pre"
    return "Reg"


def _season_year_from_name(season_name: str, scheduled_date: str) -> int | None:
    season_name = season_name or ""
    match = re.search(r"\b(20\d{2})\b", season_name)
    if match:
        return int(match.group(1))
    date_match = re.search(r"\b(20\d{2})-\d{2}-\d{2}\b", scheduled_date or "")
    if date_match:
        return int(date_match.group(1))
    return None


async def _get_json(path: str, *, params: dict | None = None) -> dict:
    client = _get_shared_client()
    response = await client.get(f"{_base_url()}{path}", headers=_headers(), params=params)
    response.raise_for_status()
    return response.json()


def _get_shared_client() -> httpx.AsyncClient:
    global _shared_client
    if _shared_client is not None:
        return _shared_client

    client_kwargs: dict[str, Any] = {
        "timeout": settings.bc_core_timeout_seconds,
        "limits": httpx.Limits(max_connections=20, max_keepalive_connections=10, keepalive_expiry=120.0),
    }
    if settings.bc_core_socks_proxy:
        client_kwargs["proxy"] = settings.bc_core_socks_proxy
    _shared_client = httpx.AsyncClient(**client_kwargs)
    return _shared_client


def _close_shared_client() -> None:
    global _shared_client
    if _shared_client is None:
        return
    try:
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            loop.create_task(_shared_client.aclose())
        else:
            asyncio.run(_shared_client.aclose())
    except Exception:
        pass
    finally:
        _shared_client = None


atexit.register(_close_shared_client)


async def fetch_bc_core_json(path: str, *, params: dict | None = None) -> dict:
    return await _get_json(path, params=params)


def bc_core_configured() -> bool:
    return bool(settings.bc_core_base_url or settings.bc_core_api_key)


def _pick_best_sportsbook(candidates: list[dict], requested_state: str) -> dict | None:
    if not candidates:
        return None
    requested = requested_state.upper()

    def score(item: dict) -> tuple[int, int, int]:
        states = [state.upper() for state in item.get("states", [])]
        exact = 1 if requested and requested in states else 0
        active = 1 if item.get("isActive") else 0
        state_specific = 1 if len(states) == 1 else 0
        return (exact, active, state_specific)

    return sorted(candidates, key=score, reverse=True)[0]


async def build_operator_context(source_facts: dict) -> tuple[dict, str]:
    offer = source_facts.get("primary_offer", {}) or {}
    brand = str(offer.get("brand") or "").strip()
    requested_state = str(source_facts.get("state") or "").upper()
    requested_sport = str((source_facts.get("event") or {}).get("sport") or "").lower()
    source_url = f"{_base_url()}/sportsbooks"

    if not bc_core_configured():
        return {
            "provider": "fallback",
            "matched": False,
            "reason": "BC Core is not configured",
            "requested_brand": brand,
            "requested_state": requested_state,
            "requested_sport": requested_sport,
            "source_urls": [source_url],
        }, "BC Core is not configured"
    if not brand:
        return {
            "provider": "fallback",
            "matched": False,
            "reason": "No offer brand supplied",
            "requested_brand": brand,
            "requested_state": requested_state,
            "requested_sport": requested_sport,
            "source_urls": [source_url],
        }, "No offer brand supplied"

    requested_brand = _normalize(brand)
    try:
        sportsbook_payload = await _get_json("/sportsbooks")
    except httpx.HTTPError as exc:
        message = f"BC Core sportsbooks request failed: {_exc_text(exc)}"
        return {
            "provider": "fallback",
            "matched": False,
            "reason": message,
            "requested_brand": brand,
            "requested_state": requested_state,
            "requested_sport": requested_sport,
            "source_urls": [source_url],
            "checked_at": datetime.now(UTC).isoformat(),
        }, message

    sportsbooks = _results_list(sportsbook_payload)

    def matches(item: dict) -> bool:
        parent = item.get("parent") or {}
        values = [
            item.get("name", ""),
            item.get("shortName", ""),
            item.get("abbr", ""),
            parent.get("name", ""),
        ]
        normalized_values = {_normalize(value) for value in values if value}
        if requested_brand in normalized_values:
            return True
        return any(requested_brand in value or value in requested_brand for value in normalized_values if value)

    candidates = [item for item in sportsbooks if matches(item)]
    best = _pick_best_sportsbook(candidates, requested_state)

    coverage = {
        "checked": False,
        "supported": None,
        "league_id": None,
        "source_url": "",
        "reason": "",
    }
    league_id = SPORT_LEAGUE_IDS.get(requested_sport)
    if best and league_id is not None:
        coverage_url = f"{_base_url()}/sportsbooks/coverage?leagueIds={league_id}"
        try:
            coverage_payload = await _get_json("/sportsbooks/coverage", params={"leagueIds": str(league_id)})
            coverage_results = _results_list(coverage_payload)
            coverage = {
                "checked": True,
                "supported": any(item.get("sportsbookParentId") == best.get("parentId") for item in coverage_results),
                "league_id": league_id,
                "source_url": coverage_url,
                "reason": "",
            }
        except httpx.HTTPError as exc:
            coverage = {
                "checked": True,
                "supported": None,
                "league_id": league_id,
                "source_url": coverage_url,
                "reason": f"BC Core coverage request failed: {_exc_text(exc)}",
            }

    if not best:
        return {
            "provider": "bc_core",
            "matched": False,
            "reason": "No sportsbook match found",
            "requested_brand": brand,
            "requested_state": requested_state,
            "requested_sport": requested_sport,
            "source_urls": [source_url],
            "checked_at": datetime.now(UTC).isoformat(),
        }, "No sportsbook match found"

    states = [state.upper() for state in best.get("states", [])]
    parent = best.get("parent") or {}
    parent_name = parent.get("name") or best.get("name", "")
    return {
        "provider": "bc_core",
        "matched": True,
        "requested_brand": brand,
        "requested_state": requested_state,
        "requested_sport": requested_sport,
        "sportsbook_id": best.get("id"),
        "sportsbook_name": best.get("name", ""),
        "parent_id": best.get("parentId"),
        "parent_name": parent_name,
        "website": best.get("website", ""),
        "states": states,
        "requested_state_supported": requested_state in states if requested_state and states else None,
        "active": bool(best.get("isActive")),
        "coverage": coverage,
        "logos": best.get("logos", {}) or {},
        "source_urls": [source_url, coverage.get("source_url", "")],
        "checked_at": datetime.now(UTC).isoformat(),
    }, ""


def _event_basis(source_facts: dict) -> str:
    event = source_facts.get("event", {}) or {}
    return " ".join(
        part
        for part in [
            event.get("headline", ""),
            event.get("away_team", ""),
            event.get("home_team", ""),
            event.get("custom_event", ""),
            source_facts.get("title", ""),
        ]
        if part
    )


def _score_event(source_facts: dict, event: dict) -> int:
    basis_terms = _tokenize(_event_basis(source_facts))
    requested_event = source_facts.get("event", {}) or {}
    away_requested = _normalize(requested_event.get("away_team", ""))
    home_requested = _normalize(requested_event.get("home_team", ""))
    teams = event.get("teams", []) or []
    away_team = _normalize(next((team.get("name", "") for team in teams if team.get("side") == "AWAY"), ""))
    home_team = _normalize(next((team.get("name", "") for team in teams if team.get("side") == "HOME"), ""))
    team_names = " ".join(team.get("name", "") for team in teams)
    event_terms = _tokenize(" ".join([event.get("name", ""), team_names]))
    overlap = len(basis_terms & event_terms)

    team_score = 0
    if away_requested and away_team:
        if away_requested == away_team:
            team_score += 10
        elif away_requested in away_team or away_team in away_requested:
            team_score += 5
    if home_requested and home_team:
        if home_requested == home_team:
            team_score += 10
        elif home_requested in home_team or home_team in home_requested:
            team_score += 5
    return team_score + overlap


def _team_alignment(source_facts: dict, event: dict) -> tuple[int, int]:
    requested_event = source_facts.get("event", {}) or {}
    away_requested = _normalize(requested_event.get("away_team", ""))
    home_requested = _normalize(requested_event.get("home_team", ""))
    teams = event.get("teams", []) or []
    away_team = _normalize(next((team.get("name", "") for team in teams if team.get("side") == "AWAY"), ""))
    home_team = _normalize(next((team.get("name", "") for team in teams if team.get("side") == "HOME"), ""))

    def value(requested: str, candidate: str) -> int:
        if requested and candidate:
            if requested == candidate:
                return 10
            if requested in candidate or candidate in requested:
                return 5
        return 0

    return value(away_requested, away_team), value(home_requested, home_team)


async def build_event_context(source_facts: dict) -> tuple[dict, str]:
    requested_event = source_facts.get("event", {}) or {}
    sport = str(requested_event.get("sport") or "").lower()
    path = SPORT_ENDPOINTS.get(sport)
    source_url = f"{_base_url()}{path}" if path else ""

    if not bc_core_configured():
        return {"provider": "fallback", "matched": False, "reason": "BC Core is not configured", "source_urls": [source_url] if source_url else []}, "BC Core is not configured"
    if not path:
        return {
            "provider": "fallback",
            "matched": False,
            "reason": f"No BC Core event endpoint mapping for sport '{sport or 'unknown'}'",
            "source_urls": [],
        }, f"No BC Core event endpoint mapping for sport '{sport or 'unknown'}'"

    try:
        payload = await _get_json(path)
    except httpx.HTTPError as exc:
        message = f"BC Core events request failed: {_exc_text(exc)}"
        return {
            "provider": "fallback",
            "matched": False,
            "reason": message,
            "sport": sport,
            "source_urls": [source_url],
        }, message

    events = _results_list(payload)
    if not events:
        return {
            "provider": "bc_core",
            "matched": False,
            "reason": "No BC Core events returned",
            "sport": sport,
            "source_urls": [source_url],
        }, "No BC Core events returned"

    scored = [(event, _score_event(source_facts, event)) for event in events]
    scored.sort(key=lambda item: item[1], reverse=True)
    best, best_score = scored[0]
    away_value, home_value = _team_alignment(source_facts, best)

    if requested_event.get("away_team") and requested_event.get("home_team") and (away_value == 0 or home_value == 0):
        return {
            "provider": "bc_core",
            "matched": False,
            "reason": "BC Core event did not match both requested teams",
            "sport": sport,
            "source_urls": [source_url],
        }, "BC Core event did not match both requested teams"
    if best_score <= 0 and requested_event.get("headline"):
        return {
            "provider": "bc_core",
            "matched": False,
            "reason": "No BC Core event matched the requested headline",
            "sport": sport,
            "source_urls": [source_url],
        }, "No BC Core event matched the requested headline"

    teams = best.get("teams", []) or []
    players = best.get("players", []) or []
    away_team = next((team for team in teams if team.get("side") == "AWAY"), {})
    home_team = next((team for team in teams if team.get("side") == "HOME"), {})
    season_schedule = best.get("seasonSchedule", {}) or {}
    broadcast = best.get("broadcast", {}) or {}
    return {
        "provider": "bc_core",
        "matched": True,
        "sport": sport,
        "event_id": best.get("id"),
        "league_id": best.get("leagueId"),
        "headline": requested_event.get("headline", ""),
        "event_name": best.get("name", ""),
        "scheduled_date": best.get("scheduledDate", ""),
        "event_status": (best.get("eventStatus") or {}).get("name", ""),
        "season_name": (best.get("season") or {}).get("name", ""),
        "schedule_name": season_schedule.get("name", ""),
        "schedule_type": season_schedule.get("scheduleType", ""),
        "season_type": _season_type_from_schedule(season_schedule.get("scheduleType", "")),
        "season_year": _season_year_from_name((best.get("season") or {}).get("name", ""), best.get("scheduledDate", "")),
        "season_schedule_id": season_schedule.get("id"),
        "away_team_id": away_team.get("id"),
        "away_team": away_team.get("name", ""),
        "home_team_id": home_team.get("id"),
        "home_team": home_team.get("name", ""),
        "network": broadcast.get("network") or broadcast.get("internet") or "",
        "coverage": best.get("coverage", ""),
        "total_bets": best.get("totalBets"),
        "participants": [
            {
                "id": item.get("id"),
                "name": " ".join(part for part in [item.get("preferredName", ""), item.get("lastName", "")] if part).strip()
                or " ".join(part for part in [item.get("firstName", ""), item.get("lastName", "")] if part).strip(),
                "side": item.get("side"),
            }
            for item in players
            if item.get("id")
        ],
        "extra_context": {
            "purse": best.get("purse"),
            "cutline": best.get("cutline"),
            "projected_cutline": best.get("projectedCutline"),
            "cut_round": best.get("cutRound"),
            "total_rounds": best.get("totalRounds"),
        },
        "source_urls": [source_url],
        "match_score": best_score,
        "checked_at": datetime.now(UTC).isoformat(),
    }, ""


def summarize_bc_core_context(
    operator_context: dict[str, Any] | None = None,
    event_context: dict[str, Any] | None = None,
    expertise_context: dict[str, Any] | None = None,
) -> str:
    lines: list[str] = []
    operator_context = operator_context or {}
    event_context = event_context or {}
    expertise_context = expertise_context or {}

    if operator_context.get("matched"):
        lines.extend([
            "BC CORE OPERATOR CONTEXT:",
            f"- Operator match: {operator_context.get('parent_name') or operator_context.get('sportsbook_name')}",
        ])
        if operator_context.get("requested_state_supported") is not None:
            lines.append(
                f"- Requested state supported: {'yes' if operator_context.get('requested_state_supported') else 'no'}"
            )
        coverage = operator_context.get("coverage") or {}
        if coverage.get("checked") and coverage.get("supported") is not None:
            lines.append(f"- BC Core coverage for this sport: {'yes' if coverage.get('supported') else 'no'}")
        states = operator_context.get("states") or []
        if states:
            lines.append(f"- BC Core state list: {', '.join(states[:25])}")
    elif operator_context.get("reason"):
        lines.extend([
            "BC CORE OPERATOR CONTEXT:",
            f"- {operator_context.get('reason')}",
        ])

    if event_context.get("matched"):
        lines.extend([
            "BC CORE EVENT CONTEXT:",
            f"- Matched event: {event_context.get('event_name') or event_context.get('headline')}",
        ])
        if event_context.get("scheduled_date"):
            lines.append(f"- Scheduled date: {event_context.get('scheduled_date')}")
        if event_context.get("network"):
            lines.append(f"- Broadcast/network: {event_context.get('network')}")
        if event_context.get("season_name"):
            lines.append(f"- Season context: {event_context.get('season_name')}")
    elif event_context.get("reason"):
        lines.extend([
            "BC CORE EVENT CONTEXT:",
            f"- {event_context.get('reason')}",
        ])

    editorial_points = expertise_context.get("editorial_points") or []
    if editorial_points:
        lines.append("BC CORE EXPERTISE NOTES:")
        lines.extend(f"- {point}" for point in editorial_points[:5])
    elif expertise_context.get("reason"):
        lines.extend([
            "BC CORE EXPERTISE NOTES:",
            f"- {expertise_context.get('reason')}",
        ])

    return "\n".join(lines).strip()
