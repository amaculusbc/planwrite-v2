"""Tests for persisted generation artifacts on sync endpoints."""

from types import SimpleNamespace

import pytest


def _artifact_path(tmp_path, rel_path: str):
    return tmp_path / rel_path


@pytest.mark.asyncio
async def test_outline_sync_persists_manifest_and_source_facts(client, monkeypatch, tmp_path):
    async def fake_offer(*args, **kwargs):
        return {
            "id": "offer-1",
            "brand": "bet365",
            "offer_text": "Bet $10, Get $200 in Bonus Bets Win or Lose!",
            "bonus_code": "TOPACTION",
            "states": ["NJ"],
        }

    async def fake_outline(**kwargs):
        return [
            {"level": "intro", "title": "", "talking_points": ["Lead with offer"], "avoid": []},
            {"level": "h2", "title": "What Stands Out for Celtics vs. Spurs", "talking_points": ["Angle"], "avoid": []},
            {"level": "h2", "title": "How to Sign Up Before Celtics vs. Spurs", "talking_points": ["Steps"], "avoid": []},
        ]

    monkeypatch.setattr("app.api.generate.get_offer_by_id_bam", fake_offer)
    monkeypatch.setattr("app.api.generate.generate_structured_outline", fake_outline)
    monkeypatch.setattr("app.services.generation_artifacts.get_settings", lambda: SimpleNamespace(storage_dir=tmp_path))

    response = await client.post(
        "/api/generate/outline/sync",
        json={
            "keyword": "bet365 bonus code",
            "title": "bet365 bonus code: Celtics vs. Spurs",
            "offer_id": "offer-1",
            "offer_property": "action_network",
            "state": "NJ",
            "game_context": {
                "away_team": "Boston Celtics",
                "home_team": "San Antonio Spurs",
                "start_time": "2026-05-08T20:00:00-04:00",
                "network": "ESPN",
            },
            "article_preferences": {
                "secondary_keywords": ["bet365 sportsbook"],
                "structure_notes": "Keep it concise.",
            },
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["run_id"]
    assert data["source_facts"]["state"] == "NJ"
    assert data["source_facts"]["primary_offer"]["bonus_code"] == "TOPACTION"
    assert data["source_facts"]["event"]["away_team"] == "Boston Celtics"
    assert data["source_facts"]["event"]["sport"] == ""

    manifest_path = _artifact_path(tmp_path, data["artifact_manifest"])
    assert manifest_path.exists()

    run_response = await client.get(f"/api/generate/runs/{data['run_id']}")
    assert run_response.status_code == 200
    manifest = run_response.json()
    stages = [item["stage"] for item in manifest["artifacts"]]
    assert "request" in stages
    assert "source_facts" in stages
    assert "outline" in stages


@pytest.mark.asyncio
async def test_outline_sync_includes_bc_core_context_for_any_property(client, monkeypatch, tmp_path):
    captured: dict[str, str] = {}

    async def fake_offer(*args, **kwargs):
        return {
            "id": "offer-1",
            "brand": "bet365",
            "offer_text": "Bet $10, Get $200 in Bonus Bets Win or Lose!",
            "bonus_code": "TOPACTION",
            "states": ["NJ"],
        }

    async def fake_outline(**kwargs):
        captured["event_context"] = kwargs.get("event_context", "")
        return [
            {"level": "intro", "title": "", "talking_points": ["Lead with offer"], "avoid": []},
            {"level": "h2", "title": "What Stands Out for Celtics vs. Spurs", "talking_points": ["Angle"], "avoid": []},
            {"level": "h2", "title": "How to Sign Up Before Celtics vs. Spurs", "talking_points": ["Steps"], "avoid": []},
        ]

    async def fake_operator_context(source_facts):
        return ({
            "matched": True,
            "parent_name": "bet365",
            "requested_state_supported": True,
            "states": ["NJ", "PA"],
            "coverage": {"checked": True, "supported": True},
        }, "")

    async def fake_event_context(source_facts):
        return ({
            "matched": True,
            "event_name": "Boston Celtics at San Antonio Spurs",
            "scheduled_date": "2026-05-08T20:00:00-04:00",
            "network": "ESPN",
            "source_urls": ["https://core.example/events"],
        }, "")

    async def fake_expertise_context(source_facts, sports_context):
        return ({
            "matched": True,
            "editorial_points": ["Boston averaged 118.2 points per game.", "San Antonio allowed 114.7 points per game."],
        }, "")

    monkeypatch.setattr("app.api.generate.get_offer_by_id_bam", fake_offer)
    monkeypatch.setattr("app.api.generate.generate_structured_outline", fake_outline)
    monkeypatch.setattr("app.api.generate.build_operator_context", fake_operator_context)
    monkeypatch.setattr("app.api.generate.build_bc_core_event_context", fake_event_context)
    monkeypatch.setattr("app.api.generate.build_expertise_context", fake_expertise_context)
    monkeypatch.setattr("app.services.generation_artifacts.get_settings", lambda: SimpleNamespace(storage_dir=tmp_path))

    response = await client.post(
        "/api/generate/outline/sync",
        json={
            "keyword": "bet365 bonus code",
            "title": "bet365 bonus code: Celtics vs. Spurs",
            "offer_id": "offer-1",
            "offer_property": "sportshandle",
            "state": "NJ",
            "game_context": {
                "sport": "nba",
                "away_team": "Boston Celtics",
                "home_team": "San Antonio Spurs",
                "start_time": "2026-05-08T20:00:00-04:00",
                "network": "ESPN",
            },
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["source_facts"]["bc_core"]["operator"]["matched"] is True
    assert data["source_facts"]["bc_core"]["event"]["matched"] is True
    assert "INTERNAL OPERATOR CONTEXT:" in captured["event_context"]
    assert "INTERNAL EXPERTISE NOTES:" in captured["event_context"]


@pytest.mark.asyncio
async def test_draft_and_validate_append_to_same_run(client, monkeypatch, tmp_path):
    async def fake_offer(*args, **kwargs):
        return {
            "id": "offer-1",
            "brand": "bet365",
            "offer_text": "Bet $10, Get $200 in Bonus Bets Win or Lose!",
            "bonus_code": "TOPACTION",
            "states": ["NJ"],
            "terms": "Available in NJ only.",
        }

    async def fake_outline(**kwargs):
        return [
            {"level": "intro", "title": "", "talking_points": ["Lead with offer"], "avoid": []},
            {"level": "h2", "title": "How bet365 bonus code fits Celtics vs. Spurs", "talking_points": ["Angle"], "avoid": []},
            {"level": "h2", "title": "How to Sign Up Before Celtics vs. Spurs", "talking_points": ["Steps"], "avoid": []},
        ]

    async def fake_draft(**kwargs):
        return "<h1>bet365 bonus code: Celtics vs. Spurs</h1><p>States Available: NJ.</p>"

    monkeypatch.setattr("app.api.generate.get_offer_by_id_bam", fake_offer)
    monkeypatch.setattr("app.api.generate.generate_structured_outline", fake_outline)
    monkeypatch.setattr("app.api.generate.generate_draft_from_outline", fake_draft)
    monkeypatch.setattr("app.services.generation_artifacts.get_settings", lambda: SimpleNamespace(storage_dir=tmp_path))

    outline_response = await client.post(
        "/api/generate/outline/sync",
        json={
            "keyword": "bet365 bonus code",
            "title": "bet365 bonus code: Celtics vs. Spurs",
            "offer_id": "offer-1",
            "offer_property": "action_network",
            "state": "NJ",
        },
    )
    run_id = outline_response.json()["run_id"]

    draft_response = await client.post(
        "/api/generate/draft/sync",
        json={
            "run_id": run_id,
            "keyword": "bet365 bonus code",
            "title": "bet365 bonus code: Celtics vs. Spurs",
            "offer_id": "offer-1",
            "offer_property": "action_network",
            "state": "NJ",
            "outline_structured": outline_response.json()["outline_structured"],
            "outline_text": outline_response.json()["outline_text"],
        },
    )
    assert draft_response.status_code == 200
    draft_data = draft_response.json()
    assert draft_data["run_id"] == run_id
    assert "States Available: NJ." in draft_data["draft"]

    validate_response = await client.post(
        "/api/generate/validate",
        json={
            "content": draft_data["draft"],
            "state": "NJ",
            "keyword": "bet365 bonus code",
            "offer_id": "offer-1",
            "offer_property": "action_network",
            "run_id": run_id,
        },
    )
    assert validate_response.status_code == 200
    assert validate_response.json()["run_id"] == run_id

    run_response = await client.get(f"/api/generate/runs/{run_id}")
    manifest = run_response.json()
    stages = [item["stage"] for item in manifest["artifacts"]]
    assert "draft" in stages
    assert "validation" in stages


@pytest.mark.asyncio
async def test_draft_sync_includes_bc_core_context_for_any_property(client, monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    async def fake_offer(*args, **kwargs):
        return {
            "id": "offer-1",
            "brand": "Novig",
            "offer_text": "Spend $5, Get $50 in Novig Coins!",
            "bonus_code": "",
            "states": ["NJ", "PA"],
            "terms": "Available in NJ and PA only.",
        }

    async def fake_draft(**kwargs):
        captured["event_context"] = kwargs.get("event_context", "")
        captured["bc_core_context"] = kwargs.get("bc_core_context")
        return "<h1>Novig promo code: Celtics vs. Spurs</h1><p>States Available: NJ, PA.</p>"

    async def fake_operator_context(source_facts):
        return ({
            "matched": True,
            "parent_name": "Novig",
            "requested_state_supported": True,
            "states": ["NJ", "PA"],
            "coverage": {"checked": True, "supported": True},
        }, "")

    async def fake_event_context(source_facts):
        return ({
            "matched": True,
            "event_name": "Boston Celtics at San Antonio Spurs",
            "scheduled_date": "2026-05-08T20:00:00-04:00",
            "network": "ESPN",
            "source_urls": ["https://core.example/events"],
        }, "")

    async def fake_expertise_context(source_facts, sports_context):
        return ({
            "matched": True,
            "editorial_points": ["Boston entered on a 4-1 run.", "San Antonio allowed 114.7 points per game."],
        }, "")

    monkeypatch.setattr("app.api.generate.get_offer_by_id_bam", fake_offer)
    monkeypatch.setattr("app.api.generate.generate_draft_from_outline", fake_draft)
    monkeypatch.setattr("app.api.generate.build_operator_context", fake_operator_context)
    monkeypatch.setattr("app.api.generate.build_bc_core_event_context", fake_event_context)
    monkeypatch.setattr("app.api.generate.build_expertise_context", fake_expertise_context)
    monkeypatch.setattr("app.services.generation_artifacts.get_settings", lambda: SimpleNamespace(storage_dir=tmp_path))

    response = await client.post(
        "/api/generate/draft/sync",
        json={
            "keyword": "novig promo code",
            "title": "novig promo code: Celtics vs. Spurs",
            "offer_id": "offer-1",
            "offer_property": "fantasy_labs",
            "state": "NJ",
            "outline_structured": [
                {"level": "intro", "title": "", "talking_points": ["Lead with offer"], "avoid": []},
                {"level": "h2", "title": "How Novig fits Celtics vs. Spurs", "talking_points": ["Angle"], "avoid": []},
            ],
            "game_context": {
                "sport": "nba",
                "away_team": "Boston Celtics",
                "home_team": "San Antonio Spurs",
                "start_time": "2026-05-08T20:00:00-04:00",
                "network": "ESPN",
            },
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["source_facts"]["bc_core"]["operator"]["matched"] is True
    assert data["source_facts"]["bc_core"]["expertise"]["matched"] is True
    assert "INTERNAL OPERATOR CONTEXT:" in captured["event_context"]
    assert "INTERNAL EVENT CONTEXT:" in captured["event_context"]
    assert "INTERNAL EXPERTISE NOTES:" in captured["event_context"]
    assert isinstance(captured["bc_core_context"], dict)
    assert captured["bc_core_context"]["expertise"]["matched"] is True
