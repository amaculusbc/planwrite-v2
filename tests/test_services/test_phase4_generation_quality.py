"""Phase 4 tests for deterministic generation quality post-processing."""

import pytest

from app.services.draft import (
    _align_selected_link_anchors,
    _apply_generation_quality_postprocess,
    _build_signup_list,
    _clean_orphaned_keyword_page_references,
    _enforce_secondary_keyword_mentions,
    _ensure_primary_keyword_internal_link,
    _ensure_intro_state_specificity,
    _ensure_keyword_in_first_paragraph,
    _extract_featured_label_from_event_context,
    _generate_signup_steps_structured,
    _humanize_article_html,
    _humanizer_preserves_markers,
    _keep_selected_non_switchboard_links,
    _keep_only_primary_non_switchboard_link,
    _is_signup_heading,
    _is_daily_promos_heading,
    _normalize_matchup_vs_notation,
    _offer_excluded_states_text,
    _offer_states_text,
    _adapt_disclaimer_for_dfs,
    _generate_body_section,
    _generate_intro_section,
    _polish_body_section_prose,
    _polish_intro_fallback_phrases,
    _polish_intro_section_prose,
    _remove_generic_state_fallbacks,
    _render_dfs_intro_deterministic,
    _render_dfs_overview_section_deterministic,
    _render_dfs_example_section_deterministic,
    _render_bet_example_section_deterministic,
    _render_terms_section_html,
    _remove_inline_compliance_fragments,
    _resolve_intro_age_conflicts,
    _soften_repetitive_intro_opener,
    _strip_formatting_from_headings,
    _strip_market_mismatch_phrasing,
    _strip_invalid_non_switchboard_links,
    _strip_source_and_prompt_leaks,
    _target_keyword_mentions,
    _trim_dangling_paragraph_endings,
    _trim_repeated_phrase_in_html,
)
from app.services.internal_links import InternalLinkSpec, get_links_by_urls, get_picker_candidates
from app.services.outline import _sanitize_outline_for_market


def test_soften_repetitive_intro_opener_rewrites_put_the_to_work():
    html = "<p>Put the <strong>bet365 promo code</strong> to work for Hawks @ Hornets tonight.</p><p>Second para.</p>"
    cleaned = _soften_repetitive_intro_opener(html)
    assert "Put the" not in cleaned
    assert "is live for Hawks @ Hornets tonight" in cleaned


def test_ensure_keyword_in_first_paragraph_inserts_exact_keyword():
    html = "<p>Charlotte and Atlanta tip tonight on ESPN.</p><p>Later mention bet365 promo code details.</p>"
    cleaned = _ensure_keyword_in_first_paragraph(html, "bet365 promo code")
    assert "bet365 promo code" in cleaned
    assert "Charlotte and Atlanta tip tonight on ESPN." in cleaned
    assert "Here is the relevant" not in cleaned


def test_polish_intro_fallback_phrases_rewrites_awkward_prefixes():
    html = "<p>That puts <a href=\"https://example.com\">bet365 bonus code</a> front and center. Celtics vs. Spurs tips tonight.</p>"
    cleaned = _polish_intro_fallback_phrases(html)
    assert "front and center" not in cleaned
    assert "For readers tracking" in cleaned


def test_ensure_keyword_in_first_paragraph_skips_when_first_paragraph_is_after_h2():
    html = "<h2>Terms & Conditions</h2><p>Deposit required. T&Cs apply.</p>"
    cleaned = _ensure_keyword_in_first_paragraph(html, "bet365 bonus code")
    assert cleaned == html


def test_normalize_matchup_vs_notation_replaces_at_symbol_in_visible_text_only():
    html = '<p>Hawks @ Hornets is the feature game.</p><a href="https://example.com/a@b">mail</a>'
    cleaned = _normalize_matchup_vs_notation(html)
    assert "Hawks vs. Hornets" in cleaned
    assert "https://example.com/a@b" in cleaned


