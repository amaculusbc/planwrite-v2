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


def test_extract_matchup_from_event_context_keeps_vs_period():
    matchup = _extract_matchup_from_event_context(
        "Featured game: Boston Celtics vs. San Antonio Spurs. Game time: Friday, May 8 at 8:00 PM ET. Network: ESPN"
    )
    assert matchup == "Boston Celtics vs. San Antonio Spurs"


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
    assert len(h2_titles) == 4
    assert len(h2_titles) == len(set(h2_titles))


def test_editorial_rules_collapse_duplicate_returning_player_sections():
    outline = [
        {"level": "intro", "title": "", "talking_points": [], "avoid": []},
        {"level": "shortcode", "title": "", "talking_points": [], "avoid": []},
        {"level": "h2", "title": "Why bet365 bonus code works for Celtics vs. Spurs", "talking_points": [], "avoid": []},
        {"level": "h2", "title": "Promos for Returning Players", "talking_points": [], "avoid": []},
        {"level": "h2", "title": "Other Promos for Existing Customers", "talking_points": [], "avoid": []},
        {"level": "h2", "title": "Terms & Conditions", "talking_points": [], "avoid": []},
    ]

    cleaned = _apply_editorial_section_rules(
        outline,
        keyword="bet365 bonus code",
        brand="bet365",
        event_context="Featured game: Boston Celtics vs San Antonio Spurs.",
    )

    h2_titles = [section["title"] for section in cleaned if section.get("level") == "h2"]
    returning_titles = [title for title in h2_titles if "returning" in title.lower() or "other sportsbook promos" in title.lower()]
    assert len(returning_titles) == 1


def test_editorial_rules_drop_signup_h3_when_signup_h2_exists():
    outline = [
        {"level": "intro", "title": "", "talking_points": [], "avoid": []},
        {"level": "shortcode", "title": "", "talking_points": [], "avoid": []},
        {"level": "h2", "title": "Best Angle for UFC 325 Main Card", "talking_points": [], "avoid": []},
        {"level": "h2", "title": "Sign-Up Steps Before UFC 325 Main Card", "talking_points": [], "avoid": []},
        {"level": "h3", "title": "Step-by-step (numbered list to use in the article)", "talking_points": [], "avoid": []},
        {"level": "h2", "title": "Offer Terms & Conditions", "talking_points": [], "avoid": []},
    ]

    cleaned = _apply_editorial_section_rules(
        outline,
        keyword="bet365 bonus code",
        brand="bet365",
        event_context="Featured event: UFC 325 Main Card.",
    )

    h3_titles = [section["title"] for section in cleaned if section.get("level") == "h3"]
    assert h3_titles == []


def test_editorial_rules_skip_daily_promos_for_dfs_articles():
    outline = [
        {"level": "intro", "title": "", "talking_points": [], "avoid": []},
        {"level": "shortcode", "title": "", "talking_points": [], "avoid": []},
        {"level": "h2", "title": "What to Know About Lakers vs. Thunder", "talking_points": [], "avoid": []},
        {"level": "h2", "title": "Daily Promos Today", "talking_points": [], "avoid": []},
        {"level": "h2", "title": "Sign-Up Steps Before Lakers vs. Thunder", "talking_points": [], "avoid": []},
    ]

    cleaned = _apply_editorial_section_rules(
        outline,
        keyword="underdog promo code",
        brand="Underdog",
        event_context="Featured game: Los Angeles Lakers vs Oklahoma City Thunder.",
        is_dfs=True,
    )

    h2_titles = [section["title"] for section in cleaned if section.get("level") == "h2"]
    assert all("daily promo" not in title.lower() for title in h2_titles)
    assert all("promo update placeholder" not in title.lower() for title in h2_titles)


