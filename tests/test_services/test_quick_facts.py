"""Quick-facts block tests."""

from app.services.draft import _collapse_state_enumerations, _maybe_quick_facts_block
from app.services.offer_parsing import enrich_offer_dict
from app.services.quick_facts import eligible_states_text, render_quick_facts_table

EVENT_CONTEXT = "Featured event: Spain vs. France. Game time: 3:00 PM ET. Network: FOX"

KALSHI_TERMS = (
    "Must be 18 years or older and have a legal, U.S. residential address within the "
    "applicable state. Not available in AZ, IL, MA, MD, MI, MT, NV, and OH."
)


def _kalshi_offer():
    return enrich_offer_dict({
        "brand": "Kalshi",
        "bonus_code": "ACTION",
        "offer_text": "Trade $10, Get $15",
        "terms": KALSHI_TERMS,
    })


def test_quick_facts_renders_offer_facts_from_source():
    html = render_quick_facts_table(_kalshi_offer(), event_context=EVENT_CONTEXT)
    assert "<table>" in html
    assert "ACTION" in html
    assert "A $10 trade" in html
    assert "$15 in promo credits" in html
    assert "18+" in html
    assert "Spain vs. France, 3:00 PM ET on FOX" in html


def test_quick_facts_states_are_complete_not_enumerated():
    """The truncated state list is why this block exists: nationwide + exclusions is one row."""
    html = render_quick_facts_table(_kalshi_offer(), event_context=EVENT_CONTEXT)
    assert "All US states except AZ, IL, MA, MD, MI, MT, NV, OH" in html


def test_eligible_states_text_handles_each_shape():
    assert eligible_states_text({"states_list": ["ALL"], "terms": KALSHI_TERMS}) == (
        "All US states except AZ, IL, MA, MD, MI, MT, NV, OH"
    )
    assert eligible_states_text({"states_list": ["NJ", "PA"], "terms": ""}) == "NJ, PA"
    # Nothing known means no claim about availability at all.
    assert eligible_states_text({"states_list": [], "terms": ""}) == ""


def test_quick_facts_omits_rows_it_cannot_source():
    offer = {"brand": "Kalshi", "offer_text": "Trade $10, Get $15", "terms": ""}
    html = render_quick_facts_table(offer, event_context="")
    assert "Minimum age" not in html
    assert "Eligible states" not in html
    assert "Event" not in html
    assert "Trade $10, Get $15" in html


def test_quick_facts_renders_nothing_without_an_offer():
    assert render_quick_facts_table({}, event_context=EVENT_CONTEXT) == ""
    assert render_quick_facts_table({"brand": "Kalshi"}, event_context=EVENT_CONTEXT) == ""


def test_collapse_state_enumerations_replaces_recited_list():
    """Nick's list ran alphabetically and died at MO; it becomes the complete statement."""
    html = (
        "<p>The offer is available in AL, AK, AR, CA, CO, CT, DE, FL, GA, HI, ID, IN, IA, "
        "KS, KY, LA, ME, MN, MS, and MO.</p>"
    )
    cleaned = _collapse_state_enumerations(html, _kalshi_offer())
    assert cleaned == (
        "<p>The offer is available in all US states except AZ, IL, MA, MD, MI, MT, NV, OH.</p>"
    )


def test_collapse_state_enumerations_spares_the_exclusion_list():
    """The excluded states are real data and the one list that must survive."""
    html = "<p>The promo is not available in AZ, IL, MA, MD, MI, MT, NV, and OH.</p>"
    assert _collapse_state_enumerations(html, _kalshi_offer()) == html


def test_collapse_state_enumerations_spares_quick_facts_row():
    html = "<p>All US states except AZ, IL, MA, MD, MI, MT, NV, OH</p>"
    assert _collapse_state_enumerations(html, _kalshi_offer()) == html


def test_collapse_state_enumerations_leaves_explicit_state_offers_alone():
    """When the offer names a real state set, enumerating it is correct."""
    offer = enrich_offer_dict({
        "brand": "BetMGM",
        "offer_text": "Bet $5, Get $150",
        "states_list": ["NJ", "PA", "AZ", "CO", "MI", "VA"],
        "terms": "",
    })
    html = "<p>Available in NJ, PA, AZ, CO, MI, and VA.</p>"
    assert _collapse_state_enumerations(html, offer) == html


def test_collapse_state_enumerations_ignores_short_state_mentions():
    html = "<p>Live in NJ and PA today.</p>"
    assert _collapse_state_enumerations(html, _kalshi_offer()) == html


def test_quick_facts_block_only_for_non_goal_prediction_markets():
    offer = _kalshi_offer()
    assert _maybe_quick_facts_block(
        offer=offer, offer_property="action_network", prediction_market=True, event_context=EVENT_CONTEXT
    ).startswith("<h2>Kalshi promo code: the quick facts</h2>")
    # GOAL has its own brief and its own terms table.
    assert _maybe_quick_facts_block(
        offer=offer, offer_property="goal_com", prediction_market=True, event_context=EVENT_CONTEXT
    ) == ""
    assert _maybe_quick_facts_block(
        offer=offer, offer_property="action_network", prediction_market=False, event_context=EVENT_CONTEXT
    ) == ""
