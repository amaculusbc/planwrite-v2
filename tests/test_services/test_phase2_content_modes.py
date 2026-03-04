"""Phase 2 regression tests for content-mode routing (sportsbook vs PM vs DFS)."""

from app.services.draft import (
    _apply_content_mode_language_guardrails,
    _build_signup_list,
    _render_terms_section_html,
)
from app.services.internal_links import InternalLinkSpec, format_links_markdown
from app.services.operator_profile import (
    CONTENT_MODE_DFS,
    CONTENT_MODE_PREDICTION_MARKET,
    get_content_mode_context,
    is_dfs_context,
    is_dfs_offer,
)
from app.services.outline import _contextual_section_titles


def test_content_mode_detection_routes_novig_and_dfs_brands():
    assert get_content_mode_context("Novig promo code") == CONTENT_MODE_PREDICTION_MARKET
    assert get_content_mode_context("Sleeper promo code") == CONTENT_MODE_DFS
    assert get_content_mode_context("Underdog Fantasy", "promo code") == CONTENT_MODE_DFS
    assert is_dfs_context("sleeper sign up bonus")
    assert is_dfs_offer({"brand": "Underdog", "offer_text": "Get bonus entries"})


def test_dfs_signup_fallback_avoids_bet_terms():
    html = _build_signup_list(
        "Sleeper",
        has_code=True,
        code_strong="<strong>ACTION</strong>",
        dfs_mode=True,
    )
    assert "qualifying fantasy entry" in html
    assert "qualifying bet" not in html
    assert "how pick'em entries work" in html


def test_dfs_terms_fallback_avoids_sportsbook_fields():
    html = _render_terms_section_html(
        terms="",
        expiration_days=7,
        min_odds="+100",
        wagering="1x",
        dfs_mode=True,
    )
    assert "Bonus entries expire in 7 days." in html
    assert "Minimum odds" not in html
    assert "Wagering requirement" not in html
    assert "contest rules" in html


def test_dfs_internal_link_hints_use_dfs_wording():
    links = [
        InternalLinkSpec(
            title="Best Betting Sites",
            url="https://example.com/best-betting-sites",
            recommended_anchors=["best betting sites"],
        )
    ]
    md = format_links_markdown(links, brand="Sleeper", dfs_mode=True)
    assert "how pick'em entries work" in md
    assert "how bonus bets work" not in md
    assert "Best DFS Sites" in md or "Best DFS sites" in md


def test_dfs_guardrail_rewrites_visible_text_but_preserves_urls():
    html = (
        '<p>Use betting tools before you bet.</p>'
        '<p><a href="https://example.com/online-sports-betting/reviews">best betting sites</a></p>'
    )
    cleaned = _apply_content_mode_language_guardrails(html, CONTENT_MODE_DFS)
    assert "daily fantasy tools before you entry." in cleaned or "daily fantasy tools before you pick." in cleaned
    assert "https://example.com/online-sports-betting/reviews" in cleaned
    assert "href" in cleaned


def test_dfs_outline_titles_use_how_to_use_not_claim():
    titles = _contextual_section_titles(
        keyword="sleeper promo code",
        brand="Sleeper",
        event_context="Featured game: Hawks vs. Hornets.",
        is_dfs=True,
    )
    assert titles["claim"].startswith("How to Use")
    assert "How to Claim" not in titles["claim"]

