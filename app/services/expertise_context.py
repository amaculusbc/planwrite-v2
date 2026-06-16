from __future__ import annotations

import asyncio
from collections import Counter, defaultdict
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

SPORT_API_FAMILIES = {
    "nba": "basketball",
    "wnba": "basketball",
    "ncaab": "basketball",
    "ncaawb": "basketball",
    "nfl": "football",
    "ncaaf": "football",
    "mlb": "baseball",
    "nhl": "hockey",
    "soccer": "soccer",
}

OUTDOOR_WEATHER_SPORTS = {"nfl", "ncaaf", "mlb", "soccer"}

INJURY_PATHS = {
    "nba": "/basketball/{league_id}/injuries",
    "wnba": "/basketball/{league_id}/injuries",
    "ncaab": "/basketball/{league_id}/injuries",
    "ncaawb": "/basketball/{league_id}/injuries",
    "nfl": "/football/nfl/injuries",
    "ncaaf": "/football/ncaafb/injuries",
    "nhl": "/hockey/{league_id}/injuries",
}


def _format_record(record: dict | None) -> str:
    if not record:
        return ""
    wins = record.get("wins")
    losses = record.get("losses")
    ties = record.get("ties")
    if wins is None or losses is None:
        return ""
    if ties:
        return f"{wins}-{losses}-{ties}"
    return f"{wins}-{losses}"


def _format_total_record(record: dict | None) -> str:
    if not record:
        return ""
    overs = record.get("overs")
    unders = record.get("unders")
    ties = record.get("ties")
    if overs is None or unders is None:
        return ""
    if ties:
        return f"{overs}-{unders}-{ties}"
    return f"{overs}-{unders}"


def _pick_primary_weather_entry(results: list[dict], venue_id: int | None) -> dict:
    if venue_id is not None:
        for item in results:
            if item.get("venueId") == venue_id:
                return item
    return results[0] if len(results) == 1 else {}


def _sport_weather_editorial_enabled(sport: str) -> bool:
    return sport in OUTDOOR_WEATHER_SPORTS


def _label_with_count(counter: Counter) -> str:
    parts = []
    for name, count in counter.most_common(3):
        if not name:
            continue
        parts.append(f"{count} {name}")
    return ", ".join(parts)


def _team_name_lookup(bc_event: dict) -> dict[int, str]:
    mapping: dict[int, str] = {}
    away_id = bc_event.get("away_team_id")
    home_id = bc_event.get("home_team_id")
    if away_id:
        mapping[int(away_id)] = bc_event.get("away_team", "") or f"Team {away_id}"
    if home_id:
        mapping[int(home_id)] = bc_event.get("home_team", "") or f"Team {home_id}"
    return mapping


def _summarize_team_injuries(team_name: str, items: list[dict]) -> dict:
    status_counts: Counter = Counter()
    condition_counts: Counter = Counter()
    latest_modified = ""
    for item in items:
        status = ((item.get("playerStatusType") or {}).get("name") or "").strip()
        condition = ((item.get("playerCondition") or {}).get("name") or "").strip()
        modified = str(item.get("modifiedDate") or "")
        if status:
            status_counts[status] += 1
        if condition:
            condition_counts[condition] += 1
        if modified and modified > latest_modified:
            latest_modified = modified
    return {
        "team_name": team_name,
        "count": len(items),
        "status_counts": dict(status_counts),
        "condition_counts": dict(condition_counts),
        "latest_modified": latest_modified,
    }


def _injury_editorial_points(team_summaries: list[dict]) -> list[str]:
    points: list[str] = []
    for summary in team_summaries:
        count = summary.get("count") or 0
        if count <= 0:
            continue
        statuses = _label_with_count(Counter(summary.get("status_counts") or {}))
        conditions = _label_with_count(Counter(summary.get("condition_counts") or {}))
        parts = [f"{summary.get('team_name')} has {count} active BC Core injury listings"]
        if statuses:
            parts.append(f"with {statuses}")
        if conditions:
            parts.append(f"most often tied to {conditions}")
        points.append(" ".join(parts) + ".")
    return points


