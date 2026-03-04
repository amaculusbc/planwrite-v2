"""Phase 5 tests for deterministic sportsbook worked-example rendering."""

import pytest

import app.services.draft as draft_mod
from app.services.draft import _generate_body_section, _render_bet_example_section_deterministic


def test_render_bet_example_section_deterministic_uses_selected_values():
    html = _render_bet_example_section_deterministic(
        offer={
            "brand": "BetMGM",
            "offer_text": "Get up to $1,500 in bonus bets if your first bet loses",
            "bonus_code": "TOPACTION",
        },
        bet_example_data={
            "bet_amount": 100,
            "selection": "Atlanta Hawks ML",
            "odds": 170,
            "potential_profit": 170.0,
            "sportsbook_used": "betmgm",
        },
        event_context="Featured game: Atlanta Hawks vs Charlotte Hornets. Game time: Friday, February 13 at 8:00 PM ET.",
    )
    assert html is not None
    assert "BetMGM" in html
    assert "$100" in html
    assert "Atlanta Hawks ML" in html
    assert "+170" in html
    assert "$170.00" in html
    assert "$270.00" in html
    assert "TOPACTION" in html
    assert "Atlanta Hawks vs Charlotte Hornets" in html


@pytest.mark.asyncio
async def test_generate_body_section_claim_uses_deterministic_bet_example(monkeypatch):
    async def _fake_query_articles(*args, **kwargs):
        return []

    async def _fake_suggest_links(*args, **kwargs):
        return []

    monkeypatch.setattr(draft_mod, "query_articles", _fake_query_articles)
    monkeypatch.setattr(draft_mod, "suggest_links_for_section", _fake_suggest_links)

    content = await _generate_body_section(
        section_title="How to Claim BetMGM bonus code for Hawks vs. Hornets",
        level="h2",
        keyword="BetMGM bonus code",
        offer={
            "brand": "BetMGM",
            "offer_text": "Get up to $1,500 in bonus bets if your first bet loses",
            "bonus_code": "TOPACTION",
            "terms": "",
        },
        all_offers=None,
        state="NC",
        offer_property="action_network",
        talking_points=[],
        avoid=[],
        previous_content="",
        current_keyword_count=1,
        target_keyword_total=6,
        event_context="Featured game: Atlanta Hawks vs Charlotte Hornets.",
        bet_example="suppose ...",  # legacy text still present
        bet_example_data={
            "bet_amount": 100,
            "selection": "Atlanta Hawks ML",
            "odds": 170,
            "potential_profit": 170.0,
            "sportsbook_used": "betmgm",
        },
        prediction_market=False,
        dfs_mode=False,
    )

    assert "Atlanta Hawks ML" in content
    assert "+170" in content
    assert "$170.00" in content
    assert "$270.00" in content
    # deterministic path should avoid placeholder wording from LLM prompt examples
    assert "[another pick]" not in content

