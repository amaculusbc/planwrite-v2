import pytest

from app.services import bc_core


def test_compact_normalize_handles_spacing_and_punctuation():
    assert bc_core._compact_normalize("Bet MGM") == "betmgm"
    assert bc_core._compact_normalize("FanDuel Sportsbook") == "fanduelsportsbook"


@pytest.mark.asyncio
async def test_build_operator_context_does_not_false_match_short_abbreviation(monkeypatch):
    async def fake_get_json(path: str, *, params=None):
        assert path == "/sportsbooks"
        return [
            {
                "id": 72,
                "name": "Nitrogen",
                "abbr": "N",
                "parentId": 642,
                "states": [],
                "isActive": True,
                "parent": {"id": 642, "name": "Nitrogen"},
            }
        ]

    monkeypatch.setattr(bc_core, "_get_json", fake_get_json)
    monkeypatch.setattr(bc_core, "bc_core_configured", lambda: True)

    context, reason = await bc_core.build_operator_context(
        {
            "state": "NJ",
            "primary_offer": {"brand": "Novig"},
            "event": {"sport": "nba"},
        }
    )

    assert context["matched"] is False
    assert reason == "No sportsbook match found"


@pytest.mark.asyncio
async def test_build_operator_context_matches_compact_brand_equivalent(monkeypatch):
    calls: list[tuple[str, dict | None]] = []

    async def fake_get_json(path: str, *, params=None):
        calls.append((path, params))
        if path == "/sportsbooks":
            return [
                {
                    "id": 1,
                    "name": "Bet MGM",
                    "abbr": "MGM",
                    "parentId": 100,
                    "states": ["NJ"],
                    "isActive": True,
                    "parent": {"id": 100, "name": "BetMGM"},
                }
            ]
        if path == "/sportsbooks/coverage":
            return [{"sportsbookParentId": 100}]
        raise AssertionError(path)

    monkeypatch.setattr(bc_core, "_get_json", fake_get_json)
    monkeypatch.setattr(bc_core, "bc_core_configured", lambda: True)

    context, reason = await bc_core.build_operator_context(
        {
            "state": "NJ",
            "primary_offer": {"brand": "BetMGM"},
            "event": {"sport": "nba"},
        }
    )

    assert reason == ""
    assert context["matched"] is True
    assert context["parent_name"] == "BetMGM"
    assert any(path == "/sportsbooks/coverage" for path, _ in calls)


@pytest.mark.asyncio
async def test_build_event_context_prefers_event_matching_both_requested_teams(monkeypatch):
    async def fake_get_json(path: str, *, params=None):
        assert path == "/nba/events"
        return {
            "results": [
                {
                    "id": 1,
                    "leagueId": 2,
                    "name": "Indiana Pacers at New York Knicks",
                    "scheduledDate": "2026-05-20T00:00:00Z",
                    "teams": [
                        {"id": 501, "name": "Indiana Pacers", "side": "AWAY"},
                        {"id": 502, "name": "New York Knicks", "side": "HOME"},
                    ],
                    "players": [],
                    "eventStatus": {"name": "Scheduled"},
                    "season": {"name": "2026 NBA Playoffs"},
                    "seasonSchedule": {"scheduleType": "Postseason"},
                    "broadcast": {"network": "ESPN"},
                },
                {
                    "id": 2,
                    "leagueId": 2,
                    "name": "Cleveland Cavaliers at New York Knicks",
                    "scheduledDate": "2026-05-20T00:00:00Z",
                    "teams": [
                        {"id": 601, "name": "Cleveland Cavaliers", "side": "AWAY"},
                        {"id": 602, "name": "New York Knicks", "side": "HOME"},
                    ],
                    "players": [],
                    "eventStatus": {"name": "Scheduled"},
                    "season": {"name": "2026 NBA Playoffs"},
                    "seasonSchedule": {"scheduleType": "Postseason"},
                    "broadcast": {"network": "ESPN"},
                },
            ]
        }

    monkeypatch.setattr(bc_core, "_get_json", fake_get_json)
    monkeypatch.setattr(bc_core, "bc_core_configured", lambda: True)

    context, reason = await bc_core.build_event_context(
        {
            "title": "bet365 bonus code: Cavaliers vs. Knicks",
            "event": {
                "sport": "nba",
                "headline": "Cleveland Cavaliers vs. New York Knicks",
                "away_team": "Cleveland Cavaliers",
                "home_team": "New York Knicks",
            },
        }
    )

    assert reason == ""
    assert context["matched"] is True
    assert context["event_id"] == 2
    assert context["away_team"] == "Cleveland Cavaliers"


@pytest.mark.asyncio
async def test_build_event_context_tolerates_null_names_in_bc_payload(monkeypatch):
    async def fake_get_json(path: str, *, params=None):
        assert path == "/mlb/events"
        return {
            "results": [
                {
                    "id": 9,
                    "leagueId": 3,
                    "name": None,
                    "scheduledDate": "2026-05-20T00:00:00Z",
                    "teams": [
                        {"id": 700, "name": "Atlanta Braves", "side": "AWAY"},
                        {"id": 701, "name": None, "side": "HOME"},
                    ],
                    "players": [
                        {"id": 1, "preferredName": None, "lastName": "Acuna", "side": "AWAY"},
                    ],
                    "eventStatus": {"name": "Scheduled"},
                    "season": {"name": "2026 MLB"},
                    "seasonSchedule": {"scheduleType": "Regular Season"},
                    "broadcast": {"network": "MLB.TV"},
                }
            ]
        }

    monkeypatch.setattr(bc_core, "_get_json", fake_get_json)
    monkeypatch.setattr(bc_core, "bc_core_configured", lambda: True)

    context, reason = await bc_core.build_event_context(
        {
            "title": "bet365 bonus code: Braves vs. Marlins",
            "event": {
                "sport": "mlb",
                "headline": "Atlanta Braves vs. Miami Marlins",
                "away_team": "Atlanta Braves",
                "home_team": "Miami Marlins",
            },
        }
    )

    assert context["matched"] is False
    assert "did not match both requested teams" in reason
