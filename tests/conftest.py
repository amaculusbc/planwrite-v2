"""Pytest fixtures for PlanWrite v2 tests."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from app.main import app, settings as app_settings
from app.database import Base, get_db
from app.services import usage_tracking


# Keep API tests focused on endpoint behavior, not auth flow.
app_settings.auth_enabled = False


# Test database
TEST_DATABASE_URL = "sqlite+aiosqlite:///./storage/test.db"
test_engine = create_async_engine(TEST_DATABASE_URL, echo=False)
test_session_maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture
async def db_session():
    """Create a fresh database session for each test."""
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with test_session_maker() as session:
        yield session

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture
async def client(db_session):
    """Create a test client with database override."""
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    original_usage_session_maker = usage_tracking.async_session_maker
    usage_tracking.async_session_maker = test_session_maker

    async with AsyncClient(app=app, base_url="http://test") as ac:
        yield ac

    usage_tracking.async_session_maker = original_usage_session_maker
    app.dependency_overrides.clear()
