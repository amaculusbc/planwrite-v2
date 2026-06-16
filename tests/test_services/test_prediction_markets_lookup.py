import pytest

from app.services import prediction_markets as pm


def _candidate(
    *,
    provider: str = "polymarket",
    event_name: str = "Mexico vs South Africa",
    market_title: str = "Will Mexico beat South Africa?",
    selection: str = "Mexico",
    volume: float = 2500,
):
    return pm.PredictionMarketCandidate(
        provider=provider,
        provider_market_id=f"{provider}-1",
        event_name=event_name,
        market_title=market_title,
        market_type="winner",
        selection=selection,
        side=selection.lower(),
        implied_probability=0.56,
        volume=volume,
        liquidity=1000,
        close_time="2026-06-11T23:59:00Z",
        event_start_time="2026-06-11T19:00:00Z",
        url=f"https://{provider}.example/market",
    )


def test_normalize_polymarket_event_creates_outcome_candidates():
    candidates = pm._normalize_polymarket_event(
        {
            "title": "Mexico vs South Africa",
            "slug": "mexico-vs-south-africa",
            "markets": [
                {
                    "id": "m1",
                    "question": "Mexico vs South Africa winner",
                    "outcomes": '["Mexico","South Africa"]',
                    "outcomePrices": '["0.58","0.42"]',
                    "active": True,
                    "closed": False,
                    "volumeNum": 1000,
                }
            ],
        }
    )

    assert len(candidates) == 2
    assert candidates[0].provider == "polymarket"
    assert candidates[0].selection == "Mexico"
    assert candidates[0].implied_probability == 0.58
    assert candidates[0].url == "https://polymarket.com/event/mexico-vs-south-africa"


@pytest.mark.asyncio
async def test_search_prediction_markets_filters_to_both_team_matches(monkeypatch):
    pm._CACHE.clear()
    calls = {"poly": 0, "kalshi": 0}

    async def fake_poly(search, client):
        calls["poly"] += 1
        return [
            _candidate(),
            _candidate(event_name="Mexico futures", market_title="Will Mexico win the group?", selection="Yes"),
        ]

    async def fake_kalshi(search, client):
        calls["kalshi"] += 1
        return [
            _candidate(
                provider="kalshi",
                event_name="Election market",
                market_title="South Africa election market",
                selection="Yes",
            )
        ]

    monkeypatch.setattr(pm, "_fetch_polymarket", fake_poly)
    monkeypatch.setattr(pm, "_fetch_kalshi", fake_kalshi)

    payload = await pm.search_prediction_markets(
        pm.PredictionMarketSearch(
            sport="soccer",
            away_team="Mexico",
            home_team="South Africa",
            event_date="2026-06-11",
            start_time="2026-06-11T19:00:00Z",
            provider="all",
            limit=10,
        )
    )

    assert payload["cached"] is False
    assert payload["candidate_count"] == 3
    assert payload["matched_count"] == 1
    assert payload["markets"][0]["market_title"] == "Will Mexico beat South Africa?"
    assert "both-teams" in payload["markets"][0]["score_reasons"]
    assert calls == {"poly": 1, "kalshi": 1}


@pytest.mark.asyncio
async def test_search_prediction_markets_uses_short_ttl_cache(monkeypatch):
    pm._CACHE.clear()
    calls = {"poly": 0}

    async def fake_poly(search, client):
        calls["poly"] += 1
        return [_candidate()]

    async def fake_kalshi(search, client):
        return []

    monkeypatch.setattr(pm, "_fetch_polymarket", fake_poly)
    monkeypatch.setattr(pm, "_fetch_kalshi", fake_kalshi)
    search = pm.PredictionMarketSearch(
        sport="soccer",
        away_team="Mexico",
        home_team="South Africa",
        event_date="2026-06-11",
        start_time="2026-06-11T19:00:00Z",
    )

    first = await pm.search_prediction_markets(search)
    second = await pm.search_prediction_markets(search)

    assert first["cached"] is False
    assert second["cached"] is True
    assert calls["poly"] == 1


