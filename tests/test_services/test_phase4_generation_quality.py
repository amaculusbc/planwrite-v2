"""Phase 4 tests for deterministic generation quality post-processing."""

from app.services.draft import (
    _apply_generation_quality_postprocess,
    _ensure_keyword_in_first_paragraph,
    _normalize_matchup_vs_notation,
    _soften_repetitive_intro_opener,
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