def test_strip_source_and_prompt_leaks_removes_internal_context_phrasing():
    html = (
        "<p>bet365 is supported in Illinois (IL) for this article's requested state context, "
        "but availability varies by location, so confirm in-app.</p>"
        "<p>That works here with no matched event data here to lean on for extra context.</p>"
        "<p>BC Core says this is a playoff-style matchup and you'll typically see the favorite shaded.</p>"
    )

    cleaned = _strip_source_and_prompt_leaks(html)

    assert "requested state context" not in cleaned
    assert "no matched event data" not in cleaned
    assert "BC Core" not in cleaned
    assert "playoff-style" not in cleaned
    assert "typically see" not in cleaned


def test_strip_source_and_prompt_leaks_removes_excerpt_alignment_commentary():
    html = (
        "<p>It reads like a Canada-facing offer, but the excerpt does not fully align "
        "with that framing, so confirm in-app access before entering the code.</p>"
        "<p>Provinces Available: AB, BC.</p>"
    )

    cleaned = _strip_source_and_prompt_leaks(html)

    assert "excerpt" not in cleaned.lower()
    assert "align with that framing" not in cleaned.lower()
    assert "Provinces Available: AB, BC" in cleaned


def test_strip_market_mismatch_phrasing_converts_canada_output():
    html = (
        "<p>21+ and U.S. residents where permitted can use the offer. "
        "States Available: AB, BC, ON. It is not nationwide.</p>"
    )

    cleaned = _strip_market_mismatch_phrasing(html, "CA")

    assert "U.S. residents" not in cleaned
    assert "21+ and" not in cleaned
    assert "States Available" not in cleaned
    assert "nationwide" not in cleaned
    assert "Provinces Available: AB, BC, ON" in cleaned


def test_sanitize_outline_for_canada_removes_us_eligibility_language():
    outline = [
        {
            "level": "intro",
            "title": "",
            "talking_points": [
                "State eligibility clearly and once: 21+ and U.S. residents where permitted, with eligible states listed.",
            ],
            "avoid": ["US states and nationwide language"],
        }
    ]

    cleaned = _sanitize_outline_for_market(outline, "CA")
    rendered = " ".join(cleaned[0]["talking_points"] + cleaned[0]["avoid"])

    assert "U.S. residents" not in rendered
    assert "eligible states" not in rendered.lower()
    assert "US states" not in rendered
    assert "listed Canadian provinces" in rendered


def test_offer_states_text_prefers_selected_sportsbook_offer_states_over_operator_defaults():
    offer = {
        "brand": "bet365",
        "states_list": ["ON", "QC"],
        "terms": "Available in Ontario and Quebec.",
    }

    rendered = _offer_states_text(offer, "ALL")

    assert rendered == "ON, QC"


def test_ensure_intro_state_specificity_respects_existing_province_label():
    html = "<p>Use the offer before tip. Provinces Available: AB, BC, QC.</p>"

    cleaned = _ensure_intro_state_specificity(html, "AB, BC, QC")

    assert cleaned.count("Provinces Available") == 1
    assert "States Available" not in cleaned


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
        "<p>See full terms. See full terms. See full terms, and</p>"
    )
    cleaned = _apply_generation_quality_postprocess(html, "bet365 promo code")
    assert "Put the" not in cleaned
    assert "Hawks vs. Hornets" in cleaned
    assert "see full terms" not in cleaned.lower()
    assert ", and</p>" not in cleaned.lower()


def test_align_selected_link_anchors_rewrites_wrong_anchor_text_to_recommended_anchor():
    html = '<p>Start with <a href="https://www.fantasylabs.com/articles/top-dfs-sites/">Best DFS Apps</a> before lineup lock.</p>'
    links = [
        InternalLinkSpec(
            title="Best DFS Apps",
            url="https://www.fantasylabs.com/articles/top-dfs-sites/",
            recommended_anchors=["top dfs sites", "best dfs apps"],
        )
    ]
    cleaned = _align_selected_link_anchors(html, links, preferred_phrases=["top dfs sites"])
    assert '>top dfs sites<' in cleaned.lower()


def test_get_links_by_urls_merges_required_anchors_for_matching_url():
    links = get_links_by_urls(
        ["https://www.fantasylabs.com/articles/top-dfs-sites/"],
        property_key="fantasy_labs",
    )
    assert links
    anchors = [anchor.lower() for anchor in links[0].recommended_anchors]
    assert "best dfs apps" in anchors
    assert "top dfs sites" in anchors


