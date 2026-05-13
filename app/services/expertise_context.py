from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from statistics import mean

from app.services.bc_core import fetch_bc_core_json, get_bc_core_base_url

TEAM_STATS_PATHS = {
    "nba": "/nba/seasons/team/{team_id}/stats",
    "wnba": "/wnba/seasons/team/{team_id}/stats",
    "ncaab": "/ncaamb/seasons/team/{team_id}/stats",
    "ncaawb": "/ncaawb/seasons/team/{team_id}/stats",
    "nfl": "/nfl/seasons/team/{team_id}/stats",
    "ncaaf": "/football/12/seasons/team/{team_id}/stats",
    "mlb": "/mlb/seasons/team/{team_id}/stats",
    "nhl": "/nhl/seasons/team/{team_id}/stats",
}

STANDINGS_PATHS = {
    "nba": "/nba/seasons/standings",
    "wnba": "/wnba/seasons/standings",
    "ncaab": "/ncaamb/seasons/standings",
    "ncaawb": "/ncaawb/seasons/standings",
    "nfl": "/nfl/seasons/standings",
    "mlb": "/mlb/seasons/standings",
    "nhl": "/nhl/seasons/standings",
}


async def _build_golf_expertise_context(bc_event: dict) -> tuple[dict, str]:
    base_url = get_bc_core_base_url()
    event_id = bc_event.get("event_id")
    league_id = bc_event.get("league_id")
    if not event_id or not league_id:
        return {}, "Missing golf event_id or league_id"

    stats_path = f"/golf/{league_id}/events/{event_id}/stats"
    stats_payload = await fetch_bc_core_json(stats_path)
    rounds = ((stats_payload.get("result") or {}).get("rounds") or [])
    first_round = rounds[0] if rounds else {}
    player_stats = first_round.get("playerStats", []) or []
    participant_map = {item.get("id"): item.get("name") for item in bc_event.get("participants", []) if item.get("id")}
    leaders = [
        {
            "player_id": item.get("playerId"),
            "name": participant_map.get(item.get("playerId"), f"Player {item.get('playerId')}"),
            "score": item.get("score"),
            "thru": item.get("thru"),
            "birdies": item.get("birdies"),
            "bogeys": item.get("bogeys"),
        }
        for item in player_stats
        if item.get("thru") is not None and item.get("score") is not None
    ]
    leaders.sort(key=lambda item: (item.get("score", 999), -(item.get("thru") or 0)))
    leaders = leaders[:5]

    extra = bc_event.get("extra_context", {}) or {}
    editorial_points = []
    if extra.get("total_rounds") and extra.get("purse"):
        editorial_points.append(
            f"{bc_event.get('event_name', 'This event')} is a {extra['total_rounds']}-round tournament with a ${extra['purse']:,.0f} purse."
        )
    if leaders:
        leader = leaders[0]
        editorial_points.append(
            f"{leader['name']} is {leader['score']:+} through {leader['thru']} holes in the current round."
        )
    if len(leaders) > 1:
        second = leaders[1]
        editorial_points.append(
            f"{second['name']} is also in the mix at {second['score']:+} through {second['thru']}."
        )

    return {
        "matched": True,
        "provider": "bc_core",
        "sport": "golf",
        "event_id": event_id,
        "league_id": league_id,
        "event_name": bc_event.get("event_name") or bc_event.get("headline", ""),
        "leaders": leaders,
        "editorial_points": editorial_points,
        "source_urls": [*bc_event.get("source_urls", []), f"{base_url}{stats_path}"],
        "checked_at": datetime.now(UTC).isoformat(),
    }, ""


def _per_game(total: int | float | None, games: int | None) -> float | None:
    if not games or total is None:
        return None
    return round(float(total) / float(games), 2)


def _pct(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value) * 100, 1)


def _safe_number(value: int | float | None) -> float | int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    rounded = round(float(value), 2)
    return int(rounded) if rounded.is_integer() else rounded


