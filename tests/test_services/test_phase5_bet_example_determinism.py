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
    assert "$270.00" not in html
    assert "matched to that losing stake" in html
    assert "not a guaranteed payout" in html
    assert "TOPACTION" in html
    assert "Atlanta Hawks vs Charlotte Hornets" in html


@pytest.mark.asyncio
async def test_generate_body_section_claim_uses_deterministic_exact_bet_mechanics(monkeypatch):
    prompts: list[str] = []

    async def _fake_query_articles(*args, **kwargs):
        return []

    async def _fake_suggest_links(*args, **kwargs):
        return []

    async def _fake_generate_completion(*, prompt, system_prompt, temperature, max_tokens):
        prompts.append(prompt)
        return "<p>This should not be used.</p>"

    monkeypatch.setattr(draft_mod, "query_articles", _fake_query_articles)
    monkeypatch.setattr(draft_mod, "suggest_links_for_section", _fake_suggest_links)
    monkeypatch.setattr(draft_mod, "generate_completion", _fake_generate_completion)

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

    assert prompts == []
    assert "Atlanta Hawks ML" in content
    assert "+170" in content
    assert "$270.00" not in content
    assert "guaranteed payout" in content or "matched to that losing stake" in content
    assert "[another pick]" not in content


def test_render_bet_example_section_deterministic_bet_and_get_uses_qualifier_not_big_example():
    html = _render_bet_example_section_deterministic(
        offer={
            "brand": "theScore Bet",
            "offer_text": "Bet $10, Get $1000 in Bonus Bets",
            "bonus_code": "SCORE1000",
            "qualifying_amount": "$10",
            "bonus_amount": "$1000",
        },
        bet_example_data=None,
        event_context="Featured game: Toronto Maple Leafs vs Boston Bruins.",
    )

    assert html is not None
    assert "$10" in html
    assert "$50" not in html
    assert "$1000 payout" not in html.lower()
    assert "separate from the result of the wager" in html


@pytest.mark.asyncio
async def test_generate_body_section_surfaces_bc_core_fact_when_initial_copy_ignores_it(monkeypatch):
    prompts: list[str] = []

    async def _fake_query_articles(*args, **kwargs):
        return []

    async def _fake_suggest_links(*args, **kwargs):
        return []

    async def _fake_generate_completion(*, prompt, system_prompt, temperature, max_tokens):
        prompts.append(prompt)
        if len(prompts) == 1:
            return "<p>This offer gives you a straightforward way to get extra value on the game.</p><p>Use the promo and keep the first wager simple.</p>"
        return "<p>This offer gives you a straightforward way to get extra value on the game.</p><p>San Antonio has gone 8-2 against the spread over the last 10 games, and warm weather around 79 degrees with light wind from right adds another layer to the setup.</p>"

    monkeypatch.setattr(draft_mod, "query_articles", _fake_query_articles)
    monkeypatch.setattr(draft_mod, "suggest_links_for_section", _fake_suggest_links)
    monkeypatch.setattr(draft_mod, "generate_completion", _fake_generate_completion)

    content = await _generate_body_section(
        section_title="Why bet365 bonus code is worth a look for Spurs vs. Thunder",
        level="h2",
        keyword="bet365 bonus code",
        offer={
            "brand": "bet365",
            "offer_text": "Bet $10, Get $200 in Bonus Bets Win or Lose!",
            "bonus_code": "TOPACTION",
            "terms": "",
        },
        all_offers=None,
        state="NJ",
        offer_property="action_network",
        talking_points=[],
        avoid=[],
        previous_content="",
        current_keyword_count=1,
        target_keyword_total=6,
        event_context="Featured game: San Antonio Spurs vs Oklahoma City Thunder. Game time: Wednesday, May 20, 2026 at 8:30 PM ET. Network: NBC/Peacock.",
        prediction_market=False,
        dfs_mode=False,
        bc_core_context={
            "event": {"matched": True, "network": "NBC/Peacock", "season_name": "2025-26", "schedule_name": "Playoffs"},
            "expertise": {
                "matched": True,
                "editorial_points": [
                    "San Antonio Spurs is 8-2 ATS in BC Core's Last10 Overall trend sample.",
                    "BC Core weather at the venue points to 79.45° conditions; 7.18 mph FromRight wind; 0% precipitation chances; partly cloudy.",
                    "San Antonio Spurs averaged 117.33 points per game with a +13.83 scoring margin.",
                ],
            },
        },
    )

    assert len(prompts) == 2
    assert "INTERNAL EXPERTISE NOTES" in prompts[0]
    assert "San Antonio Spurs has gone 8-2 against the spread over the last 10 games." in prompts[0]
    assert "MANDATORY CORRECTION" in prompts[1]
    assert "8-2 against the spread" in content
    assert "79 degrees" in content
    assert "wind from right" in content.lower()
    assert "BC Core" not in content
