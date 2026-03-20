"""Phase 4 tests for deterministic generation quality post-processing."""

import pytest

from app.services.draft import (
    _apply_generation_quality_postprocess,
    _build_signup_list,
    _ensure_keyword_in_first_paragraph,
    _generate_signup_steps_structured,
    _is_daily_promos_heading,
    _normalize_matchup_vs_notation,
    _render_bet_example_section_deterministic,
    _remove_inline_compliance_fragments,
    _soften_repetitive_intro_opener,
    _strip_invalid_non_switchboard_links,
    _trim_repeated_phrase_in_html,
)


def test_soften_repetitive_intro_opener_rewrites_put_the_to_work():
    html = "<p>Put the <strong>bet365 promo code</strong> to work for Hawks @ Hornets tonight.</p><p>Second para.</p>"
    cleaned = _soften_repetitive_intro_opener(html)
    assert "Put the" not in cleaned
    assert "is live for Hawks @ Hornets tonight" in cleaned


def test_ensure_keyword_in_first_paragraph_inserts_exact_keyword():
    html = "<p>Charlotte and Atlanta tip tonight on ESPN.</p><p>Later mention bet365 promo code details.</p>"
    cleaned = _ensure_keyword_in_first_paragraph(html, "bet365 promo code")
    assert "The bet365 promo code is live today." in cleaned


def test_normalize_matchup_vs_notation_replaces_at_symbol_in_visible_text_only():
    html = '<p>Hawks @ Hornets is the feature game.</p><a href="https://example.com/a@b">mail</a>'
    cleaned = _normalize_matchup_vs_notation(html)
    assert "Hawks vs. Hornets" in cleaned
    assert "https://example.com/a@b" in cleaned


def test_trim_repeated_see_full_terms_mentions_caps_phrase():
    html = (
        "<p>See full terms for details.</p>"
        "<p>Use the code, then see full terms before entering.</p>"
        "<p>See full terms at checkout.</p>"
    )
    cleaned = _trim_repeated_phrase_in_html(html, "see full terms", max_occurrences=2, replacement="see terms")
    assert cleaned.lower().count("see full terms") == 2
    assert "see terms at checkout" in cleaned.lower()


def test_apply_generation_quality_postprocess_combines_key_fixes():
    html = (
        "<p>Put the <strong>bet365 promo code</strong> to work for Hawks @ Hornets tonight.</p>"
        "<p>See full terms. See full terms. See full terms.</p>"
    )
    cleaned = _apply_generation_quality_postprocess(html, "bet365 promo code")
    assert "Put the" not in cleaned
    assert "Hawks vs. Hornets" in cleaned
    assert cleaned.lower().count("see full terms") <= 2


def test_remove_inline_compliance_fragments_strips_standalone_21_plus():
    html = "<p>Use the code tonight. 21+ only.</p><p>Second para.</p>"
    cleaned = _remove_inline_compliance_fragments(html)
    assert "21+ only" not in cleaned
    assert "Use the code tonight." in cleaned


def test_remove_inline_compliance_fragments_strips_full_operator_terms_line():
    html = "<p>Use the code tonight. Full operator terms apply.</p>"
    cleaned = _remove_inline_compliance_fragments(html)
    assert "Full operator terms apply" not in cleaned


def test_is_daily_promos_heading_accepts_placeholder_variants():
    assert _is_daily_promos_heading("promo update placeholder")
    assert _is_daily_promos_heading("today's promo placeholder")


def test_render_bet_example_section_deterministic_uses_reward_phrase_not_raw_offer_text():
    offer = {
        "brand": "bet365",
        "offer_text": "Bet $10 Get $50 in Bonus Bets",
        "bonus_amount": "$50",
    }
    html = _render_bet_example_section_deterministic(
        offer=offer,
        bet_example_data={
            "bet_amount": 50,
            "selection": "Boston Celtics -4.5",
            "odds": -110,
            "sportsbook_used": "bet365",
        },
        event_context="Featured game: Boston Celtics vs San Antonio Spurs.",
    )
    assert html is not None
    assert "Bet $10 Get $50 in Bonus Bets" not in html
    assert "$50 in bonus bets" in html.lower()


def test_render_bet_example_section_deterministic_builds_contextual_fallback_for_custom_event():
    offer = {
        "brand": "bet365",
        "offer_text": "Bet $10 Get $50 in Bonus Bets",
        "bonus_amount": "$50",
    }
    html = _render_bet_example_section_deterministic(
        offer=offer,
        bet_example_data=None,
        event_context="Featured event: UFC 325 Main Card. Game time: Saturday at 10:00 PM ET.",
    )
    assert html is not None
    assert "UFC 325 Main Card" in html
    assert "$200 in bonus bets" not in html
    assert "$50 in bonus bets" in html.lower()


def test_build_signup_list_uses_state_and_event_instead_of_generic_guide_copy():
    html = _build_signup_list(
        brand="bet365",
        has_code=True,
        code_strong="<strong>ACTION365</strong>",
        state="NJ",
        event_context="Featured event: UFC 325 Main Card.",
    )
    assert "sign-up guide" not in html.lower()
    assert "Open bet365 in NJ and start registration." in html
    assert "UFC 325 Main Card" in html


def test_strip_invalid_non_switchboard_links_unwraps_relative_urls():
    html = '<p>Use the <a href="/bet365-bonus-code">bet365 bonus code</a> and <a data-id="switchboard_tracking" href="https://switchboard.actionnetwork.com/offers?x=1"><strong>ACTION365</strong></a>.</p>'
    cleaned = _strip_invalid_non_switchboard_links(html)
    assert 'href="/bet365-bonus-code"' not in cleaned
    assert "bet365 bonus code" in cleaned
    assert "switchboard.actionnetwork.com" in cleaned


@pytest.mark.asyncio
async def test_generate_signup_steps_structured_rejects_plain_url_steps(monkeypatch):
    async def fake_generate_completion_structured(**kwargs):
        return {
            "steps": [
                "Open the app.",
                "Enter the code.",
                "Verify your account.",
                "Use this guide: https://example.com/guide",
                "Place the qualifying bet.",
            ]
        }

    monkeypatch.setattr(
        "app.services.draft.generate_completion_structured",
        fake_generate_completion_structured,
    )

    steps = await _generate_signup_steps_structured(
        brand="bet365",
        keyword="bet365 bonus code",
        state="NJ",
        has_code=True,
        code_strong="<strong>ACTION365</strong>",
        style_guide="",
        links_md="",
    )

    assert steps is None