def test_build_prediction_market_example_uses_selected_market_price():
    result = pm.build_prediction_market_example(
        market={
            "provider": "polymarket",
            "provider_market_id": "m1",
            "market_title": "Will Mexico beat South Africa?",
            "selection": "Mexico",
            "implied_probability": 0.5,
            "event_name": "Mexico vs South Africa",
            "url": "https://polymarket.example/market",
        },
        position_amount=25,
        qualifying_amount=10,
        reward_amount=50,
    )

    assert "I complete the $10 qualifying action first" in result["example_text"]
    assert "$25 Mexico position" in result["example_text"]
    assert result["contracts"] == 50
    assert result["potential_payout"] == 50
    assert result["prediction_market"]["provider_market_id"] == "m1"


def test_kalshi_scoring_uses_close_time_when_open_time_is_not_event_time():
    candidate = _candidate(
        provider="kalshi",
        event_name="KXPGA3BALL-USO26R1MROBMSHAJSOL-MSHA",
        market_title="Will Manav Shah win the 1st round 3-ball matchup?",
        selection="Manav Shah beats Robles and Sollon",
    )
    candidate.event_start_time = "2026-06-16T13:00:00Z"
    candidate.close_time = "2026-07-02T18:42:00Z"

    scored = pm._score_candidate(
        candidate,
        pm.PredictionMarketSearch(
            sport="golf",
            event_name="Matthew Robles Manav Shah Jake Sollon 1st round 3-ball matchup",
            event_date="2026-07-02",
            start_time="2026-07-02T18:42:00Z",
        ),
    )

    assert "date-window" in scored.score_reasons
    assert scored.score >= 35


def test_kalshi_no_side_uses_clear_no_label_when_provider_duplicates_label():
    candidates = pm._normalize_kalshi_market(
        {
            "ticker": "KXTEST",
            "event_ticker": "KXTESTEVENT",
            "title": "Will Mexico beat South Africa?",
            "yes_sub_title": "Mexico beats South Africa",
            "no_sub_title": "Mexico beats South Africa",
            "yes_ask_dollars": "0.56",
            "no_ask_dollars": "0.46",
        }
    )

    assert candidates[0].selection == "Mexico beats South Africa"
    assert candidates[1].selection == "No on Will Mexico beat South Africa?"


def test_classify_exact_score_market_as_prop_not_winner():
    assert pm._classify_market("Will the 1st half score be France wins 3-1?") == "prop"
    assert pm._classify_market("Will France win the 2nd Half?") == "winner"


def test_team_match_score_uses_kalshi_country_codes_in_ticker():
    score, reasons = pm._team_match_score(
        "KXWC2H-26JUN16FRASEN-SEN Will Senegal win the 2nd Half? Senegal",
        pm.PredictionMarketSearch(
            sport="soccer",
            away_team="Senegal",
            home_team="France",
            event_name="Senegal vs France",
        ),
    )

    assert score >= 60
    assert "both-teams" in reasons


def test_team_match_score_does_not_treat_frances_as_france_team_match():
    score, reasons = pm._team_match_score(
        "KXATPGTOTAL-26JUN17TIASHI Frances Tiafoe vs Sho Shimabukuro",
        pm.PredictionMarketSearch(
            sport="soccer",
            away_team="Senegal",
            home_team="France",
            event_name="Senegal vs France",
        ),
    )

    assert "both-teams" not in reasons
    assert score < 35


def test_kalshi_soccer_event_tickers_include_home_away_country_codes():
    tickers = pm._kalshi_soccer_event_tickers(
        pm.PredictionMarketSearch(
            sport="soccer",
            away_team="Senegal",
            home_team="France",
            event_date="2026-06-16",
            start_time="2026-06-16T19:00:00Z",
        )
    )

    assert "KXWC2H-26JUN16FRASEN" in tickers
    assert "KXWC2HSPREAD-26JUN16FRASEN" in tickers


