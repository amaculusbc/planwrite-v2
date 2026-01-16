"""Tests for article API endpoints."""

import pytest


@pytest.mark.asyncio
async def test_list_articles_empty(client):
    """Test listing articles when none exist."""
    response = await client.get("/api/articles/")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_create_article(client):
    """Test creating a new article."""
    article_data = {
        "title": "Test Article",
        "keyword": "test keyword",
        "state": "NY",
    }

    response = await client.post("/api/articles/", json=article_data)
    assert response.status_code == 200

    data = response.json()
    assert data["title"] == "Test Article"
    assert data["keyword"] == "test keyword"
    assert data["state"] == "NY"
    assert data["status"] == "draft"
    assert "id" in data


@pytest.mark.asyncio
async def test_get_article(client):
    """Test getting a single article."""
    # First create an article
    create_response = await client.post("/api/articles/", json={
        "title": "Test Article",
        "keyword": "test",
    })
    article_id = create_response.json()["id"]

    # Then get it
    response = await client.get(f"/api/articles/{article_id}")
    assert response.status_code == 200
    assert response.json()["title"] == "Test Article"


@pytest.mark.asyncio
async def test_get_article_not_found(client):
    """Test getting a non-existent article."""
    response = await client.get("/api/articles/99999")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_update_article(client):
    """Test updating an article."""
    # Create
    create_response = await client.post("/api/articles/", json={
        "title": "Original Title",
        "keyword": "test",
    })
    article_id = create_response.json()["id"]

    # Update
    update_response = await client.put(f"/api/articles/{article_id}", json={
        "title": "Updated Title",
        "draft": "Some draft content",
    })

    assert update_response.status_code == 200
    data = update_response.json()
    assert data["title"] == "Updated Title"
    assert data["draft"] == "Some draft content"


@pytest.mark.asyncio
async def test_delete_article(client):
    """Test archiving an article."""
    # Create
    create_response = await client.post("/api/articles/", json={
        "title": "To Be Deleted",
        "keyword": "test",
    })
    article_id = create_response.json()["id"]

    # Delete (archive)
    delete_response = await client.delete(f"/api/articles/{article_id}")
    assert delete_response.status_code == 200
    assert delete_response.json()["status"] == "archived"

    # Verify it's archived
    get_response = await client.get(f"/api/articles/{article_id}")
    assert get_response.json()["status"] == "archived"
