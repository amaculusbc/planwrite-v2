import pytest

from app.services import expertise_context


@pytest.mark.asyncio
async def test_build_expertise_context_adds_injuries_weather_and_trends(monkeypatch):
    async def fake_fetch(path: str, *, params=None):
        if path == "/nba/seasons/standings":
            return {
                "results": [
                    {"teamId": 101, "wins": 55, "losses": 27, "winPercentage": 0.671},
                    {"teamId": 202, "wins": 41, "losses": 41, "winPercentage": 0.500},
                ]
            }
        if path == "/nba/seasons/team/101/stats":
            return {
                "result": {
                    "teamStats": {"games": 82, "points": 9594, "assists": 2132, "playerRebounds": 3690, "threePointsMade": 1198, "trueShootingPct": 0.602},
                    "opponentStats": {"games": 82, "points": 9130, "effectiveFgPct": 0.521},
                }
            }
        if path == "/nba/seasons/team/202/stats":
            return {
                "result": {
                    "teamStats": {"games": 82, "points": 9312, "assists": 1984, "playerRebounds": 3518, "threePointsMade": 982, "trueShootingPct": 0.573},
                    "opponentStats": {"games": 82, "points": 9348, "effectiveFgPct": 0.548},
                }
            }
        if path == "/basketball/2/injuries":
            return {
                "results": [
                    {
                        "teamId": 101,
                        "playerStatusType": {"name": "Out"},
                        "playerCondition": {"name": "Hamstring"},
                        "modifiedDate": "2026-05-19T09:00:00Z",
                    },
                    {
                        "teamId": 101,
                        "playerStatusType": {"name": "Questionable"},
                        "playerCondition": {"name": "Ankle"},
                        "modifiedDate": "2026-05-19T10:00:00Z",
                    },
                    {
                        "teamId": 202,
                        "playerStatusType": {"name": "Out"},
                        "playerCondition": {"name": "Knee"},
                        "modifiedDate": "2026-05-19T11:00:00Z",
                    },
                ]
            }
        if path == "/basketball/2/weather":
            return {
                "results": [
                    {
                        "venueId": 999,
                        "temperature": 68,
                        "windSpeed": 12,
                        "windDirection": {"name": "NW"},
                        "precipitationProbability": 20,
                        "weatherCondition": {"name": "Partly Cloudy"},
                    }
                ]
            }
        if path == "/basketball/2/team/101/trends":
            return {
                "results": [
                    {
                        "timeFrame": {"name": "Last10"},
                        "split": {"name": "Overall"},
                        "ml": {"wins": 8, "losses": 2, "ties": 0},
                        "ats": {"wins": 7, "losses": 3, "ties": 0},
                        "total": {"overs": 6, "unders": 4, "ties": 0},
                    }
                ]
            }
        if path == "/basketball/2/team/202/trends":
            return {
                "results": [
                    {
                        "timeFrame": {"name": "Last10"},
                        "split": {"name": "Overall"},
                        "ml": {"wins": 4, "losses": 6, "ties": 0},
                        "ats": {"wins": 5, "losses": 5, "ties": 0},
                        "total": {"overs": 5, "unders": 5, "ties": 0},
                    }
                ]
            }
        if path == "/basketball/2/team/101/trends/opponent/202":
            return {
                "results": [
                    {
                        "timeFrame": {"name": "Season"},
                        "split": {"name": "Overall"},
                        "ml": {"wins": 2, "losses": 1, "ties": 0},
                        "ats": {"wins": 2, "losses": 1, "ties": 0},
                        "total": {"overs": 1, "unders": 2, "ties": 0},
                    }
                ]
            }
        if path == "/basketball/2/team/202/trends/opponent/101":
            return {
                "results": [
                    {
                        "timeFrame": {"name": "Season"},
                        "split": {"name": "Overall"},
                        "ml": {"wins": 1, "losses": 2, "ties": 0},
                        "ats": {"wins": 1, "losses": 2, "ties": 0},
                        "total": {"overs": 2, "unders": 1, "ties": 0},
                    }
                ]
            }
        if path == "/basketball/2/team/101/season/review":
            return {
                "results": [
                    {"eventDate": "2026-05-10T00:00:00Z", "homeTeam": 101, "awayTeam": 202, "homeScore": 118, "awayScore": 111},
                    {"eventDate": "2026-05-08T00:00:00Z", "homeTeam": 303, "awayTeam": 101, "homeScore": 102, "awayScore": 109},
                    {"eventDate": "2026-05-06T00:00:00Z", "homeTeam": 101, "awayTeam": 404, "homeScore": 121, "awayScore": 115},
                ]
            }
        if path == "/basketball/2/team/202/season/review":
            return {
                "results": [
                    {"eventDate": "2026-05-10T00:00:00Z", "homeTeam": 101, "awayTeam": 202, "homeScore": 118, "awayScore": 111},
                    {"eventDate": "2026-05-07T00:00:00Z", "homeTeam": 202, "awayTeam": 505, "homeScore": 113, "awayScore": 110},
                    {"eventDate": "2026-05-05T00:00:00Z", "homeTeam": 606, "awayTeam": 202, "homeScore": 119, "awayScore": 108},
                ]
            }
        raise AssertionError(f"Unexpected BC Core path: {path}")

    monkeypatch.setattr(expertise_context, "fetch_bc_core_json", fake_fetch)
    monkeypatch.setattr(expertise_context, "get_bc_core_base_url", lambda: "https://core.example")

    payload, reason = await expertise_context.build_expertise_context(
        {},
        {
            "bc_core_event": {
                "matched": True,
                "sport": "nba",
                "league_id": 2,
                "event_id": 77,
                "event_name": "Boston Celtics at Miami Heat",
                "away_team_id": 101,
                "away_team": "Boston Celtics",
                "home_team_id": 202,
                "home_team": "Miami Heat",
                "venue_id": 999,
                "season_year": 2026,
                "season_type": "Reg",
                "source_urls": ["https://core.example/nba/events"],
            },
            "source_urls": ["https://core.example/nba/events"],
        },
    )

    assert reason == ""
    assert payload["matched"] is True
    assert payload["injuries"]["matched"] is True
    assert payload["injuries"]["teams"]["away"]["count"] == 2
    assert payload["weather"]["matched"] is True
    assert payload["weather"]["forecast"]["temperature"] == 68
    assert payload["trends"]["matched"] is True
    assert payload["trends"]["teams"]["away"]["last10_overall"]["ats"] == "7-3"
    assert payload["trends"]["teams"]["away"]["season_review"]["recent_record"] == "3-0"
    assert any("injury listings" in point for point in payload["editorial_points"])
    assert any("7-3 ATS" in point for point in payload["editorial_points"])
    assert any("/basketball/2/injuries" in url for url in payload["source_urls"])
    assert any("/basketball/2/team/101/season/review" in url for url in payload["source_urls"])


