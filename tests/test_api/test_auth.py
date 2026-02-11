"""Authentication middleware tests."""

import pytest
from httpx import AsyncClient

from app.main import app, settings as app_settings


@pytest.mark.asyncio
async def test_auth_blocks_api_when_enabled():
    prev = app_settings.auth_enabled
    app_settings.auth_enabled = True
    try:
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get("/api/admin/status")
            assert response.status_code == 401
            assert response.json().get("detail") == "Authentication required"
    finally:
        app_settings.auth_enabled = prev


@pytest.mark.asyncio
async def test_login_accepts_secondary_user_from_auth_users_json():
    prev_enabled = app_settings.auth_enabled
    prev_users_json = app_settings.auth_users_json
    prev_username = app_settings.auth_username
    prev_password = app_settings.auth_password
    app_settings.auth_enabled = True
    app_settings.auth_users_json = '{"admin":"admin-pass","usteam":"usteam-pass"}'
    app_settings.auth_username = "legacy"
    app_settings.auth_password = "legacy-pass"
    try:
        async with AsyncClient(app=app, base_url="http://test") as client:
            login = await client.post(
                "/login",
                data={"username": "usteam", "password": "usteam-pass", "next": "/admin"},
                follow_redirects=False,
            )
            assert login.status_code == 303
            assert login.headers.get("location") == "/admin"

            api = await client.get("/api/admin/status")
            assert api.status_code == 200
    finally:
        app_settings.auth_enabled = prev_enabled
        app_settings.auth_users_json = prev_users_json
        app_settings.auth_username = prev_username
        app_settings.auth_password = prev_password
