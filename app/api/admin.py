"""Admin maintenance endpoints."""

from pathlib import Path

from fastapi import APIRouter

from app.config import get_settings
from app.services.internal_links import get_links_store

router = APIRouter(prefix="/api/admin", tags=["admin"])
settings = get_settings()


@router.get("/status")
async def admin_status():
    """Get status of internal indexes."""
    index_json = settings.storage_dir / "evergreen_index.json"
    index_vec = settings.storage_dir / "evergreen_vectors.npy"
    return {
        "evergreen_index": {
            "json": index_json.exists(),
            "vectors": index_vec.exists(),
        }
    }


@router.post("/rebuild-evergreen")
async def rebuild_evergreen():
    """Rebuild evergreen internal links index from data/evergreen.jsonl."""
    store = get_links_store()
    count = await store.ingest_from_jsonl()
    return {"status": "success", "count": count}
