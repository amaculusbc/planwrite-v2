"""Prediction-market language guardrail tests."""

import pytest

import app.services.draft as draft_mod
from app.services.draft import (
    _build_signup_list,
    _generate_body_section,
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


def test_prediction_market_outline_titles_use_how_to_use():
    titles = _contextual_section_titles(
        keyword="kalshi promo code",
        brand="Kalshi",
        event_context="",
        is_prediction_market=True,
    )
    assert titles["claim"].startswith("How to Use")


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


def test_render_prediction_market_example_section_deterministic_uses_contract_math():
    html = _render_prediction_market_example_section_deterministic(
        offer={
            "brand": "Novig",
            "offer_text": "Spend $25, Get $50 in Novig Coins",
            "bonus_code": "ACTION",
            "qualifying_amount": "$25",
            "bonus_amount": "$50",
            "reward_label": "Novig Coins",
        },
        bet_example_data=None,
        event_context="Featured event: NBA Finals MVP Market.",
    )
    assert html is not None
    assert "contracts" in html.lower()
    assert "$50 in Novig Coins" in html
    assert "bonus bets" not in html.lower()
    assert "$25 qualifying action" in html
    assert "position on a Yes position on" not in html


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
async def test_generate_body_section_uses_ai_prompt_for_prediction_market_paths(monkeypatch):
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
    assert len(prompts) == 2
    assert all("VARIATION BRIEF:" in prompt for prompt in prompts)
    assert "EXACT MECHANICS REFERENCE" in prompts[1]
    assert "$25 qualifying action" in prompts[1]
    assert "contracts" in prompts[1].lower()
    assert "Novig Coins" in overview
    assert "market math" in claim.lower()
