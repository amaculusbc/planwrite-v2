"""Prediction-market language guardrail tests."""

import pytest

import app.services.draft as draft_mod
from app.services.draft import (
    _build_signup_list,
    _generate_body_section,
    _is_claim_heading,
    _is_signup_heading,
    _generate_intro_section,
    _render_prediction_market_example_section_deterministic,
    _render_prediction_market_intro_deterministic,
    _render_prediction_market_overview_section_deterministic,
    _render_terms_section_html,
    _strip_placeholder_hash_links,
)
from app.services.internal_links import InternalLinkSpec, format_links_markdown
from app.services.operator_profile import is_prediction_market_context, is_prediction_market_offer
from app.services.outline import _contextual_section_titles


def test_prediction_market_detection_helpers():
    assert is_prediction_market_context("Kalshi promo code")
    assert is_prediction_market_offer({"brand": "Polymarket", "offer_text": "Get bonus"})
    assert not is_prediction_market_context("BetMGM promo code")


def test_prediction_market_signup_fallback_avoids_bet_terms():
    html = _build_signup_list("Kalshi", has_code=True, code_strong="<strong>KALSHI</strong>", prediction_market=True)
    assert "market" in html or "position" in html or "contract" in html
    assert "qualifying bet" not in html
    assert 'href="#"' not in html


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


def test_prediction_market_guardrail_preserves_bam_shortcode_attributes():
    html = '[bam-inline-promotion placement-id="2066" property-id="326" affiliate-type="social-sportsbook" affiliate="Novig"]'

    cleaned = draft_mod._apply_content_mode_language_guardrails(html, "prediction_market")

    assert 'affiliate-type="social-sportsbook"' in cleaned
    assert "social-operators" not in cleaned


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
    assert "(#)" not in md


def test_strip_placeholder_hash_links_removes_dummy_anchors():
    html = '<p>Use <a href="#">sign-up guide</a> then <a href="https://example.com">real link</a>.</p>'
    cleaned = _strip_placeholder_hash_links(html)
    assert 'href="#"' not in cleaned
    assert "sign-up guide" in cleaned
    assert '<a href="https://example.com">real link</a>' in cleaned


def test_prediction_market_outline_titles_are_question_mapped():
    """Nick's AIO ask: headings are the questions a reader or an LLM actually asks."""
    titles = _contextual_section_titles(
        keyword="kalshi promo code",
        brand="Kalshi",
        event_context="",
        is_prediction_market=True,
    )
    for slot in ("overview", "claim", "signup", "terms"):
        assert titles[slot].endswith("?"), f"{slot} heading is not a question: {titles[slot]}"


def test_prediction_market_question_headings_still_route_to_their_sections():
    """Section routing keys off heading text, so questions must match the same matchers."""
    for variation_key in [str(index) for index in range(8)]:
        for event_context in ("Featured event: Spain vs. France. Game time: 3:00 PM ET.", ""):
            titles = _contextual_section_titles(
                keyword="Kalshi promo code",
                brand="Kalshi",
                event_context=event_context,
                is_prediction_market=True,
                variation_key=variation_key,
            )
            signup = titles["signup"].lower()
            claim = titles["claim"].lower()
            assert _is_signup_heading(signup), f"signup heading stopped routing: {signup}"
            assert _is_claim_heading(claim, _is_signup_heading(claim)), (
                f"claim heading stopped routing: {claim}"
            )
            # Both orchestrators gate the terms renderer on this substring.
            assert "terms" in titles["terms"].lower()


def test_render_prediction_market_intro_deterministic_uses_offer_facts():
    html = _render_prediction_market_intro_deterministic(
        keyword="Novig promo code",
        offer={
            "brand": "Novig",
            "offer_text": "Spend $25, Get $50 in Novig Coins",
            "bonus_code": "ACTION",
            "qualifying_amount": "$25",
            "bonus_amount": "$50",
            "reward_label": "Novig Coins",
        },
        state="ALL",
        event_context="Featured event: NBA Finals MVP Market. Game time: Tuesday, May 5, 2026 at 8:30 PM ET.",
        article_date="Tuesday, May 5, 2026",
    )
    assert "Novig promo code ACTION" in html
    assert "$50 in Novig Coins" in html
    assert "$25 qualifying action" in html


def test_render_prediction_market_intro_deterministic_varies_with_variation_key():
    offer = {
        "brand": "Novig",
        "offer_text": "Spend $25, Get $50 in Novig Coins",
        "bonus_code": "ACTION",
        "qualifying_amount": "$25",
        "bonus_amount": "$50",
        "reward_label": "Novig Coins",
    }
    seen = {
        _render_prediction_market_intro_deterministic(
            keyword="Novig promo code",
            offer=offer,
            state="ALL",
            event_context="Featured event: NBA Finals MVP Market. Game time: Tuesday, May 5, 2026 at 8:30 PM ET.",
            article_date="Tuesday, May 5, 2026",
            variation_key=f"run-{idx}",
        )
        for idx in range(8)
    }
    assert len(seen) > 1