def test_get_picker_candidates_exposes_broader_property_link_catalog():
    links = get_picker_candidates(property_key="fantasy_labs")
    urls = {str(link.url).rstrip("/").lower() for link in links if getattr(link, "url", None)}
    assert len(urls) >= 6
    assert "https://www.fantasylabs.com/articles/top-dfs-sites" in urls
    assert "https://www.fantasylabs.com/articles/underdog-promo-code" in urls


def test_enforce_secondary_keyword_mentions_repeats_keywords_without_terms_section_pollution():
    html = (
        "<p>Main intro for the article.</p>"
        "<h2>Section One</h2><p>Body copy about the offer.</p>"
        "<h2>Section Two</h2><p>More body copy for the example.</p>"
        "<h2>Terms</h2><p>States Available: NJ, PA.</p>"
    )
    cleaned = _enforce_secondary_keyword_mentions(html, ["best dfs apps"])
    assert cleaned.lower().count("best dfs apps") >= 2
    assert "States Available: NJ, PA." in cleaned
    assert "it also ties into" not in cleaned.lower()


def test_render_terms_section_html_uses_current_state_for_multi_offer_headers():
    html = _render_terms_section_html(
        offers=[
            {
                "brand": "bet365",
                "bonus_code": "ACTION365",
                "states": ["NJ", "PA"],
                "terms": "Available in NJ only.",
            },
            {
                "brand": "FanDuel",
                "bonus_code": "",
                "states": ["NJ", "PA"],
                "terms": "Available in NJ and PA only.",
            },
        ],
        terms="",
        expiration_days=None,
        min_odds="",
        wagering="",
        state="NJ",
    )
    assert "States Available: NJ" in html


def test_remove_inline_compliance_fragments_strips_standalone_21_plus():
    html = "<p>Use the code tonight. 21+ only.</p><p>Second para.</p>"
    cleaned = _remove_inline_compliance_fragments(html)
    assert "21+ only" not in cleaned
    assert "Use the code tonight." in cleaned


def test_remove_inline_compliance_fragments_strips_full_operator_terms_line():
    html = "<p>Use the code tonight. Full operator terms apply.</p>"
    cleaned = _remove_inline_compliance_fragments(html)
    assert "Full operator terms apply" not in cleaned


def test_polish_body_section_prose_removes_legal_leakage_from_body_copy():
    html = "<p>With bet365, you can unlock the bonus as long as you're 21+ and in NJ. New customers only. Time limits and exclusions apply.</p>"
    cleaned = _polish_body_section_prose(html)
    assert "21+" not in cleaned
    assert "New customers only" not in cleaned
    assert "Time limits and exclusions apply" not in cleaned


def test_polish_intro_section_prose_removes_legalistic_intro_fragments():
    html = "<p>Use the code when you sign up as a new customer. You’ll need to be 21+ in the states where bet365 is live.</p>"
    cleaned = _polish_intro_section_prose(html)
    assert "as a new customer" not in cleaned
    assert "21+" not in cleaned


def test_is_daily_promos_heading_accepts_placeholder_variants():
    assert _is_daily_promos_heading("promo update placeholder")
    assert _is_daily_promos_heading("today's promo placeholder")


def test_polish_intro_section_prose_softens_stock_clean_spot_phrase():
    html = "<p>Lakers vs. Thunder is the featured event Tuesday night, and it's a clean spot to use the underdog promo code for extra NBA entries.</p>"
    cleaned = _polish_intro_section_prose(html)
    assert "clean spot" not in cleaned
    assert "good spot" in cleaned


def test_polish_intro_section_prose_rewrites_default_21_plus_state_line():
    html = "<p>This is 21+ and it's not valid in MD, MI, NJ, NY, OH, PA.</p>"
    cleaned = _polish_intro_section_prose(html)
    assert "This is 21+" not in cleaned
    assert "supported states" in cleaned


def test_resolve_intro_age_conflicts_removes_default_21_plus_when_dfs_age_summary_is_18_plus():
    html = "<p>21+ required, and it's only available in supported states. Age note: 18+ (age varies by state).</p>"
    cleaned = _resolve_intro_age_conflicts(html, "18+ (age varies by state)")
    assert "21+ required" not in cleaned
    assert "18+ (age varies by state)" in cleaned


