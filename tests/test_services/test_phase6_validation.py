"""Phase 6 tests for editorial/compliance regression validators."""

from app.services.compliance import (
    check_cta_links,
    check_editorial_regressions,
    check_link_quality,
    check_offer_facts,
    validate_content,
)


def _issue_types(issues):
    return {issue.type for issue in issues}


def test_check_offer_facts_uses_5_to_9_keyword_target():
    low = check_offer_facts("<p>draftkings promo code once</p>", keyword="draftkings promo code")
    assert "keyword_density_low" in _issue_types(low)

    ok_html = "<p>" + " ".join(["draftkings promo code"] * 5) + "</p>"
    ok = check_offer_facts(ok_html, keyword="draftkings promo code")
    assert "keyword_density_low" not in _issue_types(ok)


def test_editorial_regressions_detects_keyword_first_paragraph_and_link_overuse_and_duplicates():
    content = """
    <p>Tonight's matchup is live. No main keyword here.</p>
    <p><a data-id="switchboard_tracking" href="https://switchboard.actionnetwork.com/offers?x=1">bet365 promo code</a></p>
    <p><a data-id="switchboard_tracking" href="https://switchboard.actionnetwork.com/offers?x=2">bet365 promo code</a></p>
    <p><a data-id="switchboard_tracking" href="https://switchboard.actionnetwork.com/offers?x=3">bet365 promo code</a></p>
    <p><a href="https://www.actionnetwork.com/online-sports-betting/reviews/bet365">bet365 sign-up guide</a></p>
    <p><a href="https://www.actionnetwork.com/online-sports-betting/reviews/bet365">bet365 guide again</a></p>
    """
    issues = check_editorial_regressions(
        content,
        keyword="bet365 promo code",
        offer={"brand": "bet365"},
    )
    types = _issue_types(issues)
    assert "main_keyword_missing_early" in types
    assert "switchboard_link_overuse" in types
    assert "duplicate_internal_link" in types


def test_editorial_regressions_detects_cta_brand_mismatch():
    content = """
    <p><a data-id="switchboard_tracking" href="https://switchboard.actionnetwork.com/offers?x=1">
      <strong>bet365 promo code</strong>
    </a></p>
    """
    issues = check_editorial_regressions(content, keyword="BetMGM bonus code", offer={"brand": "BetMGM"})
    assert "cta_brand_mismatch" in _issue_types(issues)


def test_editorial_regressions_detects_prediction_market_language_mismatch_for_novig():
    content = "<p>Use the Novig promo code before betting at the sportsbook to get bonus bets.</p>"
    issues = check_editorial_regressions(content, keyword="Novig promo code", offer={"brand": "Novig"})
    assert "mode_language_mismatch" in _issue_types(issues)


def test_validate_content_includes_editorial_regression_checks():
    result = validate_content(
        content="<p>No keyword here.</p>",
        state="ALL",
        check_links=False,
        keyword="draftkings promo code",
        offer={"brand": "DraftKings"},
    )
    assert any(issue.type == "main_keyword_missing_early" for issue in result.issues)


def test_check_cta_links_accepts_switchboard_and_bam_shortcodes():
    switchboard_content = '<p><a data-id="switchboard_tracking" href="https://switchboard.actionnetwork.com/offers?x=1">Claim</a></p>'
    bam_content = '[bam-inline-promotion placement-id="2037" property-id="1" affiliate="bet365"]'

    assert check_cta_links(switchboard_content) == []
    assert check_cta_links(bam_content) == []


def test_check_link_quality_flags_html_heading_links():
    content = '<h2><a href="https://www.actionnetwork.com/online-sports-betting/reviews/bet365">bet365 guide</a></h2>'
    issues = check_link_quality(content, allowed_domains=["actionnetwork.com"])
    assert "heading_link" in _issue_types(issues)


def test_editorial_regressions_warn_on_bet365_promo_code_wording():
    content = "<p>The bet365 promo code is live tonight.</p>"
    issues = check_editorial_regressions(content, keyword="bet365 bonus code", offer={"brand": "bet365"})
    assert "bet365_keyword_mismatch" in _issue_types(issues)