def test_editorial_rules_skip_daily_promos_for_prediction_market_articles():
    outline = [
        {"level": "intro", "title": "", "talking_points": [], "avoid": []},
        {"level": "shortcode", "title": "", "talking_points": [], "avoid": []},
        {"level": "h2", "title": "Best Market Angle for NBA Finals MVP Market", "talking_points": [], "avoid": []},
        {"level": "h2", "title": "Daily Promos Today", "talking_points": [], "avoid": []},
        {"level": "h2", "title": "Sign-Up Steps Before NBA Finals MVP Market", "talking_points": [], "avoid": []},
    ]

    cleaned = _apply_editorial_section_rules(
        outline,
        keyword="Novig promo code",
        brand="Novig",
        event_context="Featured event: NBA Finals MVP Market.",
        is_prediction_market=True,
    )

    h2_titles = [section["title"] for section in cleaned if section.get("level") == "h2"]
    assert all("daily promo" not in title.lower() for title in h2_titles)
    assert all("promo update placeholder" not in title.lower() for title in h2_titles)


def test_contextual_section_titles_use_editorial_dfs_heading_patterns():
    titles = _contextual_section_titles(
        keyword="underdog promo code",
        brand="Underdog",
        event_context="Featured game: Los Angeles Lakers vs Oklahoma City Thunder.",
        is_dfs=True,
    )

    assert "what to know about" not in titles["overview"].lower()
    assert titles["signup"] == "How to Claim Underdog promo code"
    assert "lakers vs. thunder" in titles["overview"].lower()
    assert "underdog promo code" in titles["claim"].lower()


def test_editorial_rules_treat_get_set_up_heading_as_signup():
    outline = [
        {"level": "intro", "title": "", "talking_points": [], "avoid": []},
        {"level": "shortcode", "title": "", "talking_points": [], "avoid": []},
        {"level": "h2", "title": "Best Market Angle for NBA Finals MVP Market", "talking_points": [], "avoid": []},
        {"level": "h2", "title": "How to get set up on Novig before the NBA Finals MVP Market locks in", "talking_points": [], "avoid": []},
        {"level": "h2", "title": "How to Get Started Before NBA Finals MVP Market", "talking_points": [], "avoid": []},
    ]

    cleaned = _apply_editorial_section_rules(
        outline,
        keyword="Novig promo code",
        brand="Novig",
        event_context="Featured event: NBA Finals MVP Market.",
        is_prediction_market=True,
    )

    signup_titles = [
        section["title"]
        for section in cleaned
        if section.get("level") == "h2" and "sign" in section.get("title", "").lower()
    ]
    assert len(signup_titles) == 1


def test_editorial_rules_treat_market_rules_heading_as_terms():
    outline = [
        {"level": "intro", "title": "", "talking_points": [], "avoid": []},
        {"level": "shortcode", "title": "", "talking_points": [], "avoid": []},
        {"level": "h2", "title": "Best Market Angle for NBA Finals MVP Market", "talking_points": [], "avoid": []},
        {"level": "h2", "title": "Market rules & settlement: what 'Finals MVP' means on Novig", "talking_points": [], "avoid": []},
        {"level": "h2", "title": "Market Rules & Settlement", "talking_points": [], "avoid": []},
    ]

    cleaned = _apply_editorial_section_rules(
        outline,
        keyword="Novig promo code",
        brand="Novig",
        event_context="Featured event: NBA Finals MVP Market.",
        is_prediction_market=True,
    )

    terms_like_titles = [
        section["title"]
        for section in cleaned
        if section.get("level") == "h2" and "settlement" in section.get("title", "").lower()
    ]
    assert len(terms_like_titles) == 1


def test_editorial_rules_treat_bonus_bets_play_out_heading_as_claim():
    outline = [
        {"level": "intro", "title": "", "talking_points": [], "avoid": []},
        {"level": "shortcode", "title": "", "talking_points": [], "avoid": []},
        {"level": "h2", "title": "Best Angle for Celtics vs. Spurs", "talking_points": [], "avoid": []},
        {"level": "h2", "title": "How the Bonus Bets Play Out for Celtics vs. Spurs", "talking_points": [], "avoid": []},
        {"level": "h2", "title": "Worked Example for Celtics vs. Spurs", "talking_points": [], "avoid": []},
    ]

    cleaned = _apply_editorial_section_rules(
        outline,
        keyword="bet365 bonus code",
        brand="bet365",
        event_context="Featured game: Boston Celtics vs San Antonio Spurs.",
    )

    claim_titles = [
        section["title"]
        for section in cleaned
        if section.get("level") == "h2" and ("worked example" in section.get("title", "").lower() or "bonus bets play out" in section.get("title", "").lower() or "welcome offer looks like" in section.get("title", "").lower())
    ]
    assert len(claim_titles) == 1


