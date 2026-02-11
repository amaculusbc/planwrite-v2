"""Admin maintenance endpoints."""

import hashlib
import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.services.bam_offers import DEFAULT_PROPERTY, PROPERTIES
from app.services.internal_links import get_links_store
from app.services.rag_builder import build_rag_index
from app.services.usage_tracking import list_usage_events, usage_events_csv, usage_summary

router = APIRouter(prefix="/api/admin", tags=["admin"])
settings = get_settings()


class InternalLinkUpsert(BaseModel):
    title: str
    url: str
    description: str = ""
    recommended_anchors: list[str] = Field(default_factory=list)
    operator: str = ""
    always_include: bool = False


def _normalize_property_key_or_400(property_key: str | None) -> str:
    key = (property_key or DEFAULT_PROPERTY).strip().lower()
    if key not in PROPERTIES:
        raise HTTPException(status_code=400, detail=f"Unknown property: {property_key}")
    return key


def _property_source_path(property_key: str) -> Path:
    source = settings.data_dir / f"evergreen_{property_key}.jsonl"
    legacy = settings.data_dir / "evergreen.jsonl"
    source.parent.mkdir(parents=True, exist_ok=True)

    if not source.exists():
        if property_key == DEFAULT_PROPERTY and legacy.exists():
            source.write_text(legacy.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            source.touch()
    return source


def _record_id(title: str, url: str) -> str:
    raw = f"{title.strip().lower()}|{url.strip().lower()}"
    return f"manual::{hashlib.md5(raw.encode()).hexdigest()[:16]}"


def _normalize_source_record(rec: dict) -> dict | None:
    title = str(rec.get("title") or "").strip()
    url = str(rec.get("url") or "").strip()
    if not title or not url:
        return None

    anchors_raw = rec.get("recommended_anchors") or rec.get("anchors") or []
    anchors = [str(a).strip() for a in anchors_raw if str(a).strip()]
    if not anchors:
        anchors = [title]

    description = str(rec.get("summary") or rec.get("description") or "").strip()
    operator = str(rec.get("operator") or "").strip().lower()
    rec_id = str(rec.get("id") or _record_id(title, url)).strip()

    return {
        "id": rec_id,
        "title": title,
        "url": url,
        "summary": description,
        "recommended_anchors": anchors,
        "operator": operator,
        "always_include": bool(rec.get("always_include", False)),
    }


def _read_source_records(source: Path) -> list[dict]:
    records: list[dict] = []
    if not source.exists():
        return records

    with open(source, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except Exception:
                continue
            normalized = _normalize_source_record(parsed)
            if normalized:
                records.append(normalized)
    return records


def _write_source_records(source: Path, records: list[dict]) -> None:
    with open(source, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


@router.get("/status")
async def admin_status():
    """Get status of internal indexes."""
    index_json = settings.storage_dir / "evergreen_index.json"
    index_vec = settings.storage_dir / "evergreen_vectors.npy"
    rag_index = settings.storage_dir / "faiss_index" / "index.faiss"
    rag_meta = settings.storage_dir / "faiss_index" / "metadata.jsonl"
    per_property: dict[str, dict[str, bool]] = {}
    for property_key in PROPERTIES:
        per_property[property_key] = {
            "json": (settings.storage_dir / f"evergreen_index_{property_key}.json").exists(),
            "vectors": (settings.storage_dir / f"evergreen_vectors_{property_key}.npy").exists(),
            "source": (settings.data_dir / f"evergreen_{property_key}.jsonl").exists(),
        }
    return {
        "evergreen_index": {
            "json": index_json.exists(),
            "vectors": index_vec.exists(),
        },
        "evergreen_by_property": per_property,
        "rag_index": {
            "index": rag_index.exists(),
            "metadata": rag_meta.exists(),
        }
    }


@router.post("/rebuild-evergreen")
async def rebuild_evergreen(
    property_key: str = Query(DEFAULT_PROPERTY, alias="property"),
):
    """Rebuild evergreen internal links index for a specific property."""
    store = get_links_store(property_key=property_key)
    count = await store.ingest_from_jsonl()
    return {"status": "success", "property": property_key, "count": count}


@router.post("/rebuild-evergreen/{property_key}")
async def rebuild_evergreen_property(property_key: str):
    """Rebuild evergreen internal links index for a specific property."""
    store = get_links_store(property_key=property_key)
    count = await store.ingest_from_jsonl()
    return {"status": "success", "property": property_key, "count": count}


@router.post("/rebuild-evergreen-all")
async def rebuild_evergreen_all(
    include_scores_and_odds: bool = Query(False),
):
    """Rebuild evergreen internal links indexes for all configured properties."""
    counts: dict[str, int] = {}
    for property_key in PROPERTIES:
        if property_key == "scores_and_odds" and not include_scores_and_odds:
            continue
        store = get_links_store(property_key=property_key)
        counts[property_key] = await store.ingest_from_jsonl()
    return {"status": "success", "counts": counts}


@router.get("/internal-links")
async def list_internal_links(
    property_key: str = Query(DEFAULT_PROPERTY, alias="property"),
):
    """List editable internal-link source rows for a property."""
    property_key = _normalize_property_key_or_400(property_key)
    source = _property_source_path(property_key)
    records = _read_source_records(source)
    records.sort(key=lambda r: (str(r.get("title", "")).lower(), str(r.get("url", "")).lower()))
    return {
        "status": "success",
        "property": property_key,
        "source_path": str(source),
        "count": len(records),
        "links": records,
    }


@router.post("/internal-links")
async def upsert_internal_link(
    payload: InternalLinkUpsert,
    property_key: str = Query(DEFAULT_PROPERTY, alias="property"),
):
    """Add/update one internal-link source row and rebuild that property index.

    When `always_include` is true, the link is guaranteed to be inserted in every
    generated article for that property.
    """
    property_key = _normalize_property_key_or_400(property_key)
    source = _property_source_path(property_key)
    records = _read_source_records(source)

    normalized = _normalize_source_record({
        "id": _record_id(payload.title, payload.url),
        "title": payload.title,
        "url": payload.url,
        "summary": payload.description,
        "recommended_anchors": payload.recommended_anchors,
        "operator": payload.operator,
        "always_include": payload.always_include,
    })
    if not normalized:
        raise HTTPException(status_code=400, detail="Both title and url are required")

    existing_idx = next(
        (i for i, rec in enumerate(records) if str(rec.get("url", "")).strip().lower() == normalized["url"].lower()),
        -1,
    )
    mode = "created"
    if existing_idx >= 0:
        records[existing_idx] = normalized
        mode = "updated"
    else:
        records.append(normalized)

    _write_source_records(source, records)
    store = get_links_store(property_key=property_key)
    count = await store.ingest_from_jsonl(path=source)

    return {
        "status": "success",
        "mode": mode,
        "property": property_key,
        "link": normalized,
        "count": count,
    }


@router.delete("/internal-links")
async def delete_internal_link(
    property_key: str = Query(DEFAULT_PROPERTY, alias="property"),
    link_id: str | None = Query(None, alias="id"),
    url: str | None = Query(None),
):
    """Delete one internal-link source row by id or url and rebuild that property index."""
    property_key = _normalize_property_key_or_400(property_key)
    if not link_id and not url:
        raise HTTPException(status_code=400, detail="Provide id or url")

    source = _property_source_path(property_key)
    records = _read_source_records(source)
    before = len(records)
    url_lc = (url or "").strip().lower()
    id_lc = (link_id or "").strip().lower()
    records = [
        rec for rec in records
        if not (
            (id_lc and str(rec.get("id", "")).strip().lower() == id_lc)
            or (url_lc and str(rec.get("url", "")).strip().lower() == url_lc)
        )
    ]
    removed = before - len(records)
    if removed == 0:
        raise HTTPException(status_code=404, detail="Link not found")

    _write_source_records(source, records)
    store = get_links_store(property_key=property_key)
    count = await store.ingest_from_jsonl(path=source)

    return {
        "status": "success",
        "property": property_key,
        "removed": removed,
        "count": count,
    }


@router.post("/rebuild-rag")
async def rebuild_rag():
    """Rebuild FAISS RAG index from data/articles."""
    count = await build_rag_index()
    return {"status": "success", "chunks": count}


@router.get("/usage/events")
async def get_usage_events(
    days: int = Query(30, ge=1, le=3650),
    limit: int = Query(200, ge=1, le=5000),
    username: str | None = Query(None),
    event_type: str | None = Query(None),
    db: AsyncSession = Depends(get_db),  # noqa: ARG001
):
    """List persisted usage events."""
    events = await list_usage_events(
        days=days,
        limit=limit,
        username=username,
        event_type=event_type,
    )
    return {
        "status": "success",
        "days": days,
        "count": len(events),
        "events": events,
    }


@router.get("/usage/summary")
async def get_usage_summary(
    days: int = Query(30, ge=1, le=3650),
    db: AsyncSession = Depends(get_db),  # noqa: ARG001
):
    """Return aggregated persisted usage metrics."""
    summary = await usage_summary(days=days)
    return {"status": "success", **summary}


@router.get("/usage/export")
async def export_usage_events(
    days: int = Query(30, ge=1, le=3650),
    limit: int = Query(5000, ge=1, le=20000),
    username: str | None = Query(None),
    event_type: str | None = Query(None),
    db: AsyncSession = Depends(get_db),  # noqa: ARG001
):
    """Export persisted usage events as CSV."""
    csv_text = await usage_events_csv(
        days=days,
        limit=limit,
        username=username,
        event_type=event_type,
    )
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=usage-events.csv"},
    )
