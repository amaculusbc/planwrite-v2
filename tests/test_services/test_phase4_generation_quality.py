"""Phase 4 tests for deterministic generation quality post-processing."""

import pytest

from app.services.draft import (
    TOP_STORY_TRACKING_TAG,
    _align_selected_link_anchors,
    _apply_generation_quality_postprocess,
    _build_signup_list,
    _body_word_count_for_editorial_target,
    _cap_primary_keyword_density,
    _clean_orphaned_keyword_page_references,
    _count_keyword,
    _enforce_secondary_keyword_mentions,
    _ensure_primary_keyword_internal_link,
    _ensure_first_paragraph_keyword_internal_link,
    _ensure_top_story_tracking_tag,
    _ensure_intro_state_specificity,
    _ensure_keyword_in_first_paragraph,
    _enforce_primary_keyword_density,
    _ensure_editorial_body_length,
    _ensure_matchup_analysis_section,
    _extract_featured_label_from_event_context,
    _generate_signup_steps_structured,
    _humanize_article_html,
    _humanizer_preserves_markers,
    _keep_selected_non_switchboard_links,
    _keep_only_primary_non_switchboard_link,
    _is_signup_heading,
    _is_daily_promos_heading,
    _convert_availability_labels_to_prose,
    _decapitalize_inline_reward_mentions,
    _naturalize_bc_core_editorial_point,
    _normalize_brand_casing,
    _normalize_matchup_vs_notation,
    _offer_reward_phrase_visible,
    _title_case_headings,
    _offer_excluded_states_text,
    _offer_states_text,
    _adapt_disclaimer_for_dfs,
    _generate_body_section,
    _generate_intro_section,
    generate_draft_from_outline,
    _polish_body_section_prose,
    _polish_conditional_user_openers,
    _polish_intro_fallback_phrases,
    _polish_intro_section_prose,
    _polish_worked_example_conditionals,
    _remove_generic_state_fallbacks,
    _render_dfs_intro_deterministic,
    _render_dfs_overview_section_deterministic,
    _render_dfs_example_section_deterministic,
    _render_bet_example_section_deterministic,
    _render_terms_section_html,
    _remove_inline_compliance_fragments,
    _resolve_intro_age_conflicts,
    _select_bc_core_editorial_points,
    _soften_repetitive_intro_opener,
    _strip_formatting_from_headings,
    _strip_market_mismatch_phrasing,
    _strip_invalid_non_switchboard_links,
    _strip_unprovided_article_date,
    _strip_source_and_prompt_leaks,
    _target_keyword_mentions,
    _trim_dangling_paragraph_endings,
    _trim_repeated_phrase_in_html,
    _unwrap_generic_offer_strong,
    today_long,
)
from app.services.internal_links import InternalLinkSpec, get_links_by_urls, get_picker_candidates
from app.services.outline import _sanitize_outline_for_market


def test_soften_repetitive_intro_opener_rewrites_put_the_to_work():
    html = "<p>Put the <strong>bet365 promo code</strong> to work for Hawks @ Hornets tonight.</p><p>Second para.</p>"
    cleaned = _soften_repetitive_intro_opener(html)
    assert "Put the" not in cleaned
    assert "is live for Hawks @ Hornets tonight" in cleaned


def test_ensure_top_story_tracking_tag_appends_exact_tag_once():
    html = "<h1>Title</h1><p>Article body.</p>"
    cleaned = _ensure_top_story_tracking_tag(html)
    assert cleaned.endswith(TOP_STORY_TRACKING_TAG)
    assert cleaned.count("view_top_story") == 1
    assert "<h1>Title</h1>" in cleaned


def test_ensure_top_story_tracking_tag_dedupes_existing_variant():
    html = "<p>Article body.</p>\n<script>gtag(\"event\", \"view_top_story\")</script>"
    cleaned = _ensure_top_story_tracking_tag(html)
    assert cleaned == f"<p>Article body.</p>\n{TOP_STORY_TRACKING_TAG}"
    assert cleaned.count("<script>") == 1


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


def test_polish_intro_fallback_phrases_rewrites_if_youre_following_linked_keyword():
    html = '<p>If you’re following <a href="https://example.com">bet365 bonus code</a>, Celtics vs. Spurs tips tonight.</p>'
    cleaned = _polish_intro_fallback_phrases(html)
    assert "If you" not in cleaned
    assert 'For readers tracking <a href="https://example.com">bet365 bonus code</a>' in cleaned


def test_polish_intro_fallback_phrases_rewrites_if_youre_looking_for_keyword():
    html = '<p>Chelsea vs. Arsenal kicks off tonight. If you\'re looking for <a href="https://example.com">bet365 bonus code</a>, this is the offer.</p>'
    cleaned = _polish_intro_fallback_phrases(html)
    assert "If you" not in cleaned
    assert 'For readers looking for <a href="https://example.com">bet365 bonus code</a>' in cleaned


def test_polish_worked_example_conditionals_rewrites_common_if_phrasing():
    html = (
        "<p>If it wins, my profit is $45.45, and I get my $50 stake back. "
        "If it loses, I'm down $50 on the bet.</p>"
        "<p>If I put $200 in bonus bets on another NBA pick at -110 and it wins, "
        "the payout is profit-only: $181.82.</p>"
    )
    cleaned = _polish_worked_example_conditionals(html)
    assert "If it wins" not in cleaned
    assert "If it loses" not in cleaned
    assert "If I put" not in cleaned
    assert "A win puts the profit at $45.45" in cleaned
    assert "A loss leaves me down $50" in cleaned
    assert "A later $200 bonus bet on another NBA pick at -110 pays profit-only" in cleaned
    assert "$181.82" not in cleaned
    assert "bonus-bet stake itself does not return" in cleaned


