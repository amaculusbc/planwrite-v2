"""Phase 3 tests for source-of-truth offer formatting fidelity."""

from app.services.draft import _format_offer_for_prompt


def test_format_offer_for_prompt_preserves_novig_spend_get_mechanics():
    offer = {
        "brand": "Novig",
        "offer_text": "Spend $25, Get $50 in Novig Coins",
        "bonus_code": "ACTION",
        "terms": "",
        "states_list": ["AZ", "IN"],
    }
    row = _format_offer_for_prompt(offer, state="ALL", prediction_market=True)
    assert "Bonus Amount: $50 (Novig Coins)" in row
    assert "Qualifying Action: Spend $25 to unlock $50 in Novig Coins" in row
    assert "Credit Expiration:" in row
    assert "Bonus Amount: $25" not in row

