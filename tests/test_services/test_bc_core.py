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