def test_polish_conditional_user_openers_rewrites_if_youre_patterns():
    html = (
        "<p>If you’re already targeting Celtics vs. Spurs, this is an easy way to stretch one wager.</p>"
        "<p>If you’re signing up for UFC 325, enter TOPACTION at registration.</p>"
    )
    cleaned = _polish_conditional_user_openers(html)
    assert "If you" not in cleaned
    assert "For Celtics vs. Spurs, this is an easy way" in cleaned
    assert "When signing up for UFC 325, enter TOPACTION" in cleaned


def test_strip_unprovided_article_date_removes_inferred_today_only_when_missing():
    today = today_long()
    html = (
        f"<p>{today} turns attention to the UFC 325 main card ahead of Saturday night.</p>"
        f"<p>{today}: Celtics vs. Spurs tips at 8:00 PM ET.</p>"
        f"<p>{today} sets up a straightforward opportunity for Celtics vs. Spurs.</p>"
        f"<p>{today} pairs well with UFC 325 Main Card.</p>"
        f"<p>{today} lines up perfectly with UFC 325 Main Card.</p>"
        f"<p>{today} lines up nicely with UFC 325 Main Card.</p>"
    )
    cleaned = _strip_unprovided_article_date(html, article_date="")
    assert today not in cleaned
    assert "The UFC 325 main card" in cleaned
    assert "Celtics vs. Spurs tips" in cleaned
    assert "a straightforward opportunity for Celtics vs. Spurs" in cleaned
    assert "UFC 325 Main Card" in cleaned
    preserved = _strip_unprovided_article_date(html, article_date=today)
    assert today in preserved


def test_ensure_keyword_in_first_paragraph_skips_when_first_paragraph_is_after_h2():
    html = "<h2>Terms & Conditions</h2><p>Deposit required. T&Cs apply.</p>"
    cleaned = _ensure_keyword_in_first_paragraph(html, "bet365 bonus code")
    assert cleaned == html


def test_ensure_first_paragraph_keyword_internal_link_prioritizes_intro_keyword():
    html = (
        "<h1>bet365 Bonus Code</h1>"
        "<p>Use bet365 bonus code tonight for the main offer.</p>"
        "<h2>More Details</h2>"
        "<p>Later bet365 bonus code coverage should stay plain.</p>"
    )
    cleaned = _ensure_first_paragraph_keyword_internal_link(
        html,
        "bet365 bonus code",
        "https://example.com/bet365-bonus-code",
    )
    assert '<a href="https://example.com/bet365-bonus-code">bet365 bonus code</a>' in cleaned
    assert cleaned.count('href="https://example.com/bet365-bonus-code"') == 1


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


def test_strip_source_and_prompt_leaks_removes_market_match_leak_and_terms_typo():
    html = (
        "<p>One quick note: we do not have a clean, pre-loaded market match for both teams, "
        "so double-check the game.</p>"
        "<p>One note on our end: we did not have a clean event match for both teams in our feed, "
        "so double-check the listing.</p>"
        "<p>One quick note: our event feed did not align cleanly with both teams for extra market callouts.</p>"
        "<p>One quick note: our event feed did not cleanly match both teams for Chelsea vs Arsenal, "
        "so you may need to search for the match manually.</p>"
        "<p>This is a clean EPL wager for Chelsea vs. Arsenal.</p>"
        "<p>Minimum odds -500 of greater.</p>"
    )

    cleaned = _strip_source_and_prompt_leaks(html)

    assert "pre-loaded market match" not in cleaned
    assert "clean event match" not in cleaned
    assert "our feed" not in cleaned
    assert "event feed" not in cleaned
    assert "EPL wager" not in cleaned
    assert "soccer wager" in cleaned
    assert "Minimum odds -500 or greater" in cleaned


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


def test_ensure_intro_state_specificity_converts_province_label_to_prose():
    html = "<p>Use the offer before tip. Provinces Available: AB, BC, QC.</p>"

    cleaned = _ensure_intro_state_specificity(html, "AB, BC, QC")

    assert "Provinces Available" not in cleaned
    assert "States Available" not in cleaned
    assert cleaned.count("The offer is available in AB, BC, QC.") == 1


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


def test_enforce_secondary_keyword_mentions_removes_forced_backfill_without_inserting():
    html = (
        "<p>Main intro for the article.</p>"
        "<p>Second intro paragraph about the offer.</p>"
        "<h2>Section One</h2><p>Body copy about the offer. It also ties into best dfs apps.</p>"
        "<h2>Section Two</h2><p>More body copy for the example.</p>"
        "<h2>Terms</h2><p>States Available: NJ, PA.</p>"
    )
    cleaned = _enforce_secondary_keyword_mentions(html, ["best dfs apps"])
    assert cleaned.lower().count("best dfs apps") == 0
    assert "States Available: NJ, PA." in cleaned
    assert "it also ties into" not in cleaned.lower()
    assert "<p>Main intro for the article.</p>" in cleaned
    assert "<p>Second intro paragraph about the offer.</p>" in cleaned


def test_enforce_secondary_keyword_mentions_adds_clean_missing_coverage_outside_terms():
    html = (
        "<p>The intro explains the promo code, offer amount, featured event, and state availability for readers.</p>"
        "<p>The next paragraph gives practical account setup details before moving into the matchup angle.</p>"
        "<h2>Terms & Conditions</h2><p>Terms language should not receive secondary keyword wording.</p>"
    )
    cleaned = _enforce_secondary_keyword_mentions(html, ["best dfs apps"])
    assert cleaned.lower().count("best dfs apps") == 2
    assert "it also ties into" not in cleaned.lower()
    terms_html = cleaned.split("<h2>Terms & Conditions</h2>", 1)[1].lower()
    assert "best dfs apps" not in terms_html