@pytest.mark.asyncio
async def test_build_expertise_context_tolerates_missing_optional_enrichment(monkeypatch):
    async def fake_fetch(path: str, *, params=None):
        if path == "/nfl/seasons/standings":
            return {"results": []}
        if path == "/nfl/seasons/team/11/stats":
            return {"result": {"teamStats": {"games": 17, "points": 425}, "opponentStats": {"games": 17, "points": 389}}}
        if path == "/nfl/seasons/team/22/stats":
            return {"result": {"teamStats": {"games": 17, "points": 371}, "opponentStats": {"games": 17, "points": 401}}}
        if path == "/football/nfl/injuries":
            raise RuntimeError("proxy timeout")
        if path == "/football/9/weather":
            raise RuntimeError("proxy timeout")
        if "/trends" in path or "/season/review" in path:
            raise RuntimeError("proxy timeout")
        raise AssertionError(f"Unexpected BC Core path: {path}")

    monkeypatch.setattr(expertise_context, "fetch_bc_core_json", fake_fetch)
    monkeypatch.setattr(expertise_context, "get_bc_core_base_url", lambda: "https://core.example")

    payload, reason = await expertise_context.build_expertise_context(
        {},
        {
            "bc_core_event": {
                "matched": True,
                "sport": "nfl",
                "league_id": 9,
                "event_id": 88,
                "event_name": "Jets at Bills",
                "away_team_id": 11,
                "away_team": "Jets",
                "home_team_id": 22,
                "home_team": "Bills",
                "season_year": 2026,
                "season_type": "Reg",
                "source_urls": ["https://core.example/nfl/events"],
            },
            "source_urls": ["https://core.example/nfl/events"],
        },
    )

    assert reason == ""
    assert payload["matched"] is True
    assert payload["injuries"]["matched"] is False
    assert payload["weather"]["matched"] is False
    assert payload["trends"]["matched"] is False
    assert payload["teams"]["away"]["per_game"]["points_for"] == 25.0
