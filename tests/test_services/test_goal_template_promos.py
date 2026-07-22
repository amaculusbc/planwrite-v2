"""GOAL promos block, sticky footer and outline structure (July 8 feedback from Tom Fuller)."""

from app.services.bc_core_boosts import summarize_boosts
from app.services.goal_template import (
    GOAL_PAGE_BLOCKER,
    build_goal_outline,
    render_goal_article_footer,
    render_operator_promos_section,
)
from app.services.operator_facts import get_standing_promos

BET365 = {"brand": "bet365", "affiliate_type": "sportsbook", "internal_id": "evergreen"}


def test_promos_section_leads_with_boosts_and_standing_promos():
    html = render_operator_promos_section(
        "bet365",
        [{"id": "2", "offer_text": "Get a First Bet Safety Net up to $1,000!", "bonus_code": "GOALBET"}],
        primary_offer_id="1",
        keyword="bet365 bonus code",
        bonus_code="GOALBET",
        sport="soccer",
        boosts=[{
            "legs": ["Juan Soto: 1+ Home Runs", "Kyle Schwarber: 1+ Home Runs"],
            "original_odds": "+725",
            "boosted_odds": "+850",
        }],
        standing_promos=get_standing_promos("bet365", "soccer"),
    )
    # Tom's requested framing: for all players, not just new sign-ups, and named sport.
    assert "Aside from the bet365 bonus code GOALBET offer for new players" in html
    assert "many other soccer promos and bonuses available to all players" in html
    assert "Odds boost: Juan Soto: 1+ Home Runs + Kyle Schwarber: 1+ Home Runs - boosted from +725 to +850" in html
    assert "Early Payout" in html
    assert "First Bet Safety Net" in html


def test_standing_promos_are_sport_scoped():
    # bet365's Early Payout is a two-goals-ahead soccer rule; it must never
    # appear on a baseball article.
    soccer = get_standing_promos("bet365", "soccer")
    mlb = get_standing_promos("bet365", "mlb")

    assert any("2 Goals Ahead Early Payout" in p for p in soccer)
    assert not any("Goals Ahead" in p for p in mlb)
    assert not any("soccer" in p.lower() for p in mlb)
    # The cross-sport claim still shows for both.
    assert any("Bet Boost" in p for p in soccer)
    assert any("Bet Boost" in p for p in mlb)


def test_standing_promos_without_a_sport_return_cross_sport_claims_only():
    promos = get_standing_promos("bet365")
    assert promos == ["Bet Boost tokens on selected pre-match and in-play parlays"]


def test_promos_section_names_the_sport_per_article():
    for sport, word in (("mlb", "MLB"), ("nfl", "NFL"), ("soccer", "soccer")):
        html = render_operator_promos_section(
            "bet365", [], keyword="bet365 bonus code", bonus_code="GOALBET",
            sport=sport, standing_promos=["Early Payout on pre-match singles"],
        )
        assert f"many other {word} promos" in html


def test_promos_section_drops_foreign_currency_offers():
    html = render_operator_promos_section(
        "bet365",
        [
            {"id": "2", "offer_text": "UK: Bet £10 & Get £30 in Free Bets!"},
            {"id": "3", "offer_text": "Mexico: Consiga hasta $3,000 en apuestas gratis"},
            {"id": "4", "offer_text": "Bet $10, Get $150 in Bonus Bets!"},
        ],
        keyword="bet365 bonus code", sport="mlb", standing_promos=[],
    )
    assert "£30" not in html
    assert "apuestas" not in html
    assert "Bet $10, Get $150 in Bonus Bets!" in html


def test_summarize_boosts_dedupes_the_same_promo_across_state_books():
    # One national promo is published under several bet365 state books.
    raw = [
        {"sportsbookId": 3915, "originalOdds": 550, "boostedOdds": 600,
         "oddsBoostPicks": '[{"criteria_description": "Match Result will be NY Mets"}]'},
        {"sportsbookId": 4422, "originalOdds": 550, "boostedOdds": 600,
         "oddsBoostPicks": '[{"criteria_description": "Match Result will be NY Mets"}]'},
        {"sportsbookId": 3915, "originalOdds": 725, "boostedOdds": 850,
         "oddsBoostPicks": '[{"criteria_description": "Juan Soto: 1+ Home Runs"}]'},
    ]
    out = summarize_boosts(raw)
    assert len(out) == 2
    # Biggest boost first.
    assert out[0]["legs"] == ["Juan Soto: 1+ Home Runs"]
    assert out[0]["boosted_odds"] == "+850"


def test_summarize_boosts_drops_expired_boosts():
    from datetime import UTC, datetime

    now = datetime(2026, 7, 16, tzinfo=UTC)
    raw = [
        {"originalOdds": 100, "boostedOdds": 150, "cutOffDate": "2026-07-15T00:00:00Z",
         "oddsBoostPicks": '[{"criteria_description": "Expired leg"}]'},
        {"originalOdds": 100, "boostedOdds": 150, "cutOffDate": "2026-07-17T00:00:00Z",
         "oddsBoostPicks": '[{"criteria_description": "Live leg"}]'},
    ]
    out = summarize_boosts(raw, now=now)
    assert [b["legs"][0] for b in out] == ["Live leg"]


def test_goal_footer_adds_sticky_cta_everywhere_and_blocker_only_in_us():
    us = render_goal_article_footer(BET365, market="US")
    assert '<bam-sticky-cta placement-id="2066" property-id="326"' in us
    assert 'affiliate="bet365"' in us
    assert GOAL_PAGE_BLOCKER in us

    ca = render_goal_article_footer(BET365, market="CA")
    assert "<bam-sticky-cta" in ca
    assert "bam-page-blocker" not in ca


def test_outline_puts_analysis_under_the_match_title():
    outline = build_goal_outline(keyword="bet365 bonus code", brand="bet365", bonus_code="GOALBET",
                                 away_team="Egypt", home_team="Australia")
    h2 = next(s for s in outline if s["title"].startswith("Today's Sports Betting"))
    h3 = next(s for s in outline if s["level"] == "h3")

    assert "Do NOT analyse the match here" in " ".join(h2["talking_points"])
    assert "analysis section" in " ".join(h3["talking_points"])
    assert "never guess who comes in" in " ".join(h3["talking_points"])


def test_boost_paths_resolve_app_sport_codes():
    """The app says ncaaf/ncaab; BC Core paths say ncaafb/ncaamb. The alias bridges them."""
    from app.services.bc_core_boosts import BOOST_PATHS, _SPORT_ALIASES

    for app_code, expected_path in (
        ("ncaaf", "/ncaafb/oddsboost"),
        ("ncaab", "/ncaamb/oddsboost"),
        ("mlb", "/mlb/oddsboost"),
    ):
        resolved = BOOST_PATHS.get(_SPORT_ALIASES.get(app_code, app_code))
        assert resolved == expected_path, f"{app_code} resolved to {resolved}"
