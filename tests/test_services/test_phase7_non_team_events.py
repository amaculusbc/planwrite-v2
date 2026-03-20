"""Phase 7 tests for minimum viable non-team event support."""

from app.services.outline import (
    OUTLINE_SCHEMA,
    _apply_editorial_section_rules,
    _compact_matchup_label,
    _contextual_section_titles,
    _extract_matchup_from_event_context,
)


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
    assert "bonus code" not in titles["claim"].lower()


def test_compact_matchup_label_shortens_full_team_names():
    assert _compact_matchup_label("Boston Celtics vs. San Antonio Spurs") == "Celtics vs. Spurs"
    assert _compact_matchup_label("Boston Celtics vs San Antonio Spurs") == "Celtics vs. Spurs"


def test_claim_title_avoids_keyword_plus_matchup_pattern():
    titles = _contextual_section_titles(
        keyword="bet365 bonus code",
        brand="bet365",
        event_context="Featured game: Boston Celtics vs San Antonio Spurs.",
    )
    assert "bonus code" not in titles["claim"].lower()
    assert "celtics vs. spurs" in titles["claim"].lower()


def test_outline_schema_uses_object_root_for_structured_output():
    assert OUTLINE_SCHEMA["type"] == "object"
    assert "outline" in OUTLINE_SCHEMA["properties"]


def test_editorial_rules_do_not_append_duplicate_claim_or_signup_sections():
    outline = [
        {"level": "intro", "title": "", "talking_points": [], "avoid": []},
        {"level": "shortcode", "title": "", "talking_points": [], "avoid": []},
        {"level": "h2", "title": "What Stands Out for Boston Celtics vs San Antonio Spurs", "talking_points": [], "avoid": []},
        {"level": "h2", "title": "Using the bet365 bonus code on Celtics vs. Spurs: a quick example with real numbers", "talking_points": [], "avoid": []},
        {"level": "h2", "title": "Using bet365 bonus code on Boston Celtics vs San Antonio Spurs", "talking_points": [], "avoid": []},
        {"level": "h2", "title": "Promo Update Placeholder", "talking_points": [], "avoid": []},
        {"level": "h2", "title": "Sign-up steps to use ACTION365 before tipoff", "talking_points": [], "avoid": []},
        {"level": "h2", "title": "Sign-Up Steps Before Boston Celtics vs San Antonio Spurs", "talking_points": [], "avoid": []},
        {"level": "h2", "title": "Fine Print & Offer Terms", "talking_points": [], "avoid": []},
    ]

    cleaned = _apply_editorial_section_rules(
        outline,
        keyword="bet365 bonus code",
        brand="bet365",
        event_context="Featured game: Boston Celtics vs San Antonio Spurs.",
    )

    h2_titles = [section["title"] for section in cleaned if section.get("level") == "h2"]
    assert len(h2_titles) == 5
    assert len(h2_titles) == len(set(h2_titles))
