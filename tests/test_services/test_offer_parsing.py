"""Tests for offer parsing helpers."""

from app.services.offer_parsing import (
    enrich_offer_dict,
    extract_bonus_amount,
    extract_offer_amount_details,
    extract_states_from_terms,
    parse_states,
)


def test_parse_states_filters_unknown_and_normalizes_variants():
    states = parse_states(["SN", "IN", "AZ", "D.C.", "New York"])
    assert states == ["IN", "AZ", "DC", "NY"]


def test_extract_states_from_terms_uses_available_in_segment():
    terms = (
        "New players only, 21+. Available in AZ, CO, CT, DC, IL, IN, IA, KS, KY, "
        "LA, MA, MD, MI, MO, NC, NJ, NY, OH, PA, TN, VA, VT, WV, WY only."
    )
    states = extract_states_from_terms(terms)
    assert states == [
        "AZ", "CO", "CT", "DC", "IL", "IN", "IA", "KS", "KY",
        "LA", "MA", "MD", "MI", "MO", "NC", "NJ", "NY", "OH",
        "PA", "TN", "VA", "VT", "WV", "WY",
    ]


def test_extract_states_from_terms_ignores_negative_only_availability():
    terms = "US Promotional Offers Not Available in MS, NY, ON, or PR."
    assert extract_states_from_terms(terms) == []


def test_extract_bonus_amount_prefers_reward_amount_for_spend_get_offers():
    offer_text = "Novig promo code ACTION: Spend $25, Get $50 in Novig Coins"
    assert extract_bonus_amount(offer_text) == "$50"


def test_extract_offer_amount_details_parses_novig_spend_get_pattern():
    details = extract_offer_amount_details("Spend $25, Get $50 in Novig Coins")
    assert details["qualifying_action"] == "spend"
    assert details["qualifying_amount"] == "$25"
    assert details["reward_amount"] == "$50"
    assert details["reward_label"] == "Novig Coins"


def test_enrich_offer_dict_keeps_reward_and_qualifying_amounts_separate():
    enriched = enrich_offer_dict(
        {
            "brand": "Novig",
            "offer_text": "Make a $25 purchase to unlock $50 in Novig Coins",
            "terms": "",
        }
    )
    assert enriched["bonus_amount"] == "$50"
    assert enriched["qualifying_amount"] == "$25"
    assert enriched["reward_amount"] == "$50"
    assert enriched["reward_label"] == "Novig Coins"