def test_unwrap_generic_offer_strong_removes_bold_brand_offer_without_touching_code():
    html = "<p>Use the <strong>Underdog offer</strong> tonight with <strong>TOPACTION</strong>.</p>"
    cleaned = _unwrap_generic_offer_strong(html, "Underdog")
    assert "<strong>Underdog offer</strong>" not in cleaned
    assert "Underdog offer" in cleaned
    assert "<strong>TOPACTION</strong>" in cleaned


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
    assert "Available in NJ" in html
    assert "States Available" not in html


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
    assert "If I place" not in html
    assert "clean version" not in html.lower()
    assert "in profit" in html
    assert "separate from the result of the wager" in html


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


def test_render_bet_example_section_deterministic_fallback_uses_offer_qualifying_amount():
    offer = {
        "brand": "bet365",
        "offer_text": "Bet $10, Get $365 in Bonus Bets",
        "bonus_code": "TOPACTION",
        "qualifying_amount": "$10",
        "bonus_amount": "$365",
    }
    html = _render_bet_example_section_deterministic(
        offer=offer,
        bet_example_data=None,
        event_context="Featured game: Chelsea vs Arsenal. Game time: Saturday at 3:00 PM ET.",
    )
    assert html is not None
    assert "qualifying $10 bet" in html
    assert "I place a $50 bet" not in html
    assert "$365 in bonus bets" in html.lower()
    assert "×" not in html


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
    assert "If I use" not in html
    assert "A miss costs" in html
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
    assert "It's available in AL, AK, AR, CA, DC" in html
    assert "States Available" not in html
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
    assert "sign-up link" in html or "registration page" in html or "offer link" in html
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
    assert "The offer is available in TX." in html
    assert "States Available" not in html


@pytest.mark.asyncio
async def test_generate_intro_section_does_not_default_to_today_when_article_date_missing(monkeypatch):
    captured: dict[str, str] = {}

    async def _fake_generate_completion(*, prompt, system_prompt, temperature, max_tokens):
        captured["prompt"] = prompt
        return "<p>bet365 bonus code TOPACTION is tied to Celtics vs. Spurs at 8:00 PM ET on ESPN.</p><p>States Available: NJ, PA.</p>"

    monkeypatch.setattr("app.services.draft.generate_completion", _fake_generate_completion)

    await _generate_intro_section(
        keyword="bet365 bonus code",
        title="bet365 bonus code: Celtics vs. Spurs",
        offer={
            "brand": "bet365",
            "offer_text": "Bet $10, Get $365 in Bonus Bets",
            "bonus_code": "TOPACTION",
            "bonus_amount": "$365",
            "states": ["NJ", "PA"],
        },
        all_offers=None,
        state="NJ",
        talking_points=[],
        event_context="Featured game: Boston Celtics vs San Antonio Spurs. Game time: Friday, March 20 at 8:00 PM ET. Network: ESPN.",
        article_date="",
    )
    assert "ARTICLE DATE: not provided" in captured["prompt"]
    assert "Do not mention today's date" in captured["prompt"]
    assert "DATE (include this)" not in captured["prompt"]


@pytest.mark.asyncio
async def test_generate_intro_section_excludes_alternate_offers_from_prompt(monkeypatch):
    captured: dict[str, str] = {}
    primary_offer = {
        "brand": "bet365",
        "offer_text": "Bet $10, Get $365 in Bonus Bets",
        "bonus_code": "TOPACTION",
        "qualifying_amount": "$10",
        "bonus_amount": "$365",
        "states": ["NJ"],
    }
    alternate_offer = {
        "brand": "BetMGM",
        "offer_text": "Bet $10, Get $150 in Bonus Bets",
        "bonus_code": "MGM150",
    }

    async def _fake_generate_completion(*, prompt, system_prompt, temperature, max_tokens):
        captured["prompt"] = prompt
        return "<p>bet365 bonus code TOPACTION works for Chelsea vs. Arsenal.</p><p>States Available: NJ.</p>"

    monkeypatch.setattr("app.services.draft.generate_completion", _fake_generate_completion)

    await _generate_intro_section(
        keyword="bet365 bonus code",
        title="bet365 bonus code TOPACTION: $365 Bonus for Chelsea vs. Arsenal",
        offer=primary_offer,
        all_offers=[primary_offer, alternate_offer],
        state="NJ",
        talking_points=[],
        event_context="Featured game: Chelsea vs Arsenal. Game time: Thursday, June 18, 2026 at 3:00 PM ET. Network: FOX.",
        article_date="Thursday, June 18, 2026",
    )

    assert "bet365" in captured["prompt"]
    assert "TOPACTION" in captured["prompt"]
    assert "BetMGM" not in captured["prompt"]
    assert "MGM150" not in captured["prompt"]


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


@pytest.mark.asyncio
async def test_generate_body_section_excludes_alternate_offers_from_prompt(monkeypatch):
    captured: dict[str, str] = {}
    primary_offer = {
        "brand": "bet365",
        "offer_text": "Bet $10, Get $365 in Bonus Bets",
        "bonus_code": "TOPACTION",
        "qualifying_amount": "$10",
        "bonus_amount": "$365",
        "states": ["NJ"],
    }
    alternate_offer = {
        "brand": "BetMGM",
        "offer_text": "Bet $10, Get $150 in Bonus Bets",
        "bonus_code": "MGM150",
    }

    async def _fake_query_articles(*args, **kwargs):
        return []

    async def _fake_suggest_links(*args, **kwargs):
        return []

    async def _fake_generate_completion(*, prompt, system_prompt, temperature, max_tokens):
        captured["prompt"] = prompt
        return "<p>The $10 qualifying bet keeps the example aligned with the selected bet365 offer.</p>"

    monkeypatch.setattr("app.services.draft.generate_completion", _fake_generate_completion)
    monkeypatch.setattr("app.services.draft.query_articles", _fake_query_articles)
    monkeypatch.setattr("app.services.draft.suggest_links_for_section", _fake_suggest_links)

    await _generate_body_section(
        section_title="bet365 Bonus Code TOPACTION Details",
        level="h2",
        keyword="bet365 bonus code",
        offer=primary_offer,
        all_offers=[primary_offer, alternate_offer],
        state="NJ",
        offer_property="goal_com",
        talking_points=[],
        avoid=[],
        previous_content="",
        event_context="Featured game: Chelsea vs Arsenal. Game time: Thursday, June 18, 2026 at 3:00 PM ET. Network: FOX.",
    )

    assert "bet365" in captured["prompt"]
    assert "TOPACTION" in captured["prompt"]
    assert "BetMGM" not in captured["prompt"]
    assert "MGM150" not in captured["prompt"]


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
    assert "$5" in html
    assert "fantasy entry" in html or "qualifying contest" in html


