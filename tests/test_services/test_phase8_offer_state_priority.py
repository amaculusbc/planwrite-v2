"""Phase 8 tests for state-specific offer prioritization."""

import pytest

from app.services import bam_offers


def test_available_properties_includes_goal_bam_config():
    properties = bam_offers.get_available_properties()
    goal_config = bam_offers.PROPERTIES["goal_com"]

    assert properties["goal_com"] == "GOAL"
    assert goal_config["property_id"] == "326"
    assert goal_config["placement_id"] == "2066"
    assert goal_config["switchboard_domain"] == "us-betting.goal.com"


def test_canadian_province_geo_override_sets_country_code():
    params = bam_offers._geo_params_for_state("ON")

    assert params == {"location": "ON", "country_code": "CA"}


def test_catalog_locations_for_canada_market_exclude_us_states():
    locations, include_base = bam_offers._catalog_locations_for_market("ALL", "CA")

    assert include_base is False
    assert "ON" in locations
    assert "NJ" not in locations


def test_offer_matches_market_requires_canadian_province_for_ca_market():
    assert bam_offers._offer_matches_market({"states": ["ON", "QC"]}, "CA") is True
    assert bam_offers._offer_matches_market({"states": ["NJ", "PA"]}, "CA") is False
    assert bam_offers._offer_matches_market({"states": ["NJ", "PA"]}, "US") is True


def test_normalize_bam_affiliate_type_removes_spaces_for_shortcodes():
    assert bam_offers.normalize_bam_affiliate_type("social operators") == "social-sportsbook"
    assert bam_offers.normalize_bam_affiliate_type("daily fantasy") == "dfs"


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


@pytest.mark.asyncio
async def test_get_offers_bam_passes_state_to_bam_location_override(monkeypatch):
    captured: dict = {}

    async def fake_fetch_offers_from_bam(*args, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(bam_offers, "fetch_offers_from_bam", fake_fetch_offers_from_bam)

    await bam_offers.get_offers_bam(
        state="NJ",
        property_key="action_network",
        context="web-article-top-stories",
    )

    assert captured["location"] == "NJ"
    assert "country_code" not in captured or captured["country_code"] == ""


@pytest.mark.asyncio
async def test_get_offer_by_id_bam_uses_state_scoped_feed(monkeypatch):
    captured: dict = {}

    async def fake_fetch_offers_from_bam(*args, **kwargs):
        captured.update(kwargs)
        return [
            {"id": "generic", "brand": "Novig", "offer_text": "Generic"},
            {"id": "nj", "brand": "Novig", "offer_text": "NJ scoped"},
        ]

    monkeypatch.setattr(bam_offers, "fetch_offers_from_bam", fake_fetch_offers_from_bam)

    offer = await bam_offers.get_offer_by_id_bam(
        "nj",
        property_key="action_network",
        state="NJ",
    )

    assert offer["offer_text"] == "NJ scoped"
    assert captured["location"] == "NJ"


@pytest.mark.asyncio
async def test_get_offer_catalog_bam_unions_location_scoped_offers(monkeypatch):
    async def fake_fetch_offers_from_bam(*args, **kwargs):
        location = kwargs.get("location", "")
        if not location:
            return [{"id": "base", "brand": "bet365", "offer_text": "Base offer", "states": ["ALL"]}]
        if location == "NJ":
            return [{"id": "novig", "brand": "Novig", "offer_text": "Spend $5, Get $50", "states": ["ALL"]}]
        if location == "IL":
            return [{"id": "rebet", "brand": "Rebet", "offer_text": "Match up to $100", "states": ["ALL"]}]
        return []

    monkeypatch.setattr(bam_offers, "fetch_offers_from_bam", fake_fetch_offers_from_bam)

    offers = await bam_offers.get_offer_catalog_bam(
        state="ALL",
        property_key="action_network",
        force_refresh=True,
    )

    assert {offer["brand"] for offer in offers} >= {"bet365", "Novig", "Rebet"}


@pytest.mark.asyncio
async def test_get_offer_by_id_bam_falls_back_to_catalog_when_state_feed_misses(monkeypatch):
    async def fake_fetch_offers_from_bam(*args, **kwargs):
        location = kwargs.get("location", "")
        if location == "NJ":
            return []
        if location == "IL":
            return [{"id": "novig", "brand": "Novig", "offer_text": "Spend $5, Get $50", "states": ["ALL"]}]
        return []

    monkeypatch.setattr(bam_offers, "fetch_offers_from_bam", fake_fetch_offers_from_bam)

    offer = await bam_offers.get_offer_by_id_bam(
        "novig",
        property_key="action_network",
        state="NJ",
    )

    assert offer["brand"] == "Novig"


@pytest.mark.asyncio
async def test_get_offer_by_id_bam_all_state_prefers_catalog_state_union(monkeypatch):
    async def fake_fetch_offers_from_bam(*args, **kwargs):
        location = kwargs.get("location", "")
        if not location:
            return [{"id": "novig", "brand": "Novig", "offer_text": "Spend $5, Get $50", "states": ["ALL"]}]
        if location in {"NJ", "PA", "IL"}:
            return [{"id": "novig", "brand": "Novig", "offer_text": "Spend $5, Get $50", "states": ["ALL"]}]
        return []

    monkeypatch.setattr(bam_offers, "fetch_offers_from_bam", fake_fetch_offers_from_bam)
    monkeypatch.setattr(bam_offers, "_load_cache", lambda *args, **kwargs: (None, []))
    bam_offers._cached_offers.clear()
    bam_offers._last_fetch.clear()

    offer = await bam_offers.get_offer_by_id_bam(
        "novig",
        property_key="action_network",
        state="ALL",
    )

    assert set(offer["states_list"]) == {"NJ", "PA", "IL"}