def _find_record(standing: dict, record_name: str) -> dict | None:
    for record in standing.get("records", []):
        if ((record.get("standingType") or {}).get("name") or "") == record_name:
            return record
    return None


def _summarize_standing(standing: dict | None) -> dict:
    if not standing:
        return {}
    last_ten = _find_record(standing, "Last10") or {}
    streak_type = (((standing.get("streakType") or {}).get("name")) or "").lower()
    streak_length = standing.get("streakLength")
    if streak_type and streak_length:
        streak = f"{streak_type.title()} {streak_length}"
    else:
        streak = ""
    return {
        "wins": standing.get("wins"),
        "losses": standing.get("losses"),
        "win_percentage": _safe_number(standing.get("winPercentage")),
        "conference_rank": standing.get("conferenceRank"),
        "division_rank": standing.get("divisionRank"),
        "streak": streak,
        "last_ten": f"{last_ten.get('wins', 0)}-{last_ten.get('losses', 0)}" if last_ten else "",
    }


def _summarize_team_stats(team_name: str, team_stats: dict, opponent_stats: dict, standing: dict | None) -> dict:
    games = team_stats.get("games") or opponent_stats.get("games") or 0
    points_for = _per_game(team_stats.get("points"), games)
    points_against = _per_game(opponent_stats.get("points"), games)
    net_points = round(points_for - points_against, 2) if points_for is not None and points_against is not None else None
    assists = _per_game(team_stats.get("assists"), games)
    rebounds = _per_game(team_stats.get("playerRebounds"), games)
    threes = _per_game(team_stats.get("threePointsMade"), games)
    ts_pct = _pct(team_stats.get("trueShootingPct"))
    opp_efg_pct = _pct(opponent_stats.get("effectiveFgPct"))
    standing_summary = _summarize_standing(standing)

    editorial_angles = [
        f"{team_name} averaged {points_for} points per game with a {net_points:+} scoring margin."
        if points_for is not None and net_points is not None else "",
        f"{team_name} created {assists} assists and {rebounds} rebounds per game."
        if assists is not None and rebounds is not None else "",
        f"{team_name} converted {threes} threes per game with a {ts_pct}% true shooting clip."
        if threes is not None and ts_pct is not None else "",
        f"{team_name} allowed a {opp_efg_pct}% opponent effective field goal rate."
        if opp_efg_pct is not None else "",
        f"{team_name} finished at {standing_summary.get('wins')}-{standing_summary.get('losses')}."
        if standing_summary.get("wins") is not None else "",
    ]
    return {
        "team_name": team_name,
        "games": games,
        "record": standing_summary,
        "per_game": {
            "points_for": points_for,
            "points_against": points_against,
            "net_points": net_points,
            "assists": assists,
            "rebounds": rebounds,
            "threes_made": threes,
        },
        "advanced": {
            "true_shooting_pct": ts_pct,
            "opponent_effective_fg_pct": opp_efg_pct,
        },
        "editorial_angles": [line for line in editorial_angles if line][:3],
    }


async def _fetch_team_summary(
    sport: str,
    team_id: int,
    team_name: str,
    year: int | None,
    season_type: str,
    standing: dict | None,
) -> tuple[dict, list[str]]:
    base_url = get_bc_core_base_url()
    stats_path = TEAM_STATS_PATHS.get(sport)
    if not stats_path:
        return {}, []
    params = {"seasonType": season_type}
    if year is not None:
        params["year"] = year
    path = stats_path.format(team_id=team_id)
    stats_payload = await fetch_bc_core_json(path, params=params)
    stats_result = stats_payload.get("result", {}) or {}
    raw_team_stats = stats_result.get("teamStats") or {}
    raw_opponent_stats = stats_result.get("opponentStats") or {}
    team_stats = raw_team_stats[0] if isinstance(raw_team_stats, list) and raw_team_stats else raw_team_stats
    opponent_stats = raw_opponent_stats[0] if isinstance(raw_opponent_stats, list) and raw_opponent_stats else raw_opponent_stats
    source_urls = [f"{base_url}{path}"]
    summary = _summarize_team_stats(team_name, team_stats or {}, opponent_stats or {}, standing)
    summary["team_id"] = team_id
    summary["season_year"] = year
    summary["season_type"] = season_type
    return summary, source_urls


