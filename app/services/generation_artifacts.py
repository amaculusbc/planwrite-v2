"""Persist generation-stage artifacts for outline/draft/validation runs."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.config import get_settings
from app.services.offer_parsing import (
    extract_bonus_amount,
    extract_excluded_states_from_terms,
    extract_offer_amount_details,
    extract_states_from_terms,
    parse_states,
)
from app.services.operator_profile import get_content_mode_offer


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slugify(value: str, max_len: int = 60) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower())
    text = text.strip("-")
    if not text:
        return "run"
    return text[:max_len].rstrip("-") or "run"


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _storage_root() -> Path:
    return get_settings().storage_dir / "generation_runs"


def _relative_storage_path(path: Path) -> str:
    settings = get_settings()
    try:
        return str(path.relative_to(settings.storage_dir))
    except Exception:
        return str(path)


def _summarize_offer(offer: dict[str, Any] | None) -> dict[str, Any]:
    offer = dict(offer or {})
    offer_text = str(offer.get("offer_text") or offer.get("affiliate_offer") or "").strip()
    terms = str(offer.get("terms") or "").strip()
    amount_details = extract_offer_amount_details(offer_text)
    return {
        "id": offer.get("id") or offer.get("offer_id"),
        "brand": offer.get("brand"),
        "bonus_code": offer.get("bonus_code"),
        "offer_text": offer_text,
        "reward_amount": offer.get("bonus_amount") or offer.get("reward_amount") or amount_details.get("reward_amount") or extract_bonus_amount(offer_text),
        "reward_label": offer.get("reward_label") or amount_details.get("reward_label"),
        "qualifying_amount": offer.get("qualifying_amount") or amount_details.get("qualifying_amount"),
        "qualifying_action": offer.get("qualifying_action") or amount_details.get("qualifying_action"),
        "states": parse_states(offer.get("states_list") or offer.get("states")),
        "states_from_terms": extract_states_from_terms(terms),
        "excluded_states": extract_excluded_states_from_terms(terms),
        "terms_present": bool(terms),
        "content_mode": get_content_mode_offer(offer),
    }


def build_source_facts(
    *,
    keyword: str,
    title: str,
    state: str,
    offer_property: str | None,
    offer: dict[str, Any] | None,
    alt_offers: list[dict[str, Any]] | None,
    event_context: str = "",
    article_date: str = "",
    bet_example: str = "",
    bet_example_data: dict[str, Any] | None = None,
    game_context_data: dict[str, Any] | None = None,
    competitor_urls: list[str] | None = None,
    competitor_context: str = "",
    article_preferences: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the source-of-truth facts bundle for a generation run."""
    prefs = dict(article_preferences or {})
    return {
        "keyword": keyword,
        "title": title,
        "state": state,
        "offer_property": offer_property or "action_network",
        "primary_offer": _summarize_offer(offer),
        "alt_offers": [_summarize_offer(item) for item in (alt_offers or []) if item],
        "event": {
            "event_context": event_context,
            "article_date": article_date,
            "bet_example": bet_example,
            "bet_example_data": _json_safe(bet_example_data or {}),
            "event_type": str((game_context_data or {}).get("event_type") or ""),
            "custom_event": str((game_context_data or {}).get("custom_event") or ""),
            "sport": str((game_context_data or {}).get("sport") or ""),
            "away_team": str((game_context_data or {}).get("away_team") or ""),
            "home_team": str((game_context_data or {}).get("home_team") or ""),
            "start_time": str((game_context_data or {}).get("start_time") or ""),
            "network": str((game_context_data or {}).get("network") or ""),
            "headline": str((game_context_data or {}).get("headline") or ""),
        },
        "competitors": {
            "urls": [str(url).strip() for url in (competitor_urls or []) if str(url).strip()],
            "context_excerpt": competitor_context[:1200],
            "context_length": len(competitor_context or ""),
        },
        "editor_direction": {
            "secondary_keywords": [str(x).strip() for x in prefs.get("secondary_keywords", []) if str(x).strip()],
            "preferred_internal_urls": [str(x).strip() for x in prefs.get("preferred_internal_urls", []) if str(x).strip()],
            "structure_notes": str(prefs.get("structure_notes") or "").strip(),
            "section_count": prefs.get("section_count"),
            "allow_h3": bool(prefs.get("allow_h3", False)),
            "include_daily_promos": bool(prefs.get("include_daily_promos", False)),
            "include_bullets": bool(prefs.get("include_bullets", False)),
            "include_table": bool(prefs.get("include_table", False)),
            "enforce_active_voice": prefs.get("enforce_active_voice", True) is not False,
        },
    }


