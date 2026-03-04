"""Phase 7 tests for minimum viable non-team event support."""

from app.services.outline import _contextual_section_titles, _extract_matchup_from_event_context


def test_extract_matchup_from_event_context_supports_featured_event():
    event = _extract_matchup_from_event_context(
        "Featured event: UFC 325 Main Card. Game time: Saturday, February 14 at 10:00 PM ET."
    )
    assert event == "UFC 325 Main Card"


def test_contextual_section_titles_can_use_non_team_event_label():
    titles = _contextual_section_titles(
        keyword="bet365 promo code",
        brand="bet365",
        event_context="Featured event: UFC 325 Main Card.",
    )
    assert "UFC 325 Main Card" in titles["overview"]
    assert "UFC 325 Main Card" in titles["claim"]