def test_polish_body_section_prose_rewrites_value_is_simple_phrase():
    html = "<p>The value is simple: $50 in bonus entries after a $5 play.</p>"
    cleaned = _polish_body_section_prose(html)
    assert "The value is simple" not in cleaned
    assert "The offer adds $50 in bonus entries after a $5 play." in cleaned


def test_polish_body_section_prose_removes_location_compliance_sentence():
    html = "<p>Quick checkpoint before you fund anything: make sure you're 21+ and physically located in an eligible state, since location services control what contests you can enter.</p>"
    cleaned = _polish_body_section_prose(html)
    assert "physically located in an eligible state" not in cleaned


def test_polish_body_section_prose_removes_sign_up_guide_fallback():
    html = "<p>If you want a quick walkthrough, the Underdog sign-up guide covers the same screens you'll see.</p>"
    cleaned = _polish_body_section_prose(html)
    assert "sign-up guide" not in cleaned.lower()


def test_polish_body_section_prose_removes_any_sign_up_guide_sentence():
    html = "<p>If you want a quick refresher on platforms and formats, start with our Underdog sign-up guide.</p>"
    cleaned = _polish_body_section_prose(html)
    assert "sign-up guide" not in cleaned.lower()


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


def test_render_dfs_example_section_deterministic_uses_qualifying_entry_amount():
    offer = {
        "brand": "Underdog",
        "offer_text": "Play $5, Get $50 in bonus entries",
        "bonus_code": "TOPACTION",
        "qualifying_amount": "$5",
        "bonus_amount": "$50",
        "reward_amount": "$50",
        "reward_label": "bonus entries",
    }
    html = _render_dfs_example_section_deterministic(
        offer=offer,
        bet_example_data=None,
        event_context="Featured game: Los Angeles Lakers vs Oklahoma City Thunder.",
    )
    assert html is not None
    assert "$5" in html
    assert "$50 entry" not in html
    assert "$50 in bonus entries" in html.lower()
    assert "for Los Angeles Lakers vs Oklahoma City Thunder for Los Angeles Lakers vs Oklahoma City Thunder" not in html


def test_render_dfs_intro_deterministic_uses_exact_state_and_age_copy():
    html = _render_dfs_intro_deterministic(
        keyword="underdog promo code",
        offer={
            "brand": "Underdog",
            "offer_text": "Play $5, Get $50 in bonus entries",
            "bonus_code": "TOPACTION",
            "qualifying_amount": "$5",
            "bonus_amount": "$50",
            "reward_label": "bonus entries",
            "terms": "Offer not valid in MD, MI, NJ, NY, OH, and PA.",
        },
        state="TX",
        event_context="Featured game: Los Angeles Lakers vs Oklahoma City Thunder. Game time: Tuesday, May 5, 2026 at 8:30 PM ET. Network: NBC.",
        article_date="Monday, May 4, 2026",
    )
    assert "States Available: AL, AK, AR, CA, DC" in html
    assert "Excluded:" not in html
    assert "18+ (age varies by state)." in html
    assert "21+ required" not in html


def test_render_dfs_intro_deterministic_varies_with_variation_key():
    offer = {
        "brand": "Underdog",
        "offer_text": "Play $5, Get $50 in bonus entries",
        "bonus_code": "TOPACTION",
        "qualifying_amount": "$5",
        "bonus_amount": "$50",
        "reward_label": "bonus entries",
        "terms": "Offer not valid in MD, MI, NJ, NY, OH, and PA.",
    }
    seen = {
        _render_dfs_intro_deterministic(
            keyword="underdog promo code",
            offer=offer,
            state="TX",
            event_context="Featured game: Los Angeles Lakers vs Oklahoma City Thunder. Game time: Tuesday, May 5, 2026 at 8:30 PM ET. Network: NBC.",
            article_date="Monday, May 4, 2026",
            variation_key=f"run-{idx}",
        )
        for idx in range(8)
    }
    assert len(seen) > 1


