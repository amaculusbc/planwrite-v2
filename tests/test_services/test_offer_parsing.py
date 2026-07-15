"""Tests for offer parsing helpers."""

from app.services.offer_parsing import (
    enrich_offer_dict,
    extract_bonus_amount,
    extract_excluded_states_from_terms,
    extract_minimum_age,
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


def test_extract_states_from_terms_handles_washington_dc_without_wa():
    terms = "Must be physically present in AZ, CO, NJ, PA, or Washington, DC."
    assert extract_states_from_terms(terms) == ["AZ", "CO", "NJ", "PA", "DC"]


def test_extract_excluded_states_from_terms_parses_not_available_segment():
    terms = "Deposit required. Paid in Bonus Bets. Not available in Illinois."
    assert extract_excluded_states_from_terms(terms) == ["IL"]


def test_enrich_offer_dict_prefers_term_derived_states_over_dirty_payload_states():
    enriched = enrich_offer_dict(
        {
            "offer_text": "Sample Offer",
            "terms": "Available in AZ, CO, NJ, PA, or Washington, DC only.",
            "states_list": ["AZ", "CO", "NJ", "PA", "DC", "WA"],
        }
    )
    assert enriched["states_list"] == ["AZ", "CO", "NJ", "PA", "DC"]


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


def test_extract_offer_amount_details_parses_prediction_market_trade_pattern():
    details = extract_offer_amount_details("Trade $10, Get $15")
    assert details["qualifying_action"] == "trade"
    assert details["qualifying_amount"] == "$10"
    assert details["reward_amount"] == "$15"


def test_enrich_offer_dict_reports_kalshi_reward_not_qualifying_trade():
    enriched = enrich_offer_dict(
        {
            "brand": "Kalshi",
            "offer_text": "Kalshi promo code ACTION: Trade $10, Get $15",
            "terms": "",
        }
    )
    assert enriched["bonus_amount"] == "$15"
    assert enriched["qualifying_amount"] == "$10"


def test_extract_minimum_age_reads_prediction_market_terms():
    terms = "Must be 18 years or older and have a legal, U.S. residential address within the applicable state."
    assert extract_minimum_age(terms) == "18+"


def test_extract_minimum_age_returns_empty_when_terms_are_silent():
    assert extract_minimum_age("New customers only. Bonus expires in 7 days.") == ""
    assert extract_minimum_age("") == ""


def test_enrich_offer_dict_carries_minimum_age_from_terms():
    enriched = enrich_offer_dict(
        {
            "brand": "Kalshi",
            "offer_text": "Trade $10, Get $15",
            "terms": "Must be 18 years or older. Not available in AZ, IL, MA.",
        }
    )
    assert enriched["minimum_age"] == "18+"


def test_extract_bonus_amount_prefers_reward_verb_over_leading_amount():
    # An unrecognized qualifying verb must not make the fallback report the qualifying
    # amount as the reward.
    assert extract_bonus_amount("Stake $10, get $15 in credits") == "$15"
