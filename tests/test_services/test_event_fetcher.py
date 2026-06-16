"""Event fetcher tests for supported sports and ESPN path mapping."""

from datetime import datetime

import pytest

import app.services.event_fetcher as event_fetcher
from app.services.odds_fetcher import OddsFetcher


def test_get_available_sports_includes_soccer():
    sports = event_fetcher.get_available_sports()
    assert sports["soccer"] == "Soccer"


@pytest.mark.asyncio
async def test_get_games_for_date_uses_world_cup_soccer_scoreboard(monkeypatch):
    calls: list[str] = []

    async def fake_get_json(url, **kwargs):
        calls.append(url)
        return {
            "events": [
                {
                    "id": "760415",
                    "date": "2026-06-11T19:00Z",
                    "name": "South Africa at Mexico",
                    "shortName": "RSA @ MEX",
                    "season": {"year": 2026, "type": 13802},
                    "competitions": [
                        {
                            "broadcasts": [{"names": ["FOX", "Peacock"]}],
                            "competitors": [
                                {
                                    "homeAway": "home",
                                    "team": {
                                        "displayName": "Mexico",
                                        "abbreviation": "MEX",
                                    },
                                },
                                {
                                    "homeAway": "away",
                                    "team": {
                                        "displayName": "South Africa",
                                        "abbreviation": "RSA",
                                    },
                                },
                            ],
                        }
                    ],
                }
            ]
        }

    monkeypatch.setattr(event_fetcher, "get_json", fake_get_json)

    games = await event_fetcher.get_games_for_date("soccer", datetime(2026, 6, 11))

    assert calls
    assert "/sports/soccer/fifa.world/scoreboard?dates=20260611" in calls[0]
    assert games[0]["away_team"] == "South Africa"
    assert games[0]["home_team"] == "Mexico"
    assert games[0]["away_abbrev"] == "RSA"
    assert games[0]["home_abbrev"] == "MEX"
    assert games[0]["network"] == "FOX"
    assert games[0]["sport"] == "SOCCER"
    assert event_fetcher.format_game_start_time(games[0]) == "Thu, Jun 11, 3:00 PM ET"


def test_odds_fetcher_treats_soccer_as_daily_without_nfl_fallback():
    fetcher = OddsFetcher("soccer")
    assert fetcher.sport_path == "soccer"
    assert fetcher.is_daily_sport is True