def _summarize_weather_entry(item: dict) -> dict:
    return {
        "venue_id": item.get("venueId"),
        "effective_date": item.get("effectiveDate"),
        "condition": ((item.get("weatherCondition") or {}).get("name") or "").strip(),
        "temperature": _safe_number(item.get("temperature")),
        "apparent_temperature": _safe_number(item.get("apparentTemperature")),
        "wind_speed": _safe_number(item.get("windSpeed")),
        "wind_direction": ((item.get("windDirection") or {}).get("name") or "").strip(),
        "precipitation_probability": _safe_number(item.get("precipitationProbability")),
        "humidity": _safe_number(item.get("humidity")),
        "cloud_cover": _safe_number(item.get("cloudCover")),
    }


def _weather_editorial_point(summary: dict) -> str:
    if not summary:
        return ""
    phrases: list[str] = []
    temperature = summary.get("temperature")
    wind_speed = summary.get("wind_speed")
    precip = summary.get("precipitation_probability")
    condition = summary.get("condition")
    if temperature is not None:
        phrases.append(f"{temperature}° conditions")
    if wind_speed is not None:
        direction = f" {summary.get('wind_direction')}" if summary.get("wind_direction") else ""
        phrases.append(f"{wind_speed} mph{direction} wind".replace("  ", " ").strip())
    if precip is not None:
        phrases.append(f"{precip}% precipitation chances")
    if condition:
        phrases.append(condition.lower())
    if not phrases:
        return ""
    return f"BC Core weather at the venue points to {'; '.join(phrases[:4])}."


def _trend_payload_path(sport: str, league_id: int, team_id: int, suffix: str) -> str | None:
    family = SPORT_API_FAMILIES.get(sport)
    if not family:
        return None
    return f"/{family}/{league_id}/team/{team_id}/{suffix}"


def _pick_trend_record(results: list[dict], *, time_frame: str, split: str) -> dict:
    for item in results:
        item_time_frame = (((item.get("timeFrame") or {}).get("name")) or "").strip()
        item_split = (((item.get("split") or {}).get("name")) or "").strip()
        if item_time_frame == time_frame and item_split == split:
            return item
    return results[0] if results else {}


def _summarize_trend_record(record: dict) -> dict:
    ml = _format_record(record.get("ml") or {})
    ats = _format_record(record.get("ats") or {})
    total = _format_total_record(record.get("total") or {})
    return {
        "time_frame": (((record.get("timeFrame") or {}).get("name")) or "").strip(),
        "split": (((record.get("split") or {}).get("name")) or "").strip(),
        "ml": ml,
        "ats": ats,
        "total": total,
    }


def _trend_editorial_points(team_name: str, team_trend: dict, opponent_trend: dict, opponent_name: str) -> list[str]:
    points: list[str] = []
    if team_trend.get("ats"):
        points.append(
            f"{team_name} is {team_trend['ats']} ATS in BC Core's {team_trend.get('time_frame') or 'recent'} {team_trend.get('split') or 'overall'} trend sample."
        )
    elif team_trend.get("ml"):
        points.append(
            f"{team_name} is {team_trend['ml']} straight up in BC Core's {team_trend.get('time_frame') or 'recent'} {team_trend.get('split') or 'overall'} trend sample."
        )
    if opponent_name and opponent_trend.get("ats"):
        points.append(
            f"In BC Core's matchup trend sample against {opponent_name}, {team_name} is {opponent_trend['ats']} ATS."
        )
    elif opponent_name and opponent_trend.get("ml"):
        points.append(
            f"In BC Core's matchup trend sample against {opponent_name}, {team_name} is {opponent_trend['ml']} straight up."
        )
    return points[:2]


def _summarize_season_review(team_id: int, team_name: str, results: list[dict]) -> dict:
    recent_games: list[dict] = []
    for item in results:
        home_team = item.get("homeTeam")
        away_team = item.get("awayTeam")
        home_score = item.get("homeScore")
        away_score = item.get("awayScore")
        if home_score is None or away_score is None:
            continue
        if team_id == home_team:
            team_score = home_score
            opponent_score = away_score
            opponent_team_id = away_team
        elif team_id == away_team:
            team_score = away_score
            opponent_score = home_score
            opponent_team_id = home_team
        else:
            continue
        margin = team_score - opponent_score
        recent_games.append(
            {
                "event_id": item.get("eventId"),
                "event_date": item.get("eventDate"),
                "opponent_team_id": opponent_team_id,
                "team_score": team_score,
                "opponent_score": opponent_score,
                "margin": margin,
                "won": margin > 0,
            }
        )
    recent_games.sort(key=lambda game: str(game.get("event_date") or ""), reverse=True)
    recent_games = recent_games[:5]
    if not recent_games:
        return {"team_name": team_name, "games": []}
    wins = sum(1 for game in recent_games if game.get("won"))
    average_margin = round(mean([game.get("margin", 0) for game in recent_games]), 2)
    return {
        "team_name": team_name,
        "games": recent_games,
        "recent_record": f"{wins}-{len(recent_games) - wins}",
        "average_margin": average_margin,
    }


