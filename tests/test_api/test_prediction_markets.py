import pytest

from app.api import prediction_markets as prediction_markets_api


@pytest.mark.asyncio
async def test_prediction_market_search_endpoint_returns_ranked_markets(client, monkeypatch):
    captured = {}

    async def fake_search(search):
        captured["search"] = search
        return {
            "query": {"sport": search.sport},
            "markets": [
                {
                    "provider": "polymarket",
                    "provider_market_id": "m1",
                    "market_title": "Will Mexico beat South Africa?",
                    "selection": "Mexico",
                    "implied_probability": 0.56,
                    "score": 80,
                }
            ],
            "candidate_count": 2,
            "matched_count": 1,
            "cached": False,
            "errors": {},
        }

    monkeypatch.setattr(prediction_markets_api, "search_prediction_markets", fake_search)

    response = await client.get(
        "/api/prediction-markets/search",
        params={
            "sport": "soccer",
            "away_team": "Mexico",
            "home_team": "South Africa",
            "event_date": "2026-06-11",
            "provider": "polymarket",
            "limit": 5,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["markets"][0]["selection"] == "Mexico"
    assert captured["search"].sport == "soccer"
    assert captured["search"].provider == "polymarket"
    assert captured["search"].limit == 5


@pytest.mark.asyncio
async def test_prediction_market_example_endpoint_builds_prompt_text(client):
    response = await client.post(
        "/api/prediction-markets/example",
        json={
            "market": {
                "provider": "kalshi",
                "provider_market_id": "KXTEST",
                "market_title": "Will Mexico beat South Africa?",
                "selection": "Yes",
                "implied_probability": 0.4,
            },
            "position_amount": 20,
            "qualifying_amount": 10,
            "reward_amount": 50,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert "Will Mexico beat South Africa?" in data["example_text"]
    assert data["contracts"] == 50
    assert data["provider"] == "kalshi"
