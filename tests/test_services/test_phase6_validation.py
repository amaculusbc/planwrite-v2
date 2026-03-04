"""Phase 6 tests for editorial/compliance regression validators."""

from app.services.compliance import check_editorial_regressions, check_offer_facts, validate_content


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

