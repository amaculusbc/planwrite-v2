"""Build FAISS RAG index from article corpus."""

from __future__ import annotations

from pathlib import Path
import json

import numpy as np

from app.config import get_settings
from app.services.llm import get_embeddings_batch


def _strip_front_matter(text: str) -> str:
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4:]
    return text


def _chunk_text(text: str, chunk_size: int = 1000, overlap: int = 100) -> list[tuple[int, str]]:
    text = text.strip()
    if not text:
        return []
    step = max(1, chunk_size - overlap)
    chunks: list[tuple[int, str]] = []
    for start in range(0, len(text), step):
        chunk = text[start:start + chunk_size]
        if len(chunk.strip()) < 200:
            continue
        chunks.append((start, chunk))
    return chunks


async def build_rag_index(
    source_dir: str | Path | None = None,
    *,
    chunk_size: int = 1000,
    overlap: int = 100,
    batch_size: int = 64,
) -> int:
    """Build FAISS index for article corpus.

    Returns number of chunks indexed.
    """
    settings = get_settings()
    src_dir = Path(source_dir) if source_dir else settings.data_dir / "articles"
    if not src_dir.exists():
        return 0

    files = sorted([p for p in src_dir.rglob("*") if p.is_file()])
    if not files:
        return 0

    # Prepare output paths
    faiss_dir = settings.storage_dir / "faiss_index"
    faiss_dir.mkdir(parents=True, exist_ok=True)
    index_path = faiss_dir / "index.faiss"
    meta_path = faiss_dir / "metadata.jsonl"

    docs: list[str] = []
    meta: list[dict] = []
    vectors: list[list[float]] = []

    async def _flush_batch() -> None:
        nonlocal docs, vectors
        if not docs:
            return
        embeds = await get_embeddings_batch(docs)
        vectors.extend(embeds)
        docs = []

    for path in files:
        try:
            raw = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        body = _strip_front_matter(raw)
        body = " ".join(body.split())

        for start, chunk in _chunk_text(body, chunk_size=chunk_size, overlap=overlap):
            try:
                rel_path = str(path.relative_to(settings.base_dir))
            except ValueError:
                rel_path = str(path)
            docs.append(chunk)
            meta.append({
                "path": rel_path,
                "start": start,
                "preview": chunk[:300],
            })
            if len(docs) >= batch_size:
                await _flush_batch()

    await _flush_batch()

    if not vectors:
        return 0

    import faiss  # local import to avoid optional dependency errors at startup

    vectors_arr = np.array(vectors, dtype=np.float32)
    norms = np.linalg.norm(vectors_arr, axis=1, keepdims=True)
    vectors_arr = vectors_arr / (norms + 1e-12)

    index = faiss.IndexFlatIP(vectors_arr.shape[1])
    index.add(vectors_arr)
    faiss.write_index(index, str(index_path))

    with open(meta_path, "w", encoding="utf-8") as f:
        for record in meta:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    return len(meta)