def test_contextual_section_titles_vary_with_variation_key():
    seen = {
        _contextual_section_titles(
            keyword="underdog promo code",
            brand="Underdog",
            event_context="Featured game: Los Angeles Lakers vs Oklahoma City Thunder.",
            is_dfs=True,
            variation_key=f"run-{idx}",
        )["overview"]
        for idx in range(8)
    }
    assert len(seen) > 1


def test_editorial_rules_insert_missing_signup_before_terms():
    outline = [
        {"level": "intro", "title": "", "talking_points": [], "avoid": []},
        {"level": "shortcode", "title": "", "talking_points": [], "avoid": []},
        {"level": "h2", "title": "How underdog promo code fits Lakers vs. Thunder", "talking_points": [], "avoid": []},
        {"level": "h2", "title": "DFS Terms & Rules", "talking_points": [], "avoid": []},
    ]

    cleaned = _apply_editorial_section_rules(
        outline,
        keyword="underdog promo code",
        brand="Underdog",
        event_context="Featured game: Los Angeles Lakers vs Oklahoma City Thunder.",
        is_dfs=True,
    )

    h2_titles = [section["title"] for section in cleaned if section.get("level") == "h2"]
    assert "How to Claim Underdog promo code" in h2_titles
    terms_title = next(title for title in h2_titles if "terms" in title.lower() or "rules" in title.lower())
    assert h2_titles.index("How to Claim Underdog promo code") < h2_titles.index(terms_title)


def test_editorial_rules_honor_article_preferences_for_section_count_and_h3():
    outline = [
        {"level": "intro", "title": "", "talking_points": [], "avoid": []},
        {"level": "shortcode", "title": "", "talking_points": [], "avoid": []},
        {"level": "h2", "title": "How underdog promo code fits Lakers vs. Thunder", "talking_points": [], "avoid": []},
        {"level": "h3", "title": "Extra angle", "talking_points": [], "avoid": []},
        {"level": "h2", "title": "How to Claim Underdog promo code", "talking_points": [], "avoid": []},
        {"level": "h2", "title": "DFS Terms & Rules", "talking_points": [], "avoid": []},
        {"level": "h2", "title": "One more section", "talking_points": [], "avoid": []},
    ]

    cleaned = _apply_editorial_section_rules(
        outline,
        keyword="underdog promo code",
        brand="Underdog",
        event_context="Featured game: Los Angeles Lakers vs Oklahoma City Thunder.",
        is_dfs=True,
        article_preferences={"section_count": 3, "allow_h3": False},
    )

    assert sum(1 for section in cleaned if section.get("level") == "h2") == 3
    assert all(section.get("level") != "h3" for section in cleaned)


def test_editorial_rules_can_keep_daily_promos_when_requested_for_sportsbook():
    outline = [
        {"level": "intro", "title": "", "talking_points": [], "avoid": []},
        {"level": "shortcode", "title": "", "talking_points": [], "avoid": []},
        {"level": "h2", "title": "How bet365 bonus code fits Celtics vs. Spurs", "talking_points": [], "avoid": []},
        {"level": "h2", "title": "Sign-Up Steps Before Celtics vs. Spurs", "talking_points": [], "avoid": []},
        {"level": "h2", "title": "Terms & Conditions", "talking_points": [], "avoid": []},
    ]

    cleaned = _apply_editorial_section_rules(
        outline,
        keyword="bet365 bonus code",
        brand="bet365",
        event_context="Featured game: Boston Celtics vs San Antonio Spurs.",
        article_preferences={"include_daily_promos": True},
    )

    h2_titles = [section["title"] for section in cleaned if section.get("level") == "h2"]
    assert any("promo" in title.lower() for title in h2_titles)
