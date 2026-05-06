"""Phase 8 tests for state-specific offer prioritization."""

import pytest

from app.services import bam_offers


@pytest.mark.asyncio
async def test_get_offers_bam_prefers_exact_state_match(monkeypatch):
    async def fake_fetch_offers_from_bam(*args, **kwargs):
        return [
            {"brand": "bet365", "offer_text": "All states offer", "states": ["ALL"]},
            {"brand": "bet365", "offer_text": "NJ only offer", "states": ["NJ"]},
            {"brand": "bet365", "offer_text": "PA only offer", "states": ["PA"]},
        ]

    monkeypatch.setattr(bam_offers, "fetch_offers_from_bam", fake_fetch_offers_from_bam)

    offers = await bam_offers.get_offers_bam(state="NJ", brand="bet365")

    assert [offer["offer_text"] for offer in offers] == [
        "NJ only offer",
        "All states offer",
    ]


@pytest.mark.asyncio
async def test_get_offers_bam_filters_all_state_offers_that_terms_exclude(monkeypatch):
    async def fake_fetch_offers_from_bam(*args, **kwargs):
        return [
            {
                "brand": "bet365",
                "offer_text": "Bet $10 Get $365 Win or Lose!",
                "states": ["ALL"],
                "terms": "Deposit required. Michigan Only.",
            },
            {
                "brand": "bet365",
                "offer_text": "Bet $10, Get $200 in Bonus Bets Win or Lose!",
                "states": ["ALL"],
                "terms": "Deposit required. Not available in Illinois.",
            },
        ]

    monkeypatch.setattr(bam_offers, "fetch_offers_from_bam", fake_fetch_offers_from_bam)

    offers = await bam_offers.get_offers_bam(state="PA", brand="bet365")

    assert [offer["offer_text"] for offer in offers] == [
        "Bet $10, Get $200 in Bonus Bets Win or Lose!",
    ]


@pytest.mark.asyncio
async def test_get_offers_bam_prefers_direct_bonus_offer_over_safety_net_for_generic_state(monkeypatch):
    async def fake_fetch_offers_from_bam(*args, **kwargs):
        return [
            {
                "brand": "bet365",
                "offer_text": "Get a First Bet Safety Net up to $1,000 in Bonus Bets!",
                "states": ["ALL"],
                "terms": "",
                "reward_amount": "$1000",
            },
            {
                "brand": "bet365",
                "offer_text": "Bet $10, Get $200 in Bonus Bets Win or Lose!",
                "states": ["ALL"],
                "terms": "Not available in Illinois.",
                "reward_amount": "$200",
            },
            {
                "brand": "bet365",
                "offer_text": "Bet $10 Get $50 in Bonus Bets!",
                "states": ["ALL"],
                "terms": "",
                "reward_amount": "$50",
            },
        ]

    monkeypatch.setattr(bam_offers, "fetch_offers_from_bam", fake_fetch_offers_from_bam)

    offers = await bam_offers.get_offers_bam(state="NJ", brand="bet365")

    assert [offer["offer_text"] for offer in offers] == [
        "Bet $10, Get $200 in Bonus Bets Win or Lose!",
        "Bet $10 Get $50 in Bonus Bets!",
        "Get a First Bet Safety Net up to $1,000 in Bonus Bets!",
    ]