def test_render_dfs_overview_section_deterministic_avoids_tool_shaped_filler():
    html = _render_dfs_overview_section_deterministic(
        section_title="How Underdog promo code fits Lakers vs. Thunder",
        keyword="underdog promo code",
        offer={
            "brand": "Underdog",
            "offer_text": "Play $5, Get $50 in bonus entries",
            "bonus_code": "TOPACTION",
            "qualifying_amount": "$5",
            "bonus_amount": "$50",
            "reward_label": "bonus entries",
        },
        event_context="Featured game: Los Angeles Lakers vs Oklahoma City Thunder. Game time: Tuesday at 8:30 PM ET. Network: NBC.",
    )
    assert "sign-up guide" not in html.lower()
    assert "bonus bets" not in html.lower()
    assert "$5" in html
    assert "$50 in bonus entries" in html.lower()


def test_render_dfs_example_section_deterministic_varies_with_variation_key():
    offer = {
        "brand": "Underdog",
        "offer_text": "Play $5, Get $50 in bonus entries",
        "bonus_code": "TOPACTION",
        "qualifying_amount": "$5",
        "bonus_amount": "$50",
        "reward_amount": "$50",
        "reward_label": "bonus entries",
    }
    seen = {
        _render_dfs_example_section_deterministic(
            offer=offer,
            bet_example_data=None,
            event_context="Featured game: Los Angeles Lakers vs Oklahoma City Thunder.",
            variation_key=f"run-{idx}",
        )
        for idx in range(8)
    }
    assert len(seen) > 1


def test_build_signup_list_uses_state_and_event_instead_of_generic_guide_copy():
    html = _build_signup_list(
        brand="bet365",
        has_code=True,
        code_strong="<strong>ACTION365</strong>",
        state="NJ",
        event_context="Featured event: UFC 325 Main Card.",
        signup_url="https://switchboard.actionnetwork.com/offers?x=1",
    )
    assert "sign-up guide" not in html.lower()
    assert "sign-up link" in html
    assert "UFC 325 Main Card" in html
    assert "switchboard.actionnetwork.com" in html
    assert 'data-id="switchboard_tracking"' in html


@pytest.mark.asyncio
async def test_generate_intro_section_uses_ai_prompt_for_dfs_intro(monkeypatch):
    captured: dict[str, str] = {}

    async def _fake_generate_completion(*, prompt, system_prompt, temperature, max_tokens):
        captured["prompt"] = prompt
        captured["system_prompt"] = system_prompt
        return "<p>Use underdog promo code TOPACTION for Lakers vs. Thunder tonight.</p><p>States Available: TX. Extra entries land after the first $5 play.</p>"

    monkeypatch.setattr("app.services.draft.generate_completion", _fake_generate_completion)

    html = await _generate_intro_section(
        keyword="underdog promo code",
        title="underdog promo code TOPACTION: Lakers vs. Thunder",
        offer={
            "brand": "Underdog",
            "offer_text": "Play $5, Get $50 in bonus entries",
            "bonus_code": "TOPACTION",
            "qualifying_amount": "$5",
            "bonus_amount": "$50",
            "reward_label": "bonus entries",
            "terms": "Offer not valid in MD, MI, NJ, NY, OH, and PA.",
        },
        all_offers=None,
        state="TX",
        talking_points=[],
        event_context="Featured game: Los Angeles Lakers vs Oklahoma City Thunder. Game time: Tuesday, May 5, 2026 at 8:30 PM ET. Network: NBC.",
        article_date="Monday, May 4, 2026",
        dfs_mode=True,
    )
    assert "VARIATION BRIEF:" in captured["prompt"]
    assert "The intro should feel fresh on each run" in captured["prompt"]
    assert "DFS writer" in captured["system_prompt"]
    assert "TOPACTION" in html
    assert "States Available:" in html