def _season_review_editorial_point(summary: dict) -> str:
    if not summary.get("games"):
        return ""
    average_margin = summary.get("average_margin")
    margin_text = f" with a {average_margin:+} average margin" if average_margin is not None else ""
    return f"{summary.get('team_name')} went {summary.get('recent_record')} over its last {len(summary.get('games', []))} completed games{margin_text}."


async def _fetch_injury_context(sport: str, league_id: int | None, bc_event: dict) -> tuple[dict, list[str], list[str]]:
    path_template = INJURY_PATHS.get(sport)
    if not path_template:
        return {"matched": False, "reason": f"No injury endpoint mapping for sport '{sport}'"}, [], []
    if "{league_id}" in path_template and league_id is None:
        return {"matched": False, "reason": "Missing league_id for injury lookup"}, [], []

    path = path_template.format(league_id=league_id)
    params = {
        "includeActive": "true",
        "includeHistoric": "false",
        "teamIds": ",".join(str(item) for item in [bc_event.get("away_team_id"), bc_event.get("home_team_id")] if item),
    }
    payload = await fetch_bc_core_json(path, params=params)
    results = payload.get("results", []) or []
    team_lookup = _team_name_lookup(bc_event)
    grouped: dict[int, list[dict]] = defaultdict(list)
    for item in results:
        team_id = item.get("teamId")
        if team_id is None:
            continue
        grouped[int(team_id)].append(item)

    away_team_id = bc_event.get("away_team_id")
    home_team_id = bc_event.get("home_team_id")
    away_summary = _summarize_team_injuries(team_lookup.get(int(away_team_id), ""), grouped.get(int(away_team_id), [])) if away_team_id else {}
    home_summary = _summarize_team_injuries(team_lookup.get(int(home_team_id), ""), grouped.get(int(home_team_id), [])) if home_team_id else {}
    summaries = [summary for summary in [away_summary, home_summary] if summary]
    return {
        "matched": True,
        "teams": {"away": away_summary, "home": home_summary},
        "total_count": sum((summary.get("count") or 0) for summary in summaries),
    }, [f"{get_bc_core_base_url()}{path}"], _injury_editorial_points(summaries)


async def _fetch_weather_context(sport: str, league_id: int | None, bc_event: dict) -> tuple[dict, list[str], list[str]]:
    family = SPORT_API_FAMILIES.get(sport)
    if not family or league_id is None:
        return {"matched": False, "reason": f"No weather endpoint mapping for sport '{sport}'"}, [], []

    path = f"/{family}/{league_id}/weather"
    payload = await fetch_bc_core_json(path)
    results = payload.get("results", []) or []
    selected = _pick_primary_weather_entry(results, bc_event.get("venue_id"))
    if not selected:
        return {"matched": False, "reason": "No venue-matched weather returned"}, [f"{get_bc_core_base_url()}{path}"], []

    summary = _summarize_weather_entry(selected)
    editorial_point = _weather_editorial_point(summary) if _sport_weather_editorial_enabled(sport) else ""
    return {
        "matched": True,
        "venue_id": summary.get("venue_id"),
        "forecast": summary,
    }, [f"{get_bc_core_base_url()}{path}"], [editorial_point] if editorial_point else []