def test_build_signup_list_uses_sportsbook_deposit_amount_min_odds_and_bonus_timing():
    html = _build_signup_list(
        brand="bet365",
        has_code=True,
        code_strong="<strong>TOPACTION</strong>",
        state="NJ",
        event_context="Featured game: Chelsea vs Arsenal.",
        signup_url="https://switchboard.actionnetwork.com/offers?x=1",
        qualifying_amount="$10",
        minimum_odds="-200",
        reward_phrase="$365 in bonus bets",
    )
    assert "Deposit at least $10" in html or "Add at least $10" in html or "$10 or more" in html
    assert "at least $10" in html
    assert "-200 minimum odds" in html
    assert "$365 in bonus bets" in html
    assert "after" in html.lower() and "settles" in html.lower()


def test_build_signup_list_uses_bet_and_get_bonus_timing():
    html = _build_signup_list(
        brand="bet365",
        has_code=True,
        code_strong="<strong>GOALBET</strong>",
        state="NJ",
        event_context="Featured game: Chelsea vs Arsenal.",
        qualifying_amount="$10",
        minimum_odds="-500",
        reward_phrase="$365 in Bonus Bets",
        offer_mechanic="bet_and_get",
        variation_key="bet-and-get",
    )

    assert "at least $10" in html
    assert "-500 minimum odds" in html
    assert "Once you place the qualifying wager" in html


def test_build_signup_list_uses_money_back_bonus_timing():
    html = _build_signup_list(
        brand="theScore Bet",
        has_code=True,
        code_strong="<strong>SCORE</strong>",
        state="NJ",
        event_context="Featured game: Rangers vs Devils.",
        qualifying_amount="$10",
        minimum_odds="-200",
        reward_phrase="$1000 in bonus bets",
        offer_mechanic="money_back",
        variation_key="money-back",
    )

    assert "at least $10" in html
    assert "If the first bet loses" in html
    assert "matched with $1000 in bonus bets" in html


def test_select_bc_core_editorial_points_prioritizes_soccer_specific_context():
    points = _select_bc_core_editorial_points(
        {
            "event": {"matched": True, "network": "FOX", "season_name": "2026", "schedule_name": "World Cup"},
            "expertise": {
                "matched": True,
                "editorial_points": [
                    "Weather context points to 72 degrees, 9 mph NW wind, and 15% precipitation.",
                    "Mexico's official lineup lists 11 starters in a 4-3-3.",
                    "The latest listed score was Mexico 2, South Africa 1.",
                    "Mexico has 1 listed player absence: J. Alvarez (Out, Hamstring).",
                ],
            },
        },
        section_kind="overview",
        max_points=3,
    )
    joined = " ".join(points)
    assert "latest listed score" in joined
    assert "official lineup" in joined
    assert "listed player absence" in joined


def test_select_bc_core_editorial_points_prioritizes_market_intelligence():
    points = _select_bc_core_editorial_points(
        {
            "event": {"matched": True},
            "expertise": {
                "matched": True,
                "editorial_points": [
                    "Boston has 2 active BC Core injury listings.",
                    "Jayson Tatum projects for 29.5 points in the selected event.",
                    "DFS lines list Jayson Tatum at 28.5 points, giving fantasy users a concrete prop angle for the slate.",
                    "Market percents show 64% of tickets on Spread tied to Boston Celtics.",
                ],
            },
        },
        section_kind="overview",
        max_points=3,
    )

    joined = " ".join(points)
    assert "projects for 29.5 points" in joined
    assert "DFS lines list" in joined
    assert "Ticket data shows 64%" in joined


def test_select_bc_core_editorial_points_filters_by_content_mode():
    context = {
        "event": {"matched": True},
        "expertise": {
            "matched": True,
            "editorial_points": [
                "Jayson Tatum projects for 29.5 points in the selected event.",
                "DFS lines list Jayson Tatum at 28.5 points, giving fantasy users a concrete prop angle for the slate.",
                "Market percents show 64% of tickets on Spread tied to Boston Celtics.",
                "Boston has covered three of its last four games.",
            ],
        },
    }

    prediction_points = _select_bc_core_editorial_points(
        context,
        section_kind="overview",
        max_points=4,
        prediction_market=True,
    )
    dfs_points = _select_bc_core_editorial_points(
        context,
        section_kind="overview",
        max_points=4,
        dfs_mode=True,
    )

    assert any("projects for 29.5 points" in point for point in prediction_points)
    assert not any("DFS lines list" in point for point in prediction_points)
    assert not any("covered three" in point for point in prediction_points)
    assert any("DFS lines list" in point for point in dfs_points)
    assert not any("Market percents show" in point for point in dfs_points)
    assert not any("covered three" in point for point in dfs_points)


