"""CSB (Canada Sports Betting) onboarding: property, shortcode, and CA market."""

from app.schemas.outline import OutlineRequest, DraftRequest


def test_csb_property_forces_ca_market():
    # CSB is Canada-only. The request coerces market to CA even when omitted or US,
    # so the batch/API path stays correct without a CA selection from the caller.
    assert OutlineRequest(keyword="k", title="t", offer_property="csb", market="US").market == "CA"
    assert DraftRequest(keyword="k", title="t", offer_property="csb").market == "CA"
    assert OutlineRequest(keyword="k", title="t", offer_property="CSB").market == "CA"


def test_non_ca_property_market_unchanged():
    assert OutlineRequest(keyword="k", title="t", offer_property="action_network", market="US").market == "US"
    assert OutlineRequest(keyword="k", title="t", offer_property="goal_com", market="CA").market == "CA"


def test_csb_registered_with_bonus_block_style():
    from app.services.bam_offers import PROPERTIES, get_available_properties

    assert get_available_properties().get("csb") == "Canada Sports Betting"
    cfg = PROPERTIES["csb"]
    assert cfg["property_id"] == "21"
    assert cfg["placement_id"] == "2314"
    assert cfg["shortcode_style"] == "bonus_block"
