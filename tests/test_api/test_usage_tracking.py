"""Usage tracking API tests."""

import pytest


@pytest.mark.asyncio
async def test_usage_events_persist_and_can_be_queried(client):
    # First call records an api_request usage row via middleware.
    first = await client.get("/api/admin/usage/events?limit=10")
    assert first.status_code == 200

    # Second call should observe at least one persisted event.
    second = await client.get("/api/admin/usage/events?limit=50")
    assert second.status_code == 200
    payload = second.json()
    events = payload.get("events", [])
    assert isinstance(events, list)
    assert len(events) >= 1
    assert any(e.get("event_type") in {"api_request", "api_request_blocked"} for e in events)


@pytest.mark.asyncio
async def test_usage_export_returns_csv(client):
    resp = await client.get("/api/admin/usage/export?limit=10")
    assert resp.status_code == 200
    assert resp.headers.get("content-type", "").startswith("text/csv")
    body = resp.text
    assert "username,event_type,method,path,status_code" in body
