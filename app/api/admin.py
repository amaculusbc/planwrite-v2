"""Admin maintenance endpoints."""

from fastapi import APIRouter

from app.config import get_settings
from app.services.internal_links import get_links_store
from app.services.rag_builder import build_rag_index

router = APIRouter(prefix="/api/admin", tags=["admin"])
settings = get_settings()


@router.get("/status")
async def admin_status():
    """Get status of internal indexes."""
    index_json = settings.storage_dir / "evergreen_index.json"
    index_vec = settings.storage_dir / "evergreen_vectors.npy"
    rag_index = settings.storage_dir / "faiss_index" / "index.faiss"
    rag_meta = settings.storage_dir / "faiss_index" / "metadata.jsonl"
    return {
        "evergreen_index": {
            "json": index_json.exists(),
            "vectors": index_vec.exists(),
        },
        "rag_index": {
            "index": rag_index.exists(),
            "metadata": rag_meta.exists(),
        }
    }


@router.post("/rebuild-evergreen")
async def rebuild_evergreen():
    """Rebuild evergreen internal links index from data/evergreen.jsonl."""
    store = get_links_store()
    count = await store.ingest_from_jsonl()
    return {"status": "success", "count": count}


@router.post("/rebuild-rag")
async def rebuild_rag():
    """Rebuild FAISS RAG index from data/articles."""
    count = await build_rag_index()
    return {"status": "success", "chunks": count}
