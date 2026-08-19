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


def test_catalog_locations_for_us_market_exclude_base_feed():
    # The base feed is BAM's Canada-leaning default catalog; including it put
    # GOALCA / C$ / French offers into US articles.
    locations, include_base = bam_offers._catalog_locations_for_market("ALL", "US")

    assert include_base is False
    assert "NJ" in locations
    assert "ON" not in locations


def test_offer_matches_market_requires_canadian_province_for_ca_market():
    assert bam_offers._offer_matches_market({"states": ["ON", "QC"]}, "CA") is True
    assert bam_offers._offer_matches_market({"states": ["NJ", "PA"]}, "CA") is False
    assert bam_offers._offer_matches_market({"states": ["NJ", "PA"]}, "US") is True


def test_foreign_campaigns_never_match_the_us_market():
    # BAM serves these under US location overrides, so only their copy gives them away.
    us_states = ["NJ", "PA"]
    for offer in (
        {"states": us_states, "brand": "TonyBet", "bonus_code": "GOALCA",
         "offer_text": "100% Deposit Match Up to C$350!"},
        {"states": us_states, "brand": "bet365", "bonus_code": "365INT",
         "offer_text": "UK: Bet £10 & Get £30 in Free Bets!"},
        {"states": us_states, "brand": "bet365", "bonus_code": "365INT",
         "offer_text": "Mexico: Consiga hasta $3,000 en apuestas gratis"},
    ):
        assert bam_offers._offer_matches_market(offer, "US") is False

    # A plain US offer is untouched.
    assert bam_offers._offer_matches_market(
        {"states": us_states, "brand": "bet365", "offer_text": "Bet $10, Get $150 in Bonus Bets!"}, "US"
    ) is True


def test_canadian_market_keeps_c_dollar_offers_but_drops_uk_and_mexico():
    ca = ["ON", "QC"]
    assert bam_offers._offer_matches_market(
        {"states": ca, "brand": "TonyBet", "offer_text": "100% Deposit Match Up to C$350!"}, "CA"
    ) is True
    assert bam_offers._offer_matches_market(
        {"states": ca, "brand": "bet365", "offer_text": "UK: Bet £10 & Get £30 in Free Bets!"}, "CA"
    ) is False


def test_offer_with_no_availability_evidence_matches_no_market():
    # Base-feed Canadian offers arrive as states=["ALL"] with no source_locations
    # and used to default into the US catalog.
    for offer in ({"states": ["ALL"]}, {"states": []}, {"states": ["ALL"], "source_locations": []}):
        assert bam_offers._offer_matches_market(offer, "US") is False
        assert bam_offers._offer_matches_market(offer, "CA") is False


def test_normalize_catalog_states_merges_source_locations():
    # bet365 GOALBET: BAM metadata says KY, but BAM served it for 20 US states.
    offer = bam_offers._normalize_catalog_offer_states(
        {"states": ["KY"], "source_locations": ["AZ", "CO", "KY", "NY"]},
        "US",
    )
    assert offer["states"] == ["AZ", "CO", "KY", "NY"]
    assert offer["states_list"] == ["AZ", "CO", "KY", "NY"]


def test_normalize_catalog_states_uses_terms_exclusions_for_nationwide_offers():
    # A prediction-market offer names its excluded states in terms. That is nationwide minus
    # those, not an enumeration of source_locations (which BAM returns for nearly every state).
    offer = bam_offers._normalize_catalog_offer_states(
        {
            "brand": "Kalshi",
            "source_locations": ["AZ", "CA", "NY", "TX", "IL"],  # BAM over-reports; ignore it
            "terms": "18+ only. Not available in AZ, IL, MA, MD, MI, MT, NV, and OH.",
        },
        "US",
    )
    assert offer["states_list"] == ["ALL"]
    assert offer["excluded_states_list"] == ["AZ", "IL", "MA", "MD", "MI", "MT", "NV", "OH"]


def test_normalize_catalog_states_keeps_states_outside_the_sweep():
    # Fanatics lists MO/VT, which are not in the location sweep; keep them.
    offer = bam_offers._normalize_catalog_offer_states(
        {"states": ["MO", "VT", "NJ"], "source_locations": ["NJ", "PA"]},
        "US",
    )
    assert offer["states"] == ["MO", "NJ", "PA", "VT"]


def test_normalize_catalog_states_never_leaks_us_state_into_ca_market():
    # Same junk KY metadata on the Canadian variant must not reach CA copy.
    offer = bam_offers._normalize_catalog_offer_states(
        {"states": ["KY"], "source_locations": ["BC", "MB", "QC"]},
        "CA",
    )
    assert offer["states"] == ["BC", "MB", "QC"]
    assert "KY" not in offer["states"]


def test_normalize_catalog_states_replaces_placeholder_all():
    offer = bam_offers._normalize_catalog_offer_states(
        {"states": ["ALL"], "source_locations": ["NJ", "PA"]},
        "US",
    )
    assert offer["states"] == ["NJ", "PA"]


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
async def test_get_offer_by_id_bam_prefers_picker_catalog_variant(monkeypatch):
    async def fake_catalog(*args, **kwargs):
        return [
            {
                "id": "bet365-selected",
                "brand": "bet365",
                "offer_text": "Bet $10, Get $365 in Bonus Bets",
                "bonus_code": "TOPACTION",
            }
        ]

    async def fake_fetch_offers_from_bam(*args, **kwargs):
        return [
            {
                "id": "bet365-selected",
                "brand": "bet365",
                "offer_text": "Bet $10, Get $365 in Bonus Bets",
                "bonus_code": "GOALBET",
            }
        ]

    monkeypatch.setattr(bam_offers, "get_offer_catalog_bam", fake_catalog)
    monkeypatch.setattr(bam_offers, "fetch_offers_from_bam", fake_fetch_offers_from_bam)

    offer = await bam_offers.get_offer_by_id_bam(
        "bet365-selected",
        property_key="goal_com",
        state="NJ",
    )

    assert offer["bonus_code"] == "TOPACTION"


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
