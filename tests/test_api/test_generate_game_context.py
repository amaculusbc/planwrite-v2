"""Tests for game context formatting in generation endpoints."""

from app.api.generate import _build_game_context
from app.schemas.outline import GameContext


def test_build_game_context_formats_iso_time_to_et():
    """ISO game times should be normalized to readable ET text."""
    context = GameContext(
        away_team="Atlanta Hawks",
        home_team="Charlotte Hornets",
        start_time="2026-02-14T01:00Z",
        network="ESPN",
    )

    game_context, _, bet_example_data = _build_game_context(context)

    assert "Featured game: Atlanta Hawks vs Charlotte Hornets" in game_context
    assert "Game time: Friday, February 13 at 8:00 PM ET" in game_context
    assert "Network: ESPN" in game_context
    assert "2026-02-14T01:00Z" not in game_context
    assert bet_example_data == {}


def test_build_game_context_keeps_preformatted_time():
    """Already-formatted game times should pass through unchanged."""
    context = GameContext(
        away_team="Arizona",
        home_team="Kansas",
        start_time="Mon, Feb 9, 9:00 PM ET",
    )

    game_context, _, _ = _build_game_context(context)

    assert "Game time: Mon, Feb 9, 9:00 PM ET" in game_context


def test_build_game_context_preserves_structured_bet_example_data():
    context = GameContext(
        away_team="Atlanta Hawks",
        home_team="Charlotte Hornets",
        bet_example="Suppose I place a $100 first bet...",
        bet_example_data={
            "bet_amount": 100,
            "selection": "Atlanta Hawks ML",
            "odds": 170,
            "sportsbook_used": "betmgm",
        },
    )

    _, bet_example, bet_example_data = _build_game_context(context)

    assert "Suppose I place a $100 first bet" in bet_example
    assert bet_example_data["bet_amount"] == 100
    assert bet_example_data["selection"] == "Atlanta Hawks ML"
    assert bet_example_data["sportsbook_used"] == "betmgm"


def test_build_game_context_supports_custom_non_team_event():
    context = GameContext(
        event_type="fight",
        custom_event="UFC 325 Main Card",
        start_time="2026-02-14T03:00Z",
        network="ESPN+",
    )

    game_context, _, _ = _build_game_context(context)

    assert "Featured event: UFC 325 Main Card" in game_context
    assert "Game time:" in game_context
    assert "Network: ESPN+" in game_context