@dataclass
class GenerationArtifactRun:
    """Filesystem-backed artifact recorder for one generation run."""

    run_id: str
    run_dir: Path
    manifest_path: Path
    manifest: dict[str, Any] = field(default_factory=dict)

    def write_stage(self, stage: str, payload: Any, file_name: str | None = None) -> str:
        safe_name = file_name or f"{stage}.json"
        target = self.run_dir / safe_name
        target.write_text(json.dumps(_json_safe(payload), indent=2, ensure_ascii=False), encoding="utf-8")
        self.manifest.setdefault("artifacts", []).append({
            "stage": stage,
            "file": safe_name,
            "path": _relative_storage_path(target),
            "created_at": _utc_now_iso(),
        })
        self._flush_manifest()
        return _relative_storage_path(target)

    def set_meta(self, **kwargs: Any) -> None:
        self.manifest.update(_json_safe(kwargs))
        self._flush_manifest()

    def _flush_manifest(self) -> None:
        self.manifest_path.write_text(json.dumps(_json_safe(self.manifest), indent=2, ensure_ascii=False), encoding="utf-8")

    def response_meta(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "artifact_manifest": _relative_storage_path(self.manifest_path),
            "artifact_dir": _relative_storage_path(self.run_dir),
        }


def create_generation_run(
    *,
    keyword: str,
    title: str,
    state: str,
    offer_property: str | None,
    run_id: str | None = None,
) -> GenerationArtifactRun:
    """Create or reopen an artifact run directory."""
    base = _storage_root()
    base.mkdir(parents=True, exist_ok=True)

    active_run_id = str(run_id or uuid4().hex).strip()
    if run_id:
        existing = next(base.glob(f"*/{active_run_id}_*/manifest.json"), None)
        if existing:
            try:
                manifest = json.loads(existing.read_text(encoding="utf-8"))
            except Exception:
                manifest = {
                    "run_id": active_run_id,
                    "keyword": keyword,
                    "title": title,
                    "state": state,
                    "offer_property": offer_property or "action_network",
                    "created_at": _utc_now_iso(),
                    "artifacts": [],
                }
            recorder = GenerationArtifactRun(
                run_id=active_run_id,
                run_dir=existing.parent,
                manifest_path=existing,
                manifest=manifest,
            )
            recorder._flush_manifest()
            return recorder

    date_dir = base / datetime.now(timezone.utc).strftime("%Y-%m-%d")
    date_dir.mkdir(parents=True, exist_ok=True)
    run_dir = date_dir / f"{active_run_id}_{_slugify(keyword)}_{_slugify(title, max_len=40)}"
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = run_dir / "manifest.json"
    manifest = {
        "run_id": active_run_id,
        "keyword": keyword,
        "title": title,
        "state": state,
        "offer_property": offer_property or "action_network",
        "created_at": _utc_now_iso(),
        "artifacts": [],
    }
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    recorder = GenerationArtifactRun(
        run_id=active_run_id,
        run_dir=run_dir,
        manifest_path=manifest_path,
        manifest=manifest,
    )
    recorder._flush_manifest()
    return recorder


def load_generation_run(run_id: str) -> dict[str, Any] | None:
    """Load an existing run manifest by run id."""
    base = _storage_root()
    if not base.exists():
        return None
    for manifest_path in base.glob(f"*/{run_id}_*/manifest.json"):
        try:
            return json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None