def test_naturalize_bc_core_point_cleans_market_intelligence_labels():
    projection = _naturalize_bc_core_editorial_point(
        "Payton Tolle projects for 6.25 baseball_pitchinghits in the selected event."
    )
    percent = _naturalize_bc_core_editorial_point(
        "Market percents show 83% of ticket on Total tied to Over."
    )

    assert "baseball_pitchinghits" not in projection
    assert "pitching hits allowed" in projection
    assert percent == "Ticket data shows 83% of tickets on Total tied to Over."


def test_build_signup_list_varies_by_mode_and_generation_key():
    pm_html = _build_signup_list(
        brand="Kalshi",
        has_code=True,
        code_strong="<strong>KALSHI</strong>",
        state="CO",
        event_context="Featured market: Celtics vs Spurs.",
        signup_url="https://switchboard.actionnetwork.com/offers?x=1",
        qualifying_amount="$10",
        prediction_market=True,
        variation_key="pm-run",
    )
    sportsbook_html = _build_signup_list(
        brand="bet365",
        has_code=True,
        code_strong="<strong>ACTION365</strong>",
        state="NJ",
        event_context="Featured game: Celtics vs Spurs.",
        signup_url="https://switchboard.actionnetwork.com/offers?x=1",
        qualifying_amount="$10",
        variation_key="sportsbook-run",
    )

    assert pm_html != sportsbook_html
    assert "position" in pm_html.lower() or "contract" in pm_html.lower()
    assert "bet" in sportsbook_html.lower() or "wager" in sportsbook_html.lower()
    assert "qualifying bet" not in pm_html.lower()


@pytest.mark.asyncio
async def test_generate_draft_shortcodes_keep_primary_offer_when_alt_offers_exist(monkeypatch):
    async def _identity_humanizer(html, **kwargs):
        return html

    monkeypatch.setattr("app.services.draft._humanize_article_html", _identity_humanizer)

    html = await generate_draft_from_outline(
        outline=[
            {"level": "shortcode", "title": "", "talking_points": [], "avoid": []},
            {"level": "shortcode_1", "title": "", "talking_points": [], "avoid": []},
        ],
        keyword="bet365 bonus code",
        title="bet365 bonus code test",
        offer={
            "brand": "bet365",
            "offer_text": "Bet $10, Get $365 in Bonus Bets",
            "bonus_code": "TOPACTION",
            "shortcode": '[bam-inline-promotion placement-id="2037" property-id="1" context="web-article-top-stories" internal-id="evergreen" affiliate-type="sportsbook" affiliate="bet365"]',
        },
        alt_offers=[
            {
                "brand": "BetMGM",
                "offer_text": "Bet $10, Get $200 in Bonus Bets",
                "bonus_code": "MGM",
                "shortcode": '[bam-inline-promotion placement-id="2037" property-id="1" context="web-article-top-stories" internal-id="evergreen" affiliate-type="sportsbook" affiliate="BetMGM"]',
            }
        ],
        state="NJ",
        offer_property="action_network",
    )
    assert html.count('affiliate="bet365"') == 1
    assert html.count('affiliate="BetMGM"') == 1


@pytest.mark.asyncio
async def test_generate_draft_does_not_duplicate_single_selected_bam_unit(monkeypatch):
    async def _identity_humanizer(html, **kwargs):
        return html

    monkeypatch.setattr("app.services.draft._humanize_article_html", _identity_humanizer)

    html = await generate_draft_from_outline(
        outline=[
            {"level": "shortcode", "title": "", "talking_points": [], "avoid": []},
            {"level": "shortcode_1", "title": "", "talking_points": [], "avoid": []},
            {"level": "shortcode_2", "title": "", "talking_points": [], "avoid": []},
        ],
        keyword="bet365 bonus code",
        title="bet365 bonus code test",
        offer={
            "brand": "bet365",
            "offer_text": "Bet $10, Get $365 in Bonus Bets",
            "bonus_code": "TOPACTION",
            "shortcode": '[bam-inline-promotion placement-id="2037" property-id="1" context="web-article-top-stories" internal-id="evergreen" affiliate-type="sportsbook" affiliate="bet365"]',
        },
        alt_offers=[],
        state="NJ",
        offer_property="action_network",
    )
    assert html.count("[bam-inline-promotion") == 1
    assert html.count('affiliate="bet365"') == 1


@pytest.mark.asyncio
async def test_generate_draft_tolerates_missing_primary_offer(monkeypatch):
    async def _identity_humanizer(html, **kwargs):
        return html

    monkeypatch.setattr("app.services.draft._humanize_article_html", _identity_humanizer)

    html = await generate_draft_from_outline(
        outline=[
            {"level": "shortcode", "title": "", "talking_points": [], "avoid": []},
        ],
        keyword="bet365 bonus code",
        title="bet365 bonus code test",
        offer=None,
        alt_offers=[],
        state="NJ",
        offer_property="goal_com",
    )

    assert "<h1>bet365 Bonus Code Test</h1>" in html
    assert "view_top_story" in html


def test_enforce_primary_keyword_density_adds_plain_text_mentions_without_ctas():
    html = (
        '<p><a href="https://example.com">bet365 bonus code</a> starts the article.</p>'
        "<p>The offer details are clear for the matchup.</p>"
        "<p>The signup flow uses the selected operator.</p>"
        "<p>The example keeps the stake aligned with the offer.</p>"
        "<p>Terms apply. 21+.</p>"
    )

    cleaned = _enforce_primary_keyword_density(html, "bet365 bonus code")

    assert _count_keyword(cleaned, "bet365 bonus code") >= 5
    assert cleaned.count("href=") == 1