async def _fetch_team_trend_summary(
    sport: str,
    league_id: int | None,
    team_id: int,
    team_name: str,
    opponent_team_id: int,
    opponent_name: str,
    year: int | None,
    season_type: str,
) -> tuple[dict, list[str], list[str]]:
    trend_path = _trend_payload_path(sport, int(league_id or 0), team_id, "trends")
    opponent_trend_path = _trend_payload_path(sport, int(league_id or 0), team_id, f"trends/opponent/{opponent_team_id}")
    review_path = _trend_payload_path(sport, int(league_id or 0), team_id, "season/review")
    if not trend_path or not opponent_trend_path or not review_path or league_id is None:
        return {"matched": False, "reason": f"No trend endpoint mapping for sport '{sport}'"}, [], []

    trend_params = {"timeFrame": "Last10", "splitType": "Overall"}
    opponent_params = {"timeFrame": "Season", "splitType": "Overall"}
    review_params = {"seasonType": season_type}
    if year is not None:
        review_params["year"] = year

    trend_payload, opponent_trend_payload, review_payload = await asyncio.gather(
        fetch_bc_core_json(trend_path, params=trend_params),
        fetch_bc_core_json(opponent_trend_path, params=opponent_params),
        fetch_bc_core_json(review_path, params=review_params),
        return_exceptions=True,
    )
    team_trend = {}
    opponent_trend = {}
    season_review = {"team_name": team_name, "games": []}
    matched = False
    if not isinstance(trend_payload, Exception):
        team_trend = _summarize_trend_record(
            _pick_trend_record(trend_payload.get("results", []) or [], time_frame="Last10", split="Overall")
        )
        matched = matched or bool(team_trend.get("ats") or team_trend.get("ml") or team_trend.get("total"))
    if not isinstance(opponent_trend_payload, Exception):
        opponent_trend = _summarize_trend_record(
            _pick_trend_record(opponent_trend_payload.get("results", []) or [], time_frame="Season", split="Overall")
        )
        matched = matched or bool(opponent_trend.get("ats") or opponent_trend.get("ml") or opponent_trend.get("total"))
    if not isinstance(review_payload, Exception):
        season_review = _summarize_season_review(team_id, team_name, review_payload.get("results", []) or [])
    editorial_points = _trend_editorial_points(team_name, team_trend, opponent_trend, opponent_name)
    review_point = _season_review_editorial_point(season_review)
    if review_point:
        editorial_points.append(review_point)
    return {
        "matched": matched,
        "team_name": team_name,
        "last10_overall": team_trend,
        "opponent_trend": opponent_trend,
        "season_review": season_review,
    }, [
        f"{get_bc_core_base_url()}{trend_path}",
        f"{get_bc_core_base_url()}{opponent_trend_path}",
        f"{get_bc_core_base_url()}{review_path}",
    ], editorial_points[:3]


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


def _person_name(item: dict | None) -> str:
    item = item or {}
    return (
        str(item.get("shortName") or "").strip()
        or " ".join(str(part).strip() for part in [item.get("firstName"), item.get("lastName")] if str(part or "").strip())
        or str(item.get("name") or "").strip()
    )


def _nested_name(item: dict | None) -> str:
    item = item or {}
    return str(item.get("name") or item.get("shortName") or "").strip()


def _summarize_soccer_absences(results: list[dict]) -> tuple[dict, list[str]]:
    by_team: dict[str, list[str]] = defaultdict(list)
    statuses: Counter = Counter()
    for item in results:
        player = _person_name(item.get("player") if isinstance(item.get("player"), dict) else {})
        status = _nested_name(item.get("playerStatusType") if isinstance(item.get("playerStatusType"), dict) else {})
        condition = _nested_name(item.get("playerStatusCondition") if isinstance(item.get("playerStatusCondition"), dict) else {})
        teams = item.get("teams") if isinstance(item.get("teams"), list) else []
        team_name = _nested_name(teams[0]) if teams else "Unknown team"
        if status:
            statuses[status] += 1
        label = player or "Player"
        if status:
            label = f"{label} ({status})"
        if condition:
            label = f"{label}, {condition}"
        by_team[team_name].append(label)

    points: list[str] = []
    for team_name, players in list(by_team.items())[:2]:
        if players:
            points.append(f"{team_name} has {len(players)} listed player absence{'s' if len(players) != 1 else ''}: {', '.join(players[:3])}.")
    return {
        "matched": bool(results),
        "total_count": len(results),
        "status_counts": dict(statuses),
        "teams": dict(by_team),
    }, points


