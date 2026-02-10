"""Internal links (evergreen) service.

Suggests relevant internal links to weave into content.
"""

import json
from pathlib import Path
from typing import Optional

import numpy as np

from app.config import get_settings
from app.services.llm import get_embedding

settings = get_settings()

STORAGE_DIR = settings.storage_dir
DATA_DIR = settings.data_dir

INDEX_JSON = STORAGE_DIR / "evergreen_index.json"
INDEX_VEC = STORAGE_DIR / "evergreen_vectors.npy"
SOURCE_JSONL = DATA_DIR / "evergreen.jsonl"


class InternalLinkSpec:
    """Specification for an internal link suggestion."""

    def __init__(
        self,
        title: str,
        url: str,
        recommended_anchors: list[str] | None = None,
        description: str = "",
        score: float = 0.0,
    ):
        self.title = title
        self.url = url
        self.recommended_anchors = recommended_anchors or [title, title.lower()]
        self.description = description
        self.score = score

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "url": self.url,
            "recommended_anchors": self.recommended_anchors,
            "description": self.description,
            "score": self.score,
        }


class InternalLinksStore:
    """Store for internal link suggestions."""

    def __init__(self):
        self._items: list[dict] = []
        self._vectors: Optional[np.ndarray] = None
        self._loaded = False

    def _ensure_loaded(self) -> bool:
        """Load index if not already loaded."""
        if self._loaded:
            return True

        if INDEX_JSON.exists() and INDEX_VEC.exists():
            return self._load_index()

        return False

    def _load_index(self) -> bool:
        """Load pre-built index."""
        try:
            with open(INDEX_JSON, "r", encoding="utf-8") as f:
                self._items = json.load(f)
            self._vectors = np.load(str(INDEX_VEC))
            self._loaded = True
            return True
        except Exception as e:
            print(f"Failed to load internal links index: {e}")
            return False

    async def ingest_from_jsonl(self, path: Path | str | None = None) -> int:
        """Ingest internal links from JSONL file and build index.

        JSONL format: {"id": "...", "title": "...", "url": "...", "summary": "..."}
        """
        source = Path(path) if path else SOURCE_JSONL

        if not source.exists():
            return 0

        items = []
        with open(source, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                items.append({
                    "id": rec.get("id", rec.get("url", "")),
                    "title": rec.get("title", ""),
                    "url": rec.get("url", ""),
                    "summary": rec.get("summary", ""),
                    "recommended_anchors": rec.get("recommended_anchors") or rec.get("anchors") or [],
                })

        if not items:
            return 0

        # Build embeddings
        from app.services.llm import get_embeddings_batch

        docs = [f"{r['title']} — {r['summary']}" for r in items]
        vectors = await get_embeddings_batch(docs)
        vectors_arr = np.array(vectors, dtype=np.float32)

        # Normalize
        norms = np.linalg.norm(vectors_arr, axis=1, keepdims=True)
        vectors_arr = vectors_arr / (norms + 1e-12)

        # Save
        STORAGE_DIR.mkdir(parents=True, exist_ok=True)
        with open(INDEX_JSON, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
        np.save(str(INDEX_VEC), vectors_arr)

        self._items = items
        self._vectors = vectors_arr
        self._loaded = True

        return len(items)

    async def suggest_links(
        self,
        title: str,
        context: list[str] | None = None,
        k: int = 3,
    ) -> list[InternalLinkSpec]:
        """Suggest internal links based on title and context."""
        if not self._ensure_loaded():
            return []

        if self._vectors is None or len(self._items) == 0:
            return []

        # Build query
        query_parts = [title or ""]
        if context:
            query_parts.extend(context[:3])  # Limit context
        query = " | ".join(query_parts)

        # Get query embedding
        query_vec = await get_embedding(query)
        query_arr = np.array([query_vec], dtype=np.float32)

        # Normalize
        norm = np.linalg.norm(query_arr)
        if norm > 0:
            query_arr = query_arr / norm

        # Compute similarities
        sims = self._vectors @ query_arr.T
        sims = sims.flatten()

        # Get top-k
        top_idx = np.argsort(-sims)[:k]

        results = []
        for idx in top_idx:
            if idx >= len(self._items):
                continue
            item = self._items[idx]
            results.append(InternalLinkSpec(
                title=item["title"],
                url=item["url"],
                recommended_anchors=item.get("recommended_anchors") or [item["title"], item["title"].lower()],
                description=item.get("summary", ""),
                score=float(sims[idx]),
            ))

        return results


# Global instance
_links_store: Optional[InternalLinksStore] = None


def get_links_store() -> InternalLinksStore:
    """Get or create the global internal links store."""
    global _links_store
    if _links_store is None:
        _links_store = InternalLinksStore()
    return _links_store


async def suggest_links_for_section(
    title: str,
    must_include: list[str] | None = None,
    k: int = 3,
) -> list[InternalLinkSpec]:
    """Convenience function for suggesting links."""
    store = get_links_store()
    return await store.suggest_links(title, context=must_include, k=k)


def format_links_markdown(links: list[InternalLinkSpec], brand: str = "") -> str:
    """Format link suggestions as markdown bullets for prompts.

    Also includes generic contextual link suggestions that the LLM
    can use with placeholder URLs (href="#") for helpful references.
    """
    lines = []

    # Add actual evergreen links if available
    if links:
        for link in links:
            anchor_hint = ""
            if link.recommended_anchors:
                anchor_hint = f" — anchors: {', '.join(link.recommended_anchors[:3])}"
            display = f"[{link.title}]({link.url})" if link.url else link.title
            lines.append(f"- {display}{anchor_hint}")

    # Add generic contextual link suggestions (LLM can use href="#" for these)
    brand_name = brand or "BRAND"
    contextual_suggestions = [
        f"- [{brand_name} sign-up guide](#) — use when explaining registration steps",
        "- [how bonus bets work](#) — use when explaining bonus bet mechanics",
        f"- [check your state's {brand_name} terms](#) — use when mentioning state-specific rules",
    ]
    lines.extend(contextual_suggestions)

    return "\n".join(lines)