def test_body_word_count_excludes_signup_terms_shortcodes_and_disclaimers():
    html = (
        "<h1>Title</h1>"
        "<p>Intro body words count here for the editorial target.</p>"
        '[bam-inline-promotion placement-id="2066" property-id="326" affiliate="bet365"]'
        "<h2>How to Claim bet365 Bonus Code</h2>"
        "<ol><li>Sign up step one has many many words that should not count.</li></ol>"
        "<h2>Terms and Conditions</h2>"
        "<p>Terms apply. 21+. Minimum odds -500 or greater.</p>"
        "<p><em>21+. Gambling problem? Call 1-800-GAMBLER.</em></p>"
    )

    assert _body_word_count_for_editorial_target(html) == 9


@pytest.mark.asyncio
async def test_ensure_editorial_body_length_adds_useful_section_before_terms():
    html = (
        "<h1>bet365 bonus code test</h1>"
        "<p>Short intro for the selected offer.</p>"
        "<h2>bet365 Bonus Code Details</h2>"
        "<p>The selected offer requires a $10 qualifying wager.</p>"
        "<h2>bet365 Bonus Code Terms</h2>"
        "<p>Terms apply. 21+.</p>"
    )

    expanded = await _ensure_editorial_body_length(
        html,
        keyword="bet365 bonus code",
        offer={
            "brand": "bet365",
            "offer_text": "Bet $10, Get $365 in Bonus Bets",
            "qualifying_amount": "$10",
            "bonus_amount": "$365",
            "minimum_odds": "-500",
        },
        event_context="Featured game: Chelsea vs Arsenal.",
        target_words=120,
    )

    assert "What to Watch Before Using bet365" in expanded
    assert expanded.index("What to Watch Before Using bet365") < expanded.index("bet365 Bonus Code Terms")
    assert "filler" not in expanded.lower()
    assert "payout math" not in expanded.lower()
    assert _body_word_count_for_editorial_target(expanded) > _body_word_count_for_editorial_target(html)


@pytest.mark.asyncio
async def test_ensure_editorial_body_length_uses_keyword_brand_when_offer_missing():
    html = (
        "<h1>draftkings promo code test</h1>"
        "<p>Short intro for the selected offer.</p>"
        "<h2>DraftKings Promo Code Details</h2>"
        "<p>The selected offer still needs a useful event explanation.</p>"
        "<h2>DraftKings Promo Code Terms</h2>"
        "<p>Terms apply. 21+.</p>"
    )

    expanded = await _ensure_editorial_body_length(
        html,
        keyword="draftkings promo code",
        offer={},
        event_context="Featured game: Nationals vs Red Sox.",
        target_words=120,
    )

    assert "What to Watch Before Using DraftKings" in expanded
    assert "What to Watch Before Using the operator" not in expanded
    assert "rules., so" not in expanded
    assert "The best example is the bet you were already comfortable making." in expanded


@pytest.mark.asyncio
async def test_ensure_editorial_body_length_builds_numbers_section_with_play_close(monkeypatch):
    async def _refuse_narrative(**kwargs):
        return "not valid html"

    monkeypatch.setattr("app.services.draft.generate_completion", _refuse_narrative)

    html = (
        "<h1>bet365 bonus code test</h1>"
        "<p>Short intro for the selected offer.</p>"
        "<h2>bet365 Bonus Code Details</h2>"
        "<p>The selected offer requires a $10 qualifying wager.</p>"
        "<h2>bet365 Bonus Code Terms</h2>"
        "<p>Terms apply. 21+.</p>"
    )
    bc_core_context = {
        "expertise": {
            "matched": True,
            "editorial_points": [
                "Boston enter at 44-43 overall this season.",
                "Washington went 6-4 against the spread over the last 10 games.",
                "Payton Tolle projects for 6.25 pitching hits allowed.",
            ],
        },
        "event": {"matched": False},
    }

    expanded = await _ensure_editorial_body_length(
        html,
        keyword="bet365 bonus code",
        offer={
            "brand": "bet365",
            "offer_text": "Bet $10, Get $365 in Bonus Bets",
            "qualifying_amount": "$10",
            "bonus_amount": "$365",
        },
        event_context="Featured game: Nationals vs Red Sox.",
        bc_core_context=bc_core_context,
        bet_example_data={"bet_amount": 10, "selection": "Boston Red Sox ML", "odds": -140},
        target_words=120,
    )

    assert "What the Numbers Say About Nationals vs Red Sox" in expanded
    assert "Payton Tolle projects for 6.25 pitching hits allowed." in expanded
    assert "The play: use the qualifying bet on Boston Red Sox ML at -140" in expanded
    # Editor/writer-facing meta guidance must never ship in published copy.
    assert "gives editors" not in expanded
    assert "keeps the example useful for readers" not in expanded
    assert "give the article" not in expanded