def _summarize_soccer_lineups(results: list[dict]) -> tuple[dict, list[str]]:
    lineups: list[dict] = []
    points: list[str] = []
    for event_lineup in results:
        for team_lineup in event_lineup.get("teamLineups", []) or []:
            team_name = _nested_name(team_lineup.get("team") if isinstance(team_lineup.get("team"), dict) else {})
            formation = _nested_name(
                team_lineup.get("lineupFormationType") if isinstance(team_lineup.get("lineupFormationType"), dict) else {}
            )
            starter_count = len(team_lineup.get("startingLineup", []) or [])
            reserve_count = len(team_lineup.get("reserves", []) or [])
            official = bool(team_lineup.get("isOfficial"))
            lineups.append(
                {
                    "team_name": team_name,
                    "formation": formation,
                    "starter_count": starter_count,
                    "reserve_count": reserve_count,
                    "official": official,
                }
            )
            if team_name and (formation or starter_count):
                label = "official" if official else "projected"
                formation_text = f" in a {formation}" if formation else ""
                points.append(f"{team_name}'s {label} lineup lists {starter_count or 'multiple'} starters{formation_text}.")
    return {"matched": bool(lineups), "teams": lineups}, points[:2]


def _summarize_soccer_matchups(results: list[dict]) -> tuple[dict, list[str]]:
    completed: list[dict] = []
    for item in results:
        teams = item.get("teams") if isinstance(item.get("teams"), list) else []
        if len(teams) < 2:
            continue
        first, second = teams[0], teams[1]
        completed.append(
            {
                "event_id": item.get("eventId"),
                "name": item.get("name"),
                "scheduled_date": item.get("scheduledDate"),
                "teams": [
                    {"team_id": first.get("teamId"), "team_name": first.get("teamName"), "score": first.get("score")},
                    {"team_id": second.get("teamId"), "team_name": second.get("teamName"), "score": second.get("score")},
                ],
            }
        )

    points: list[str] = []
    if completed:
        latest = completed[0]
        teams = latest.get("teams") or []
        if len(teams) >= 2 and teams[0].get("score") is not None and teams[1].get("score") is not None:
            points.append(
                f"The recent matchup sample includes {len(completed)} meeting{'s' if len(completed) != 1 else ''}; the latest listed score was {teams[0].get('team_name')} {teams[0].get('score')}, {teams[1].get('team_name')} {teams[1].get('score')}."
            )
        else:
            points.append(f"The recent matchup sample includes {len(completed)} previous meeting{'s' if len(completed) != 1 else ''}.")
    return {"matched": bool(completed), "events": completed[:5]}, points