@pytest.mark.asyncio
async def test_generate_body_section_uses_ai_prompt_for_dfs_overview(monkeypatch):
    captured: dict[str, str] = {}

    async def _fake_query_articles(*args, **kwargs):
        return []

    async def _fake_suggest_links(*args, **kwargs):
        return []

    async def _fake_generate_completion(*, prompt, system_prompt, temperature, max_tokens):
        captured["prompt"] = prompt
        captured["system_prompt"] = system_prompt
        return "<p>The extra entries matter on a one-game slate because they let you spread across more builds.</p><p>Use underdog promo code once, then move the credit into different contest paths.</p>"

    monkeypatch.setattr("app.services.draft.generate_completion", _fake_generate_completion)
    monkeypatch.setattr("app.services.draft.query_articles", _fake_query_articles)
    monkeypatch.setattr("app.services.draft.suggest_links_for_section", _fake_suggest_links)

    html = await _generate_body_section(
        section_title="How Underdog promo code fits Lakers vs. Thunder",
        level="h2",
        keyword="underdog promo code",
        offer={
            "brand": "Underdog",
            "offer_text": "Play $5, Get $50 in bonus entries",
            "bonus_code": "TOPACTION",
            "qualifying_amount": "$5",
            "bonus_amount": "$50",
            "reward_label": "bonus entries",
        },
        all_offers=None,
        state="TX",
        offer_property="action_network",
        talking_points=[],
        avoid=[],
        previous_content="",
        event_context="Featured game: Los Angeles Lakers vs Oklahoma City Thunder. Game time: Tuesday at 8:30 PM ET. Network: NBC.",
        dfs_mode=True,
    )
    assert "VARIATION BRIEF:" in captured["prompt"]
    assert "The article should feel new on each run" in captured["prompt"]
    assert "How Underdog promo code fits Lakers vs. Thunder" in captured["prompt"]
    assert "sign-up guide" not in html.lower()
    assert "more builds" in html.lower()


def test_build_signup_list_uses_exact_qualifying_amount_for_dfs_entries():
    html = _build_signup_list(
        brand="Underdog",
        has_code=True,
        code_strong="<strong>TOPACTION</strong>",
        state="TX",
        event_context="Featured game: Los Angeles Lakers vs Oklahoma City Thunder.",
        signup_url="https://switchboard.actionnetwork.com/offers?x=1",
        qualifying_amount="$5",
        dfs_mode=True,
    )
    assert "first $5 fantasy entry" in html


def test_strip_invalid_non_switchboard_links_unwraps_relative_urls():
    html = '<p>Use the <a href="/bet365-bonus-code">bet365 bonus code</a> and <a data-id="switchboard_tracking" href="https://switchboard.actionnetwork.com/offers?x=1"><strong>ACTION365</strong></a>.</p>'
    cleaned = _strip_invalid_non_switchboard_links(html)
    assert 'href="/bet365-bonus-code"' not in cleaned
    assert "bet365 bonus code" in cleaned
    assert "switchboard.actionnetwork.com" in cleaned


def test_target_keyword_mentions_normalizes_case_and_bolds_body_mentions():
    html = "<p>Use BET365 BONUS CODE tonight.</p><p><a href=\"https://example.com\">bet365 bonus code</a> later.</p>"
    cleaned = _target_keyword_mentions(html, "bet365 bonus code")
    assert "<strong>bet365 bonus code</strong>" in cleaned
    assert ">bet365 bonus code<" in cleaned


def test_target_keyword_mentions_leaves_heading_case_alone():
    html = "<h2>How Underdog promo code fits Lakers vs. Thunder</h2><p>Use underdog promo code tonight.</p>"
    cleaned = _target_keyword_mentions(html, "underdog promo code")
    assert "<h2>How Underdog promo code fits Lakers vs. Thunder</h2>" in cleaned
    assert "<strong>underdog promo code</strong>" in cleaned


def test_remove_generic_state_fallbacks_drops_vague_duplicate_line():
    html = "<p>States Available: AZ, CO, IA.</p><p>States Available: eligible states listed by the operator.</p>"
    cleaned = _remove_generic_state_fallbacks(html)
    assert "eligible states listed by the operator" not in cleaned


def test_strip_formatting_from_headings_removes_strong_and_links():
    html = "<h1><strong>underdog promo code</strong> TOPACTION</h1><h2>How to Sign Up for <a href=\"https://example.com\">underdog promo code</a></h2>"
    cleaned = _strip_formatting_from_headings(html)
    assert "<strong>" not in cleaned
    assert "<a href=" not in cleaned
    assert "<h2>How to Sign Up for underdog promo code</h2>" in cleaned


def test_is_signup_heading_treats_how_to_claim_as_signup_flow():
    assert _is_signup_heading("how to claim underdog promo code")


