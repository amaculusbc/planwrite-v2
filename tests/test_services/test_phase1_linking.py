"""Phase 1 linking and CTA behavior tests."""

from app.services.draft import (
    _dedupe_non_switchboard_links_by_url,
    _inject_switchboard_links_for_offers,
    _link_first_keyword_internal,
    _offer_switchboard_url,
)
from app.services.switchboard_links import inject_switchboard_links


def test_switchboard_injection_skips_generic_promo_code_anchor_for_wrong_offer():
    html = "<p>Use <strong>promo code</strong> tonight for a deal.</p>"
    out = inject_switchboard_links(
        html,
        brand="BetMGM",
        bonus_code="ACTION1550",
        switchboard_url="https://switchboard.example.com/offers?affiliateId=1",
        max_links=3,
    )
    assert 'switchboard_tracking' not in out
    assert "<strong>promo code</strong>" in out


def test_switchboard_injection_wraps_brand_or_code_specific_anchor():
    html = "<p>Use <strong>bet365 promo code TOPACTION</strong> before you place your first bet.</p>"
    out = inject_switchboard_links(
        html,
        brand="bet365",
        bonus_code="TOPACTION",
        switchboard_url="https://switchboard.example.com/offers?affiliateId=1",
        max_links=3,
    )
    assert 'switchboard_tracking' in out
    assert "TOPACTION" in out


def test_global_switchboard_cap_is_enforced_across_multiple_offers():
    html = (
        "<p>"
        "<strong>bet365 promo code TOPACTION</strong> "
        "<strong>bet365 bonus code TOPACTION</strong> "
        "<strong>BetMGM promo code ACTION1550</strong> "
        "<strong>BetMGM bonus code ACTION1550</strong>"
        "</p>"
    )
    offers = [
        {
            "brand": "bet365",
            "bonus_code": "TOPACTION",
            "switchboard_link": "https://switchboard.example.com/offers?affiliateId=1",
        },
        {
            "brand": "BetMGM",
            "bonus_code": "ACTION1550",
            "switchboard_link": "https://switchboard.example.com/offers?affiliateId=2",
        },
    ]

    out = _inject_switchboard_links_for_offers(html, offers, state="ALL", max_links=2)
    assert out.count('data-id="switchboard_tracking"') <= 2


def test_first_keyword_linking_skips_headings_and_links_first_body_mention():
    html = (
        "<h1>bet365 promo code: Get Bonus</h1>"
        "<p>The bet365 promo code is available today for eligible users.</p>"
        "<p>Another bet365 promo code mention appears later.</p>"
    )
    out = _link_first_keyword_internal(
        html,
        keyword="bet365 promo code",
        url="https://www.actionnetwork.com/online-sports-betting/reviews/bet365",
    )

    assert "<h1>bet365 promo code: Get Bonus</h1>" in out
    assert out.count('href="https://www.actionnetwork.com/online-sports-betting/reviews/bet365"') == 1
    assert '<p>The <a href="https://www.actionnetwork.com/online-sports-betting/reviews/bet365">bet365 promo code</a>' in out


def test_dedupes_internal_links_but_keeps_switchboard_links():
    html = (
        '<p><a href="https://example.com/bet365">bet365 guide</a> before sign-up.</p>'
        '<p><a href="https://example.com/bet365">bet365 review page</a> later in article.</p>'
        '<p><a data-id="switchboard_tracking" href="https://switchboard.example.com/offers?x=1" rel="nofollow">bet365</a></p>'
        '<p><a data-id="switchboard_tracking" href="https://switchboard.example.com/offers?x=1" rel="nofollow">bet365 code</a></p>'
    )
    out = _dedupe_non_switchboard_links_by_url(html)

    assert out.count('href="https://example.com/bet365"') == 1
    assert "bet365 review page" in out  # unwrapped text remains
    assert out.count('data-id="switchboard_tracking"') == 2


def test_offer_switchboard_url_uses_property_domain_and_state_code():
    url = _offer_switchboard_url(
        {
            "affiliate_id": "123",
            "campaign_id": "456",
            "switchboard_link": "https://switchboard.actionnetwork.com/offers?affiliateId=123&campaignId=456",
        },
        state="NJ",
        property_key="vegas_insider",
    )

    assert "switchboard.vegasinsider.com/offers" in url
    assert "stateCode=NJ" in url
    assert "propertyId=2" in url