async def build_expertise_context(source_facts: dict, sports_context: dict) -> tuple[dict, str]:
    base_url = get_bc_core_base_url()
    bc_event = sports_context.get("bc_core_event", {}) if isinstance(sports_context.get("bc_core_event"), dict) else {}
    if not bc_event.get("matched"):
        return {
            "matched": False,
            "provider": "fallback",
            "reason": "No matched BC Core event available for expertise enrichment",
            "source_urls": sports_context.get("source_urls", []),
        }, "No matched BC Core event available for expertise enrichment"

    sport = (bc_event.get("sport") or "").lower()
    away_team_id = bc_event.get("away_team_id")
    home_team_id = bc_event.get("home_team_id")
    if sport == "golf":
        try:
            payload, reason = await _build_golf_expertise_context(bc_event)
            if payload:
                return payload, reason
        except Exception as exc:
            return {
                "matched": False,
                "provider": "fallback",
                "reason": str(exc),
                "source_urls": bc_event.get("source_urls", []),
            }, str(exc)
    if sport not in TEAM_STATS_PATHS or not away_team_id or not home_team_id:
        return {
            "matched": False,
            "provider": "fallback",
            "reason": f"No expertise endpoint mapping for sport '{sport or 'unknown'}' or missing team IDs",
            "source_urls": bc_event.get("source_urls", []),
        }, f"No expertise endpoint mapping for sport '{sport or 'unknown'}' or missing team IDs"

    season_year = bc_event.get("season_year")
    season_type = bc_event.get("season_type") or "Reg"
    params = {"seasonType": season_type}
    if season_year is not None:
        params["year"] = season_year

    standings_map: dict[int, dict] = {}
    standings_urls: list[str] = []
    standings_path = STANDINGS_PATHS.get(sport)
    if standings_path:
        try:
            standings_payload = await fetch_bc_core_json(standings_path, params=params)
            standings_map = {item.get("teamId"): item for item in standings_payload.get("results", []) if item.get("teamId")}
            standings_urls.append(f"{base_url}{standings_path}")
        except Exception:
            standings_map = {}

    away_summary, home_summary = await asyncio.gather(
        _fetch_team_summary(sport, int(away_team_id), bc_event.get("away_team", ""), season_year, season_type, standings_map.get(int(away_team_id))),
        _fetch_team_summary(sport, int(home_team_id), bc_event.get("home_team", ""), season_year, season_type, standings_map.get(int(home_team_id))),
    )
    away_summary, away_urls = away_summary
    home_summary, home_urls = home_summary

    editorial_points = [*away_summary.get("editorial_angles", [])[:2], *home_summary.get("editorial_angles", [])[:2]]
    scoring_margin_values = [
        value
        for value in [
            away_summary.get("per_game", {}).get("net_points"),
            home_summary.get("per_game", {}).get("net_points"),
        ]
        if isinstance(value, (int, float))
    ]
    if scoring_margin_values:
        editorial_points.append(
            f"Combined scoring-margin context averages {round(mean(scoring_margin_values), 2):+} points across the two teams."
        )

    source_urls = list(dict.fromkeys([*bc_event.get("source_urls", []), *standings_urls, *away_urls, *home_urls]))
    return {
        "matched": True,
        "provider": "bc_core",
        "sport": sport,
        "season_year": season_year,
        "season_type": season_type,
        "event_id": bc_event.get("event_id"),
        "event_name": bc_event.get("event_name") or bc_event.get("headline", ""),
        "teams": {"away": away_summary, "home": home_summary},
        "editorial_points": [point for point in editorial_points if point][:5],
        "source_urls": source_urls,
        "checked_at": datetime.now(UTC).isoformat(),
    }, ""