def test_clean_orphaned_keyword_page_references_removes_trailing_keyword_stub():
    html = "<p>For the full rundown, start at the <strong>Underdog offer</strong> page: <strong>underdog promo code</strong>.</p>"
    cleaned = _clean_orphaned_keyword_page_references(html, "underdog promo code")
    assert "page: <strong>underdog promo code</strong>" not in cleaned
    assert "page.</p>" in cleaned


def test_clean_orphaned_keyword_page_references_removes_page_at_keyword_stub():
    html = "<p>You can always reference the <strong>Underdog offer</strong> page at <strong>underdog promo code</strong> for the full details.</p>"
    cleaned = _clean_orphaned_keyword_page_references(html, "underdog promo code")
    assert "page at <strong>underdog promo code</strong>" not in cleaned
    assert "page for the full details." in cleaned


def test_ensure_primary_keyword_internal_link_rewrites_existing_external_keyword_anchor():
    html = '<p>Use the <a href="https://www.bet365.com">bet365 bonus code</a> today.</p>'
    cleaned = _ensure_primary_keyword_internal_link(
        html,
        "bet365 bonus code",
        "https://www.actionnetwork.com/online-sports-betting/reviews/bet365",
    )
    assert 'href="https://www.actionnetwork.com/online-sports-betting/reviews/bet365"' in cleaned
    assert "bet365.com" not in cleaned


def test_keep_only_primary_non_switchboard_link_unwraps_other_links():
    html = (
        '<p><a href="https://www.bet365.com">bet365</a> text.</p>'
        '<p><a href="https://www.actionnetwork.com/online-sports-betting/reviews/bet365">bet365 bonus code</a></p>'
        '<p><a data-id="switchboard_tracking" href="https://switchboard.actionnetwork.com/offers?x=1">Claim</a></p>'
    )
    cleaned = _keep_only_primary_non_switchboard_link(
        html,
        "https://www.actionnetwork.com/online-sports-betting/reviews/bet365",
    )
    assert "switchboard.actionnetwork.com" in cleaned
    assert 'href="https://www.actionnetwork.com/online-sports-betting/reviews/bet365"' in cleaned
    assert "https://www.bet365.com" not in cleaned


def test_keep_selected_non_switchboard_links_preserves_only_requested_urls():
    html = (
        '<p><a href="https://www.actionnetwork.com/online-sports-betting/reviews/bet365">bet365 bonus code</a></p>'
        '<p><a href="https://www.actionnetwork.com/online-sports-betting/reviews/draftkings">draftkings promo code</a></p>'
        '<p><a data-id="switchboard_tracking" href="https://switchboard.actionnetwork.com/offers?x=1">Claim</a></p>'
    )
    cleaned = _keep_selected_non_switchboard_links(
        html,
        ["https://www.actionnetwork.com/online-sports-betting/reviews/draftkings"],
        fallback_primary_url="https://www.actionnetwork.com/online-sports-betting/reviews/bet365",
    )
    assert "switchboard.actionnetwork.com" in cleaned
    assert 'href="https://www.actionnetwork.com/online-sports-betting/reviews/draftkings"' in cleaned
    assert "https://www.actionnetwork.com/online-sports-betting/reviews/bet365" not in cleaned


def test_trim_dangling_paragraph_endings_removes_trailing_conjunction_fragment():
    html = '<p>If you want the full breakdown of the <a href="https://www.actionnetwork.com/online-sports-betting/reviews/bet365">bet365 bonus code</a> details, that page lays it out, and</p>'
    cleaned = _trim_dangling_paragraph_endings(html)
    assert cleaned.endswith("details, that page lays it out.</p>")


def test_offer_states_text_uses_curated_underdog_states_for_dfs():
    offer = {
        "brand": "Underdog",
        "states_list": [],
        "terms": "Offer not valid in MD, MI, NJ, NY, OH, and PA.",
    }
    rendered = _offer_states_text(offer, "ALL", dfs_mode=True)
    assert rendered.startswith("AL, AK, AR, CA, DC")
    assert "NJ" not in rendered


def test_adapt_disclaimer_for_dfs_removes_default_21_plus():
    cleaned = _adapt_disclaimer_for_dfs("21+. Gambling problem? Call 1-800-GAMBLER. Please bet responsibly.")
    assert cleaned.startswith("Need help?")
    assert "21+" not in cleaned


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


