"""Tests for offer parsing helpers."""

from app.services.offer_parsing import (
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
