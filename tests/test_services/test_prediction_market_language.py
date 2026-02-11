"""Prediction-market language guardrail tests."""

from app.services.draft import _build_signup_list, _render_terms_section_html
from app.services.internal_links import InternalLinkSpec, format_links_markdown
from app.services.operator_profile import is_prediction_market_context, is_prediction_market_offer
from app.services.outline import _contextual_section_titles


def test_prediction_market_detection_helpers():
    assert is_prediction_market_context("Kalshi promo code")
    assert is_prediction_market_offer({"brand": "Polymarket", "offer_text": "Get bonus"})
    assert not is_prediction_market_context("BetMGM promo code")


def test_prediction_market_signup_fallback_avoids_bet_terms():
    html = _build_signup_list("Kalshi", has_code=True, code_strong="<strong>KALSHI</strong>", prediction_market=True)
    assert "qualifying market position" in html
    assert "qualifying bet" not in html


def test_prediction_market_terms_fallback_avoids_odds_and_wagering():
    html = _render_terms_section_html(
        terms="",
        expiration_days=5,
        min_odds="+100",
        wagering="1x",
        prediction_market=True,
    )
    assert "Promotional credits expire in 5 days." in html
    assert "Minimum odds" not in html
    assert "Wagering requirement" not in html


def test_prediction_market_internal_link_hints_use_market_wording():
    links = [
        InternalLinkSpec(
            title="Best Betting Sites",
            url="https://example.com/reviews",
            recommended_anchors=["best betting sites"],
        )
    ]
    md = format_links_markdown(links, brand="Kalshi", prediction_market=True)
    assert "how market contracts settle" in md
    assert "how bonus bets work" not in md


def test_prediction_market_outline_titles_use_how_to_use():
    titles = _contextual_section_titles(
        keyword="kalshi promo code",
        brand="Kalshi",
        event_context="",
        is_prediction_market=True,
    )
    assert titles["claim"].startswith("How to Use")

