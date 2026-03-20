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