def test_render_prediction_market_overview_section_deterministic_avoids_sportsbook_terms():
    html = _render_prediction_market_overview_section_deterministic(
        section_title="Why Novig promo code fits NBA Finals MVP Market",
        keyword="Novig promo code",
        offer={
            "brand": "Novig",
            "offer_text": "Spend $25, Get $50 in Novig Coins",
            "bonus_code": "ACTION",
            "qualifying_amount": "$25",
            "bonus_amount": "$50",
            "reward_label": "Novig Coins",
        },
        event_context="Featured event: NBA Finals MVP Market. Game time: Tuesday, May 5, 2026 at 8:30 PM ET.",
    )
    assert "bonus bets" not in html.lower()
    assert "market positions" in html.lower() or "positions" in html.lower()
    assert "$50 in Novig Coins" in html


_NOVIG_OFFER = {
    "brand": "Novig",
    "offer_text": "Spend $25, Get $50 in Novig Coins",
    "bonus_code": "ACTION",
    "qualifying_amount": "$25",
    "bonus_amount": "$50",
    "reward_label": "Novig Coins",
}


def test_render_prediction_market_example_section_omits_price_without_matched_market():
    """Without a matched market there is no real contract price, so none may publish."""
    html = _render_prediction_market_example_section_deterministic(
        offer=_NOVIG_OFFER,
        bet_example_data=None,
        event_context="Featured event: NBA Finals MVP Market.",
    )
    assert html is not None
    assert "per contract" not in html.lower()
    assert "contracts" not in html.lower()
    assert "$0.50" not in html
    # The money still has to trace: the qualifying action triggers the reward that funds the position.
    assert "$25 qualifying action" in html
    assert "$50 in Novig Coins" in html


def test_render_prediction_market_example_section_prices_from_matched_market():
    html = _render_prediction_market_example_section_deterministic(
        offer=_NOVIG_OFFER,
        bet_example_data={
            "qualifying_amount": 25,
            "reward_amount": 50,
            "position_amount": 50,
            "entry_price": 0.40,
            "settlement_price": 1.0,
            "selection": "Yes",
            "market_title": "Will Player X win MVP?",
            "prediction_market": {"provider": "novig", "yes_price": 0.40},
        },
        event_context="Featured event: NBA Finals MVP Market.",
    )
    assert html is not None
    assert "$0.40 per contract" in html
    assert "125 contracts" in html  # $50 of credits / $0.40
    assert "$50 in Novig Coins" in html


def test_render_prediction_market_example_section_uses_selected_market_title():
    html = _render_prediction_market_example_section_deterministic(
        offer={
            "brand": "Kalshi",
            "offer_text": "Spend $10, Get $50 in promo credits",
            "qualifying_amount": 10,
            "reward_amount": 50,
        },
        bet_example_data={
            "qualifying_amount": 10,
            "position_amount": 25,
            "entry_price": 0.56,
            "settlement_price": 1,
            "selection": "Mexico",
            "market_title": "Will Mexico beat South Africa?",
            "prediction_market": {
                "provider": "kalshi",
                "provider_market_id": "KXTEST",
                "market_title": "Will Mexico beat South Africa?",
            },
        },
        event_context="Featured event: Mexico vs. South Africa.",
    )

    assert html is not None
    # The raw question-mark market title is humanized before it reaches copy.
    assert "Will Mexico beat South Africa" in html
    assert "Will Mexico beat South Africa?" not in html
    assert "bonus bets" not in html.lower()
    assert "$10 qualifying action" in html
    assert "position on a Yes position on" not in html
    assert "If I complete" not in html
    assert "$1.00 settlement pays" in html or "A close at $1.00 returns" in html


