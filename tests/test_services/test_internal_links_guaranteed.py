"""Tests for guaranteed internal-link insertion behavior."""

import json
import shutil
from pathlib import Path
from uuid import uuid4

from app.services.internal_links import InternalLinkSpec, InternalLinksStore, format_links_markdown


def test_always_include_source_links_are_required(monkeypatch):
    base = Path("storage") / f"test_internal_links_{uuid4().hex}"
    data_dir = base / "data"
    storage_dir = base / "storage"
    data_dir.mkdir(parents=True, exist_ok=True)
    storage_dir.mkdir(parents=True, exist_ok=True)

    source_path = data_dir / "evergreen_action_network.jsonl"
    source_rows = [
        {
            "id": "required-1",
            "title": "Guaranteed Link",
            "url": "https://example.com/guaranteed",
            "recommended_anchors": ["guaranteed link"],
            "always_include": True,
        },
        {
            "id": "optional-1",
            "title": "Optional Link",
            "url": "https://example.com/optional",
            "recommended_anchors": ["optional link"],
            "always_include": False,
        },
    ]
    with open(source_path, "w", encoding="utf-8") as f:
        for row in source_rows:
            f.write(json.dumps(row) + "\n")

    monkeypatch.setattr("app.services.internal_links.DATA_DIR", data_dir)
    monkeypatch.setattr("app.services.internal_links.STORAGE_DIR", storage_dir)
    monkeypatch.setattr("app.services.internal_links.LEGACY_SOURCE_JSONL", data_dir / "evergreen.jsonl")
    monkeypatch.setattr("app.services.internal_links.LEGACY_INDEX_JSON", storage_dir / "evergreen_index.json")
    monkeypatch.setattr("app.services.internal_links.LEGACY_INDEX_VEC", storage_dir / "evergreen_vectors.npy")

    try:
        store = InternalLinksStore(property_key="action_network")
        required = store.get_required_links()
        urls = {link.url for link in required}

        assert "https://example.com/guaranteed" in urls
        assert "https://example.com/optional" not in urls
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_format_links_markdown_marks_guaranteed_links():
    md = format_links_markdown(
        [
            InternalLinkSpec(
                title="Guaranteed Link",
                url="https://example.com/guaranteed",
                recommended_anchors=["guaranteed link"],
                always_include=True,
            )
        ],
        brand="Bet365",
        prediction_market=False,
    )

    assert "GUARANTEED:" in md
    assert "must appear in the final article" in md