def test_humanizer_preserves_markers_rejects_fact_drift():
    original = "<p>Use ACTION365 to get $150 in bonus bets. States Available: NJ, PA.</p>"
    rewritten = "<p>Use ACTION365 to get $200 in bonus bets. States Available: NJ, PA.</p>"
    assert not _humanizer_preserves_markers(original, rewritten, {}, offer={"bonus_code": "ACTION365"})


def test_extract_featured_label_from_event_context_keeps_vs_period():
    label = _extract_featured_label_from_event_context(
        "Featured game: Celtics vs. Spurs. Game time: Friday, May 8 at 8:00 PM ET. Network: ESPN"
    )
    assert label == "Celtics vs. Spurs"


def test_offer_excluded_states_text_omits_irrelevant_exclusion_for_selected_state():
    offer = {"terms": "Deposit required. Not available in Illinois."}
    assert _offer_excluded_states_text(offer, current_state="NJ") == ""
    assert _offer_excluded_states_text(offer, current_state="IL") == "IL"


@pytest.mark.asyncio
async def test_humanize_article_html_only_rewrites_safe_sections(monkeypatch):
    async def fake_generate_completion_structured(**kwargs):
        return {
            "rewrites": [
                "<p>For readers tracking bet365 bonus code, Celtics vs. Spurs tips tonight and the $150 offer is in play.</p>"
                "<p>Use [[KEEP_0]] at sign-up and keep the focus on the matchup instead of filler copy. States Available: NJ, PA.</p>",
                "<p>This version reads tighter while keeping the same offer details and event hook.</p>",
            ]
        }

    monkeypatch.setattr(
        "app.services.draft.generate_completion_structured",
        fake_generate_completion_structured,
    )

    html = (
        "<h1>bet365 bonus code: Celtics vs. Spurs</h1>"
        "<p>bet365 bonus code is live for Celtics vs. Spurs tonight.</p>"
        "<p>Use <strong>ACTION365</strong> at sign-up. States Available: NJ, PA.</p>"
        "<h2>Why bet365 bonus code is worth a look for Celtics vs. Spurs</h2>"
        "<p>This is the kind of stock filler section that should be cleaned up.</p>"
        "<h2>Sign-Up Steps Before Celtics vs. Spurs</h2>"
        "<ol><li>Open the site.</li><li>Enter ACTION365.</li></ol>"
        "<h2>Terms & Conditions</h2>"
        "<p>Bonus bets expire in 7 days.</p>"
    )

    cleaned = await _humanize_article_html(
        html,
        keyword="bet365 bonus code",
        offer={"brand": "bet365", "bonus_code": "ACTION365"},
    )

    assert "For readers tracking bet365 bonus code" in cleaned
    assert "This version reads tighter" in cleaned
    assert "<ol><li>Open the site.</li><li>Enter ACTION365.</li></ol>" in cleaned
    assert "<p>Bonus bets expire in 7 days.</p>" in cleaned


@pytest.mark.asyncio
async def test_humanize_article_html_reverts_section_when_facts_drift(monkeypatch):
    async def fake_generate_completion_structured(**kwargs):
        return {
            "rewrites": [
                "<p>bet365 bonus code is live for Celtics vs. Spurs tonight.</p>"
                "<p>Use [[KEEP_0]] at sign-up to get $200 in bonus bets. States Available: NJ, PA.</p>",
            ]
        }

    monkeypatch.setattr(
        "app.services.draft.generate_completion_structured",
        fake_generate_completion_structured,
    )

    html = (
        "<h1>bet365 bonus code: Celtics vs. Spurs</h1>"
        "<p>bet365 bonus code is live for Celtics vs. Spurs tonight.</p>"
        "<p>Use <strong>ACTION365</strong> at sign-up to get $150 in bonus bets. States Available: NJ, PA.</p>"
        "<h2>Sign-Up Steps Before Celtics vs. Spurs</h2>"
        "<ol><li>Open the site.</li></ol>"
    )

    cleaned = await _humanize_article_html(
        html,
        keyword="bet365 bonus code",
        offer={"brand": "bet365", "bonus_code": "ACTION365"},
    )

    assert "$150 in bonus bets" in cleaned
    assert "$200 in bonus bets" not in cleaned