@pytest.mark.asyncio
async def test_ensure_editorial_body_length_uses_narrative_composition_when_valid(monkeypatch):
    narrative = (
        "<p>Boston walk in at 44-43 and everything about this spot says the margin for error is gone. "
        "A team sitting one game over even in July is not coasting; it is fighting for its season every night, "
        "and that urgency is exactly what this matchup demands.</p>"
        "<p>The problem is the opposition's form. Washington have covered in six of their last ten, a 6-4 run "
        "against the spread that reads like a team playing better than its record. Payton Tolle projects for "
        "6.25 pitching hits allowed, and that is the profile of a starter who keeps traffic off the bases and "
        "keeps his side in front. For a first bet with bet365, the $10 qualifying wager fits a straightforward "
        "market here, with $365 in bonus bets to follow.</p>"
        "<p>The play: back Boston Red Sox ML at -140 with the qualifying bet, then keep the bonus bets for later "
        "eligible markets once they post.</p>"
    )

    async def _compose(**kwargs):
        return narrative

    monkeypatch.setattr("app.services.draft.generate_completion", _compose)

    html = (
        "<h1>bet365 bonus code test</h1>"
        "<p>Short intro for the selected offer.</p>"
        "<h2>bet365 Bonus Code Details</h2>"
        "<p>The selected offer requires a $10 qualifying wager.</p>"
        "<h2>bet365 Bonus Code Terms</h2>"
        "<p>Terms apply. 21+.</p>"
    )
    bc_core_context = {
        "expertise": {
            "matched": True,
            "editorial_points": [
                "Boston enter at 44-43 overall this season.",
                "Washington went 6-4 against the spread over the last 10 games.",
                "Payton Tolle projects for 6.25 pitching hits allowed.",
            ],
        },
        "event": {"matched": False},
    }

    expanded = await _ensure_editorial_body_length(
        html,
        keyword="bet365 bonus code",
        offer={
            "brand": "bet365",
            "offer_text": "Bet $10, Get $365 in Bonus Bets",
            "qualifying_amount": "$10",
            "bonus_amount": "$365",
        },
        event_context="Featured game: Nationals vs Red Sox.",
        bc_core_context=bc_core_context,
        bet_example_data={"bet_amount": 10, "selection": "Boston Red Sox ML", "odds": -140},
        target_words=120,
    )

    assert "What the Numbers Say About Nationals vs Red Sox" in expanded
    assert "everything about this spot says the margin for error is gone" in expanded
    assert "The play: back Boston Red Sox ML at -140" in expanded
    # The deterministic fallback's stock sentence must not appear when the narrative is used.
    assert "That is the backdrop for the first bet" not in expanded


@pytest.mark.asyncio
async def test_ensure_matchup_analysis_section_renders_even_when_body_is_long(monkeypatch):
    narrative = (
        "<p>Boston walk in at 44-43 and everything about this spot says the margin for error is gone. "
        "A team sitting one game over even in July is not coasting; it is fighting for its season every night, "
        "and that urgency is exactly what this matchup demands.</p>"
        "<p>Washington have covered in six of their last ten, a 6-4 run against the spread that reads like a team "
        "playing better than its record. Payton Tolle projects for 6.25 pitching hits allowed, the profile of a "
        "starter who keeps traffic off the bases. For a first bet with bet365, the $10 qualifying wager fits a "
        "straightforward market here, with $365 in bonus bets to follow.</p>"
        "<p>The play: back Boston Red Sox ML at -140 with the qualifying bet, then keep the bonus bets for later "
        "eligible markets once they post.</p>"
    )

    async def _compose(**kwargs):
        return narrative

    monkeypatch.setattr("app.services.draft.generate_completion", _compose)

    long_body = "".join(f"<p>Editorial paragraph {i} about the matchup and the offer in depth.</p>" for i in range(60))
    html = (
        "<h1>bet365 bonus code test</h1>"
        + long_body
        + "<h2>bet365 Bonus Code Terms</h2><p>Terms apply. 21+.</p>"
    )
    bc_core_context = {
        "expertise": {
            "matched": True,
            "editorial_points": [
                "Boston enter at 44-43 overall this season.",
                "Washington went 6-4 against the spread over the last 10 games.",
                "Payton Tolle projects for 6.25 pitching hits allowed.",
            ],
        },
        "event": {"matched": False},
    }

    expanded = await _ensure_matchup_analysis_section(
        html,
        keyword="bet365 bonus code",
        offer={"brand": "bet365", "qualifying_amount": "$10", "bonus_amount": "$365", "offer_text": "Bet $10, Get $365"},
        event_context="Featured game: Nationals vs Red Sox.",
        bc_core_context=bc_core_context,
        bet_example_data={"bet_amount": 10, "selection": "Boston Red Sox ML", "odds": -140},
    )

    assert "What the Numbers Say About Nationals vs Red Sox" in expanded
    assert expanded.index("What the Numbers Say") < expanded.index("bet365 Bonus Code Terms")

    # Prediction-market mode never gets the sportsbook analysis section.
    pm_result = await _ensure_matchup_analysis_section(
        html,
        keyword="kalshi promo code",
        offer={"brand": "Kalshi"},
        event_context="Featured game: Nationals vs Red Sox.",
        bc_core_context=bc_core_context,
        content_mode="prediction_market",
    )
    assert "What the Numbers Say" not in pm_result


@pytest.mark.asyncio
async def test_ensure_editorial_body_length_rejects_narrative_with_invented_numbers(monkeypatch):
    async def _hallucinate(**kwargs):
        return (
            "<p>Boston are 44-43 and have won 12 of 15 at home, a stretch that includes a 9-2 rout. "
            "Washington went 6-4 against the spread over the last 10 games, but the deeper numbers all "
            "point one way in this matchup, and the value follows the form line here as well tonight.</p>"
            "<p>Payton Tolle projects for 6.25 pitching hits allowed, which keeps the game script stable "
            "and the favorite in control for most of the evening in this spot.</p>"
            "<p>The play: back Boston Red Sox ML at -140 with the qualifying bet.</p>"
        )

    monkeypatch.setattr("app.services.draft.generate_completion", _hallucinate)

    html = (
        "<h1>bet365 bonus code test</h1>"
        "<p>Short intro for the selected offer.</p>"
        "<h2>bet365 Bonus Code Terms</h2>"
        "<p>Terms apply. 21+.</p>"
    )
    bc_core_context = {
        "expertise": {
            "matched": True,
            "editorial_points": [
                "Boston enter at 44-43 overall this season.",
                "Washington went 6-4 against the spread over the last 10 games.",
                "Payton Tolle projects for 6.25 pitching hits allowed.",
            ],
        },
        "event": {"matched": False},
    }

    expanded = await _ensure_editorial_body_length(
        html,
        keyword="bet365 bonus code",
        offer={"brand": "bet365", "qualifying_amount": "$10", "bonus_amount": "$365", "offer_text": "Bet $10, Get $365"},
        event_context="Featured game: Nationals vs Red Sox.",
        bc_core_context=bc_core_context,
        bet_example_data={"bet_amount": 10, "selection": "Boston Red Sox ML", "odds": -140},
        target_words=120,
    )

    # Invented "12 of 15" / "9-2" figures must force the deterministic fallback.
    assert "12 of 15" not in expanded
    assert "That is the backdrop for the first bet" in expanded