@pytest.mark.asyncio
async def test_fetch_kalshi_uses_targeted_soccer_event_tickers(monkeypatch):
    requested_event_tickers = []

    class FakeResponse:
        def __init__(self, payload, status_code=200):
            self._payload = payload
            self.status_code = status_code

        def raise_for_status(self):
            if self.status_code >= 400:
                raise AssertionError(self.status_code)

        def json(self):
            return self._payload

    class FakeClient:
        async def get(self, url, params=None):
            params = params or {}
            if params.get("event_ticker"):
                requested_event_tickers.append(params["event_ticker"])
            if params.get("event_ticker") == "KXWC2H-26JUN16FRASEN":
                return FakeResponse(
                    {
                        "markets": [
                            {
                                "ticker": "KXWC2H-26JUN16FRASEN-FRA",
                                "event_ticker": "KXWC2H-26JUN16FRASEN",
                                "title": "Will France win the 2nd Half?",
                                "yes_sub_title": "France",
                                "yes_ask_dollars": "0.55",
                                "no_ask_dollars": "0.51",
                                "close_time": "2026-06-30T19:00:00Z",
                            }
                        ],
                        "cursor": "",
                    }
                )
            return FakeResponse({"markets": [], "cursor": ""})

    candidates = await pm._fetch_kalshi(
        pm.PredictionMarketSearch(
            sport="soccer",
            away_team="Senegal",
            home_team="France",
            event_name="Senegal vs France",
            event_date="2026-06-16",
            start_time="2026-06-16T19:00:00Z",
            provider="kalshi",
        ),
        FakeClient(),
    )

    assert "KXWC2H-26JUN16FRASEN" in requested_event_tickers
    assert candidates
    assert candidates[0].provider_market_id == "KXWC2H-26JUN16FRASEN-FRA"


@pytest.mark.asyncio
async def test_fetch_kalshi_uses_fallback_host_for_targeted_soccer_event():
    requested_hosts = []

    class FakeResponse:
        def __init__(self, payload, status_code=200):
            self._payload = payload
            self.status_code = status_code

        def raise_for_status(self):
            if self.status_code >= 400:
                raise AssertionError(self.status_code)

        def json(self):
            return self._payload

    class FakeClient:
        async def get(self, url, params=None):
            params = params or {}
            if params.get("event_ticker") == "KXWC2H-26JUN16FRASEN":
                requested_hosts.append(url)
                if url.startswith(pm.KALSHI_BASE_URL):
                    return FakeResponse({}, status_code=429)
                return FakeResponse(
                    {
                        "markets": [
                            {
                                "ticker": "KXWC2H-26JUN16FRASEN-FRA",
                                "event_ticker": "KXWC2H-26JUN16FRASEN",
                                "title": "Will France win the 2nd Half?",
                                "yes_sub_title": "France",
                                "yes_ask_dollars": "0.55",
                                "no_ask_dollars": "0.51",
                                "close_time": "2026-06-16T21:00:00Z",
                            }
                        ]
                    }
                )
            return FakeResponse({"markets": [], "cursor": ""})

    candidates = await pm._fetch_kalshi(
        pm.PredictionMarketSearch(
            sport="soccer",
            away_team="Senegal",
            home_team="France",
            event_name="Senegal vs France",
            event_date="2026-06-16",
            start_time="2026-06-16T19:00:00Z",
            provider="kalshi",
        ),
        FakeClient(),
    )

    assert requested_hosts == [
        f"{pm.KALSHI_BASE_URL}/markets",
        f"{pm.KALSHI_FALLBACK_BASE_URL}/markets",
    ]
    assert candidates


@pytest.mark.asyncio
async def test_fetch_kalshi_skips_broad_scan_for_targeted_soccer_event():
    broad_calls = 0

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"markets": [], "cursor": ""}

    class FakeClient:
        async def get(self, url, params=None):
            nonlocal broad_calls
            params = params or {}
            if not params.get("event_ticker"):
                broad_calls += 1
            return FakeResponse()

    candidates = await pm._fetch_kalshi(
        pm.PredictionMarketSearch(
            sport="soccer",
            away_team="Senegal",
            home_team="France",
            event_name="Senegal vs France",
            event_date="2026-06-16",
            start_time="2026-06-16T19:00:00Z",
            provider="kalshi",
        ),
        FakeClient(),
    )

    assert candidates == []
    assert broad_calls == 0