async def _build_soccer_expertise_context(bc_event: dict) -> tuple[dict, str]:
    base_url = get_bc_core_base_url()
    event_id = bc_event.get("event_id")
    league_id = bc_event.get("league_id")
    if not event_id:
        return {}, "Missing soccer event_id"

    matchup_path = f"/soccer/event/{event_id}/participants-matchups/basic"
    lineup_path = f"/soccer/event/{event_id}/lineups"
    absences_path = f"/soccer/event/{event_id}/players/absences"
    fetches = [
        fetch_bc_core_json(matchup_path, params={"numPastEvents": 5}),
        fetch_bc_core_json(lineup_path),
        fetch_bc_core_json(absences_path),
    ]
    weather_path = f"/soccer/{league_id}/weather" if league_id else ""
    if weather_path:
        fetches.append(fetch_bc_core_json(weather_path))

    results = await asyncio.gather(*fetches, return_exceptions=True)
    matchup_payload = results[0] if not isinstance(results[0], Exception) else {}
    lineup_payload = results[1] if not isinstance(results[1], Exception) else {}
    absences_payload = results[2] if not isinstance(results[2], Exception) else {}
    weather_payload = results[3] if len(results) > 3 and not isinstance(results[3], Exception) else {}

    matchups, matchup_points = _summarize_soccer_matchups(matchup_payload.get("results", []) or [])
    lineups, lineup_points = _summarize_soccer_lineups(lineup_payload.get("results", []) or [])
    absences, absence_points = _summarize_soccer_absences(absences_payload.get("results", []) or [])
    weather_results = weather_payload.get("results", []) if isinstance(weather_payload, dict) else []
    weather_selected = _pick_primary_weather_entry(weather_results or [], bc_event.get("venue_id"))
    weather_summary = _summarize_weather_entry(weather_selected) if weather_selected else {}
    weather_point = _weather_editorial_point(weather_summary) if weather_summary else ""

    source_urls = [
        *bc_event.get("source_urls", []),
        f"{base_url}{matchup_path}",
        f"{base_url}{lineup_path}",
        f"{base_url}{absences_path}",
    ]
    if weather_path:
        source_urls.append(f"{base_url}{weather_path}")

    editorial_points = [
        *matchup_points[:1],
        *lineup_points[:2],
        *absence_points[:2],
        weather_point,
    ]

    return {
        "matched": bool(matchups.get("matched") or lineups.get("matched") or absences.get("matched") or weather_summary),
        "provider": "bc_core",
        "sport": "soccer",
        "event_id": event_id,
        "league_id": league_id,
        "event_name": bc_event.get("event_name") or bc_event.get("headline", ""),
        "teams": {
            "away": {"team_id": bc_event.get("away_team_id"), "team_name": bc_event.get("away_team", "")},
            "home": {"team_id": bc_event.get("home_team_id"), "team_name": bc_event.get("home_team", "")},
        },
        "matchups": matchups,
        "lineups": lineups,
        "absences": absences,
        "weather": {"matched": bool(weather_summary), "forecast": weather_summary},
        "editorial_points": [point for point in editorial_points if point][:8],
        "source_urls": list(dict.fromkeys(source_urls)),
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
    if sport == "soccer":
        try:
            payload, reason = await _build_soccer_expertise_context(bc_event)
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
    league_id = bc_event.get("league_id")
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
    injury_context: dict = {"matched": False, "reason": "No injury enrichment fetched"}
    weather_context: dict = {"matched": False, "reason": "No weather enrichment fetched"}
    trend_context: dict = {"matched": False, "reason": "No trend enrichment fetched"}
    injury_urls: list[str] = []
    weather_urls: list[str] = []
    trend_urls: list[str] = []
    injury_points: list[str] = []
    weather_points: list[str] = []
    trend_points: list[str] = []
    enrichment_results = await asyncio.gather(
        _fetch_injury_context(sport, league_id, bc_event),
        _fetch_weather_context(sport, league_id, bc_event),
        _fetch_team_trend_summary(
            sport,
            league_id,
            int(away_team_id),
            bc_event.get("away_team", ""),
            int(home_team_id),
            bc_event.get("home_team", ""),
            season_year,
            season_type,
        ),
        _fetch_team_trend_summary(
            sport,
            league_id,
            int(home_team_id),
            bc_event.get("home_team", ""),
            int(away_team_id),
            bc_event.get("away_team", ""),
            season_year,
            season_type,
        ),
        return_exceptions=True,
    )
    if not isinstance(enrichment_results[0], Exception):
        injury_context, injury_urls, injury_points = enrichment_results[0]
    if not isinstance(enrichment_results[1], Exception):
        weather_context, weather_urls, weather_points = enrichment_results[1]
    away_trend_context: dict = {"matched": False, "reason": "No away-team trend enrichment fetched"}
    home_trend_context: dict = {"matched": False, "reason": "No home-team trend enrichment fetched"}
    if not isinstance(enrichment_results[2], Exception):
        away_trend_context, away_trend_urls, away_trend_points = enrichment_results[2]
        trend_urls.extend(away_trend_urls)
        trend_points.extend(away_trend_points[:2])
    if not isinstance(enrichment_results[3], Exception):
        home_trend_context, home_trend_urls, home_trend_points = enrichment_results[3]
        trend_urls.extend(home_trend_urls)
        trend_points.extend(home_trend_points[:2])
    trend_context = {
        "matched": bool(away_trend_context.get("matched") or home_trend_context.get("matched")),
        "teams": {"away": away_trend_context, "home": home_trend_context},
    }

    editorial_points = [
        *injury_points[:2],
        *weather_points[:1],
        *trend_points[:4],
        *away_summary.get("editorial_angles", [])[:2],
        *home_summary.get("editorial_angles", [])[:2],
    ]
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

    source_urls = list(
        dict.fromkeys(
            [
                *bc_event.get("source_urls", []),
                *standings_urls,
                *away_urls,
                *home_urls,
                *injury_urls,
                *weather_urls,
                *trend_urls,
            ]
        )
    )
    return {
        "matched": True,
        "provider": "bc_core",
        "sport": sport,
        "season_year": season_year,
        "season_type": season_type,
        "event_id": bc_event.get("event_id"),
        "event_name": bc_event.get("event_name") or bc_event.get("headline", ""),
        "teams": {"away": away_summary, "home": home_summary},
        "injuries": injury_context,
        "weather": weather_context,
        "trends": trend_context,
        "editorial_points": [point for point in editorial_points if point][:8],
        "source_urls": source_urls,
        "checked_at": datetime.now(UTC).isoformat(),
    }, ""