def test_render_terms_section_fallback_is_reader_facing():
    html = _render_terms_section_html(
        offers=[{"brand": "DraftKings"}],
        terms="",
        expiration_days=None,
        min_odds="",
        wagering="",
    )
    assert "not provided in source data" not in html
    assert "See the operator's app or site" in html


def test_normalize_brand_casing_fixes_visible_copy_and_headings():
    html = (
        "<h1>draftkings promo code: Nationals vs. Red Sox</h1>"
        "<h2>What to Watch Before Using Draftkings</h2>"
        '<p>Use <strong>draftkings promo code</strong> at signup. '
        '<a href="https://sportsbook.draftkings.com/x">Claim at draftkings</a></p>'
    )
    fixed = _normalize_brand_casing(html, "DraftKings")
    assert "<h1>DraftKings promo code: Nationals vs. Red Sox</h1>" in fixed
    assert "What to Watch Before Using DraftKings" in fixed
    assert "<strong>DraftKings promo code</strong>" in fixed
    assert 'href="https://sportsbook.draftkings.com/x"' in fixed
    assert "Claim at DraftKings" in fixed


def test_normalize_brand_casing_keeps_bet365_lowercase_and_skips_unknown_lowercase():
    html = "<p>Bet365 bonus code works for Celtics vs. Spurs tonight.</p>"
    fixed = _normalize_brand_casing(html, "bet365")
    assert "bet365 bonus code works" in fixed

    unknown = "<p>Use Novig promo code today.</p>"
    assert _normalize_brand_casing(unknown, "novig") == unknown


def test_convert_availability_labels_to_prose_handles_lists_and_generic():
    html = (
        "<p>States Available: NJ, PA.</p>"
        "<p>Provinces Available: AB, BC.</p>"
        "<p>States Available: eligible states listed by the operator.</p>"
    )
    fixed = _convert_availability_labels_to_prose(html)
    assert "The offer is available in NJ, PA." in fixed
    assert "The offer is available in AB, BC." in fixed
    assert "Availability varies by state, so confirm eligibility during signup." in fixed
    assert "Available:" not in fixed


def test_convert_availability_labels_to_prose_rewrites_generic_prose_fallback():
    html = "<p>It's available in eligible states listed by the operator.</p>"
    fixed = _convert_availability_labels_to_prose(html)
    assert "eligible states listed by the operator" not in fixed
    assert "Availability varies by state, so confirm eligibility during signup." in fixed


def test_title_case_headings_preserves_brands_acronyms_and_small_words():
    html = (
        "<h1>draftkings promo code: nationals vs. red sox MLB offer</h1>"
        "<h2>What the Numbers Say About Nationals vs Red Sox</h2>"
        "<h2>bet365 bonus code terms</h2>"
        "<p>body text stays lowercase</p>"
    )
    fixed = _title_case_headings(html)
    assert "<h1>Draftkings Promo Code: Nationals vs. Red Sox MLB Offer</h1>" in fixed
    assert "<h2>What the Numbers Say About Nationals vs Red Sox</h2>" in fixed
    assert "<h2>bet365 Bonus Code Terms</h2>" in fixed
    assert "body text stays lowercase" in fixed


def test_decapitalize_inline_reward_mentions_spares_terms_and_headings():
    html = (
        "<h2>DraftKings Promo Code Bonus Bets</h2>"
        "<p>DraftKings drops the Bet $5, Get $200 in Bonus Bets Instantly reward.</p>"
        "<h2>DraftKings Promo Code Terms</h2>"
        "<p>Max. $200 issued as non-withdrawable Bonus Bets that expire in 7 days.</p>"
    )
    fixed = _decapitalize_inline_reward_mentions(html)
    assert "in bonus bets instantly reward" in fixed
    assert "<h2>DraftKings Promo Code Bonus Bets</h2>" in fixed
    assert "non-withdrawable Bonus Bets" in fixed


def test_offer_reward_phrase_visible_decapitalizes_marketing_but_keeps_branded_labels():
    shouty = _offer_reward_phrase_visible(
        {"brand": "DraftKings", "bonus_amount": "$200", "reward_label": "Bonus Bets Instantly"}
    )
    assert shouty == "$200 in bonus bets instantly"

    branded = _offer_reward_phrase_visible(
        {"brand": "Novig", "bonus_amount": "$50", "reward_label": "Novig Coins"}
    )
    assert branded == "$50 in Novig Coins"


def test_cap_primary_keyword_density_reduces_late_plain_mentions():
    html = "".join(f"<p>bet365 bonus code mention {idx}.</p>" for idx in range(11))
    capped = _cap_primary_keyword_density(html, "bet365 bonus code", max_count=9)

    assert _count_keyword(capped, "bet365 bonus code") == 9
    assert "bet365 mention 10" in capped


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


def test_trim_dangling_paragraph_endings_removes_trailing_remember_fragment():
    html = '<p>For a step-by-step walkthrough, use this <strong>bet365 bonus code</strong> guide, and remember</p><h2>How to Claim</h2>'
    cleaned = _trim_dangling_paragraph_endings(html)
    assert "and remember" not in cleaned
    assert cleaned.startswith('<p>For a step-by-step walkthrough, use this <strong>bet365 bonus code</strong> guide.</p>')


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
