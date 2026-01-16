"""RAG (Retrieval-Augmented Generation) service using FAISS.

Upgraded from v1's NumPy memmap to FAISS for faster search.
"""

import json
import re
from pathlib import Path
from typing import Optional

import numpy as np

from app.config import get_settings
from app.services.llm import get_embedding, get_embeddings_batch

settings = get_settings()

# Index paths
DATA_DIR = settings.data_dir
STORAGE_DIR = settings.storage_dir
FAISS_DIR = STORAGE_DIR / "faiss_index"

# Legacy paths (for migration)
LEGACY_INDEX_BIN = DATA_DIR / "articles_index.bin"
LEGACY_INDEX_META = DATA_DIR / "articles_meta.jsonl"
LEGACY_INDEX_SHAPE = DATA_DIR / "articles_index.shape.json"

# FAISS index files
FAISS_INDEX_FILE = FAISS_DIR / "index.faiss"
FAISS_META_FILE = FAISS_DIR / "metadata.jsonl"


class RAGStore:
    """Vector store for article retrieval."""

    def __init__(self):
        self._index = None
        self._metadata: list[dict] = []
        self._loaded = False

    def _ensure_loaded(self) -> bool:
        """Load index if not already loaded."""
        if self._loaded:
            return True

        # Try FAISS first
        if FAISS_INDEX_FILE.exists() and FAISS_META_FILE.exists():
            return self._load_faiss()

        # Fall back to legacy format
        if LEGACY_INDEX_BIN.exists() and LEGACY_INDEX_META.exists():
            return self._load_legacy()

        return False

    def _load_faiss(self) -> bool:
        """Load FAISS index."""
        try:
            import faiss

            self._index = faiss.read_index(str(FAISS_INDEX_FILE))
            self._metadata = []
            with open(FAISS_META_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        self._metadata.append(json.loads(line))
            self._loaded = True
            return True
        except Exception as e:
            print(f"Failed to load FAISS index: {e}")
            return False

    def _load_legacy(self) -> bool:
        """Load legacy NumPy memmap index and convert to in-memory."""
        try:
            dim = json.loads(LEGACY_INDEX_SHAPE.read_text())["dim"]
            nbytes = LEGACY_INDEX_BIN.stat().st_size
            total = nbytes // (4 * dim)

            # Load vectors
            vectors = np.memmap(
                LEGACY_INDEX_BIN,
                dtype=np.float32,
                mode="r",
                shape=(total, dim),
            )

            # Load metadata
            self._metadata = []
            with open(LEGACY_INDEX_META, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        self._metadata.append(json.loads(line))

            # Create FAISS index from legacy data
            import faiss

            self._index = faiss.IndexFlatIP(dim)  # Inner product (cosine for normalized)
            self._index.add(np.array(vectors))

            self._loaded = True
            return True
        except Exception as e:
            print(f"Failed to load legacy index: {e}")
            return False

    async def search(
        self,
        query: str,
        top_k: int = 8,
        min_score: float = 0.0,
    ) -> list[dict]:
        """Search for relevant article chunks.

        Returns list of dicts: {score, path, preview, snippet}
        """
        if not self._ensure_loaded():
            return []

        if self._index is None or not self._metadata:
            return []

        # Get query embedding
        query_vec = await get_embedding(query)
        query_arr = np.array([query_vec], dtype=np.float32)

        # Normalize for cosine similarity
        norm = np.linalg.norm(query_arr)
        if norm > 0:
            query_arr = query_arr / norm

        # Search
        scores, indices = self._index.search(query_arr, top_k)

        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0 or idx >= len(self._metadata):
                continue
            if score < min_score:
                continue

            meta = self._metadata[idx]
            results.append({
                "score": float(score),
                "path": meta.get("path", ""),
                "preview": meta.get("preview", ""),
                "snippet": meta.get("preview", ""),
                "source": Path(meta.get("path", "")).name if meta.get("path") else "",
            })

        return results

    async def query_articles(
        self,
        query: str,
        k: int = 5,
        snippet_chars: int = 500,
    ) -> list[dict]:
        """Query articles with extended snippets.

        Compatibility layer matching v1's query_articles function.
        """
        hits = await self.search(query, top_k=k)
        results = []

        for hit in hits:
            snippet = hit.get("preview", "")

            # Extend snippet if needed
            if snippet_chars > len(snippet) and hit.get("path"):
                try:
                    path = Path(hit["path"])
                    if path.exists():
                        raw = path.read_text(encoding="utf-8", errors="ignore")
                        body = self._strip_front_matter(raw)
                        body = re.sub(r"\s+", " ", body).strip()
                        snippet = body[:snippet_chars]
                except Exception:
                    pass

            results.append({
                "score": hit["score"],
                "path": hit["path"],
                "snippet": snippet,
                "source": hit["source"],
            })

        return results

    @staticmethod
    def _strip_front_matter(text: str) -> str:
        """Remove YAML front matter from markdown."""
        if text.startswith("---"):
            end = text.find("\n---", 3)
            if end != -1:
                return text[end + 4:]
        return text


# Global instance
_rag_store: Optional[RAGStore] = None


def get_rag_store() -> RAGStore:
    """Get or create the global RAG store instance."""
    global _rag_store
    if _rag_store is None:
        _rag_store = RAGStore()
    return _rag_store


async def search_articles(query: str, top_k: int = 8) -> list[dict]:
    """Convenience function for searching articles."""
    store = get_rag_store()
    return await store.search(query, top_k=top_k)


async def query_articles(query: str, k: int = 5, snippet_chars: int = 500) -> list[dict]:
    """Convenience function matching v1 API."""
    store = get_rag_store()
    return await store.query_articles(query, k=k, snippet_chars=snippet_chars)