@pytest.mark.asyncio
async def test_generate_intro_section_uses_ai_prompt_for_prediction_market(monkeypatch):
    captured: dict[str, str] = {}

    async def _fake_generate_completion(*, prompt, system_prompt, temperature, max_tokens):
        captured["prompt"] = prompt
        captured["system_prompt"] = system_prompt
        return "<p>Novig promo code ACTION is live around the NBA Finals MVP market.</p><p>Spend $25, then use the $50 in Novig Coins on later positions.</p>"

    monkeypatch.setattr(draft_mod, "generate_completion", _fake_generate_completion)

    html = await _generate_intro_section(
        keyword="Novig promo code",
        title="Novig promo code ACTION",
        offer={
            "brand": "Novig",
            "offer_text": "Spend $25, Get $50 in Novig Coins",
            "bonus_code": "ACTION",
            "qualifying_amount": "$25",
            "bonus_amount": "$50",
            "reward_label": "Novig Coins",
        },
        all_offers=None,
        state="ALL",
        talking_points=[],
        event_context="Featured event: NBA Finals MVP Market. Game time: Tuesday, May 5, 2026 at 8:30 PM ET.",
        article_date="Tuesday, May 5, 2026",
        prediction_market=True,
    )
    assert "VARIATION BRIEF:" in captured["prompt"]
    assert "feel fresh on each run" in captured["prompt"]
    assert "Use prediction-market terms only" in captured["prompt"]
    assert "Novig promo code ACTION" in html
    assert "Novig Coins" in html


@pytest.mark.asyncio
async def test_generate_body_section_publishes_deterministic_prediction_market_example(monkeypatch):
    prompts: list[str] = []

    async def _fake_query_articles(*args, **kwargs):
        return []

    async def _fake_suggest_links(*args, **kwargs):
        return []

    async def _fake_generate_completion(*, prompt, system_prompt, temperature, max_tokens):
        prompts.append(prompt)
        if "How to Use Novig promo code" in prompt:
            return "<p>If I use the first $25 qualifying action, I can open later positions with the $50 in Novig Coins.</p><p>That keeps the market math intact without drifting into betting language.</p>"
        return "<p>The offer gives prediction-market users more flexibility after the first action.</p><p>The $50 in Novig Coins helps when you want to spread across more than one market angle.</p>"

    monkeypatch.setattr(draft_mod, "generate_completion", _fake_generate_completion)
    monkeypatch.setattr(draft_mod, "query_articles", _fake_query_articles)
    monkeypatch.setattr(draft_mod, "suggest_links_for_section", _fake_suggest_links)

    overview = await _generate_body_section(
        section_title="Why Novig promo code fits NBA Finals MVP Market",
        level="h2",
        keyword="Novig promo code",
        offer={
            "brand": "Novig",
            "offer_text": "Spend $25, Get $50 in Novig Coins",
            "bonus_code": "ACTION",
            "qualifying_amount": "$25",
            "bonus_amount": "$50",
            "reward_label": "Novig Coins",
        },
        all_offers=None,
        state="ALL",
        offer_property="action_network",
        talking_points=[],
        avoid=[],
        previous_content="",
        event_context="Featured event: NBA Finals MVP Market. Game time: Tuesday, May 5, 2026 at 8:30 PM ET.",
        prediction_market=True,
    )
    claim = await _generate_body_section(
        section_title="How to Use Novig promo code on NBA Finals MVP Market",
        level="h2",
        keyword="Novig promo code",
        offer={
            "brand": "Novig",
            "offer_text": "Spend $25, Get $50 in Novig Coins",
            "bonus_code": "ACTION",
            "qualifying_amount": "$25",
            "bonus_amount": "$50",
            "reward_label": "Novig Coins",
        },
        all_offers=None,
        state="ALL",
        offer_property="action_network",
        talking_points=[],
        avoid=[],
        previous_content="",
        event_context="Featured event: NBA Finals MVP Market. Game time: Tuesday, May 5, 2026 at 8:30 PM ET.",
        prediction_market=True,
    )
    # The overview is still model-written; the worked example is not. Handing the model the
    # mechanics as a reference let it rewrite the amounts, so the claim section now publishes
    # straight from offer data and never reaches the model.
    assert len(prompts) == 1
    assert "VARIATION BRIEF:" in prompts[0]
    assert "Novig Coins" in overview
    # The claim traces the money: the $25 action triggers the reward, which funds the position.
    assert "$25 qualifying action" in claim
    assert "$50 in Novig Coins" in claim


def test_kalshi_ticker_game_date_parses_and_maps_series():
    from app.services.prediction_markets import _kalshi_ticker_game_date, _KALSHI_GAME_SERIES

    assert _kalshi_ticker_game_date("KXMLBGAME-26SEP032210STLLAD-STL") == "2026-09-03"
    assert _kalshi_ticker_game_date("KXNFLGAME-26AUG261905HOUNYY") == "2026-08-26"
    assert _kalshi_ticker_game_date("no-date-here") == ""
    # Every mapped sport points at a Kalshi game-winner series ticker.
    assert _KALSHI_GAME_SERIES["mlb"] == "KXMLBGAME"
    assert _KALSHI_GAME_SERIES["nfl"] == "KXNFLGAME"
    assert _KALSHI_GAME_SERIES["nba"] == "KXNBAGAME"
