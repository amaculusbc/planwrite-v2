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
    market: str = "US",
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
        "market": market,
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

    def record_token_usage(self, usage: dict[str, Any]) -> None:
        """Accumulate LLM token usage + cost into the manifest.

        Outline and draft are separate calls that may share a run_id; adding (not replacing)
        keeps the run's total honest across both.
        """
        if not usage:
            return
        existing = self.manifest.get("token_usage") or {}
        merged = {
            "calls": int(existing.get("calls", 0)) + int(usage.get("calls", 0) or 0),
            "prompt_tokens": int(existing.get("prompt_tokens", 0)) + int(usage.get("prompt_tokens", 0) or 0),
            "cached_input_tokens": int(existing.get("cached_input_tokens", 0)) + int(usage.get("cached_input_tokens", 0) or 0),
            "completion_tokens": int(existing.get("completion_tokens", 0)) + int(usage.get("completion_tokens", 0) or 0),
            "total_tokens": int(existing.get("total_tokens", 0)) + int(usage.get("total_tokens", 0) or 0),
            "cost_usd": round(float(existing.get("cost_usd", 0.0)) + float(usage.get("cost_usd", 0.0) or 0.0), 6),
        }
        self.manifest["token_usage"] = merged
        self._flush_manifest()

    def _flush_manifest(self) -> None:
        self.manifest_path.write_text(json.dumps(_json_safe(self.manifest), indent=2, ensure_ascii=False), encoding="utf-8")

    def response_meta(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "artifact_manifest": _relative_storage_path(self.manifest_path),
            "artifact_dir": _relative_storage_path(self.run_dir),
        }


def list_same_day_run_titles(
    offer_property: str | None,
    exclude_run_id: str = "",
    limit: int = 8,
) -> list[str]:
    """Titles of today's runs for a property, for cross-article cannibalism avoidance."""
    base = _storage_root()
    date_dir = base / datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if not date_dir.exists():
        return []
    target = str(offer_property or "action_network").strip().lower()
    titles: list[str] = []
    seen: set[str] = set()
    for manifest_path in sorted(date_dir.glob("*/manifest.json")):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if str(manifest.get("offer_property") or "").strip().lower() != target:
            continue
        if exclude_run_id and str(manifest.get("run_id") or "") == exclude_run_id:
            continue
        title = str(manifest.get("title") or "").strip()
        key = title.lower()
        if not title or key in seen:
            continue
        seen.add(key)
        titles.append(title)
        if len(titles) >= limit:
            break
    return titles


def create_generation_run(
    *,
    keyword: str,
    title: str,
    state: str,
    market: str = "US",
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
                    "market": market,
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
        "market": market,
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


def generation_run_stats() -> dict[str, Any]:
    """Aggregate every persisted run manifest by property, month, and state.

    The articles table only holds saved articles (writers rarely save), and usage events
    record the request path but not the offer property. The run manifests on the storage
    volume are the one place per-generation property/keyword/date is captured, so this walks
    them for real per-property and monthly generation counts.
    """
    base = _storage_root()
    total = 0
    by_property: dict[str, int] = {}
    by_month: dict[str, int] = {}
    by_state: dict[str, int] = {}
    property_by_month: dict[str, dict[str, int]] = {}
    keyword_counts: dict[str, int] = {}
    by_brand: dict[str, int] = {}
    by_content_mode: dict[str, int] = {}
    by_sport: dict[str, int] = {}
    words_total = 0
    runs_with_words = 0
    # Token/cost accumulators (only runs that carry token_usage — i.e. generated after tracking
    # was added — contribute; runs_with_cost reports how many that is).
    runs_with_cost = 0
    tok_total = 0
    tok_prompt = 0
    tok_completion = 0
    cost_total = 0.0
    cost_by_property: dict[str, float] = {}
    cost_by_month: dict[str, float] = {}
    if not base.exists():
        return {"total_runs": 0, "by_property": {}, "by_month": {}, "by_state": {},
                "property_by_month": {}, "top_keywords": [], "cost": {"runs_with_cost": 0}}

    for manifest_path in base.glob("*/*/manifest.json"):
        try:
            m = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        total += 1
        prop = str(m.get("offer_property") or "unknown")
        created = str(m.get("created_at") or "")
        month = created[:7] if len(created) >= 7 else "unknown"
        state = str(m.get("state") or "unknown")
        keyword = str(m.get("keyword") or "").strip().lower()

        by_property[prop] = by_property.get(prop, 0) + 1
        by_month[month] = by_month.get(month, 0) + 1
        by_state[state] = by_state.get(state, 0) + 1
        property_by_month.setdefault(month, {})[prop] = property_by_month.setdefault(month, {}).get(prop, 0) + 1
        if keyword:
            keyword_counts[keyword] = keyword_counts.get(keyword, 0) + 1

        # Tier-1 production fields (present only on runs generated after tracking was added).
        brand = str(m.get("brand") or "").strip()
        if brand:
            by_brand[brand] = by_brand.get(brand, 0) + 1
        cmode = str(m.get("content_mode") or "").strip()
        if cmode:
            by_content_mode[cmode] = by_content_mode.get(cmode, 0) + 1
        msport = str(m.get("sport") or "").strip()
        if msport:
            by_sport[msport] = by_sport.get(msport, 0) + 1
        wc = m.get("word_count")
        if isinstance(wc, (int, float)) and wc > 0:
            words_total += int(wc)
            runs_with_words += 1

        tu = m.get("token_usage") or {}
        if tu:
            runs_with_cost += 1
            tok_total += int(tu.get("total_tokens", 0) or 0)
            tok_prompt += int(tu.get("prompt_tokens", 0) or 0)
            tok_completion += int(tu.get("completion_tokens", 0) or 0)
            c = float(tu.get("cost_usd", 0.0) or 0.0)
            cost_total += c
            cost_by_property[prop] = round(cost_by_property.get(prop, 0.0) + c, 6)
            cost_by_month[month] = round(cost_by_month.get(month, 0.0) + c, 6)

    top_keywords = sorted(keyword_counts.items(), key=lambda kv: -kv[1])[:20]
    avg_cost = round(cost_total / runs_with_cost, 6) if runs_with_cost else 0.0
    avg_tokens = round(tok_total / runs_with_cost) if runs_with_cost else 0
    return {
        "total_runs": total,
        "by_property": dict(sorted(by_property.items(), key=lambda kv: -kv[1])),
        "by_month": dict(sorted(by_month.items())),
        "by_state": dict(sorted(by_state.items(), key=lambda kv: -kv[1])),
        "property_by_month": {mo: property_by_month[mo] for mo in sorted(property_by_month)},
        "top_keywords": [{"keyword": k, "count": c} for k, c in top_keywords],
        "by_brand": dict(sorted(by_brand.items(), key=lambda kv: -kv[1])),
        "by_content_mode": dict(sorted(by_content_mode.items(), key=lambda kv: -kv[1])),
        "by_sport": dict(sorted(by_sport.items(), key=lambda kv: -kv[1])),
        "avg_word_count": round(words_total / runs_with_words) if runs_with_words else 0,
        "runs_with_tracking": runs_with_words,
        "cost": {
            "runs_with_cost": runs_with_cost,
            "total_cost_usd": round(cost_total, 4),
            "avg_cost_per_article_usd": avg_cost,
            "total_tokens": tok_total,
            "avg_tokens_per_article": avg_tokens,
            "avg_input_tokens": round(tok_prompt / runs_with_cost) if runs_with_cost else 0,
            "avg_output_tokens": round(tok_completion / runs_with_cost) if runs_with_cost else 0,
            "cost_by_property_usd": dict(sorted(cost_by_property.items(), key=lambda kv: -kv[1])),
            "cost_by_month_usd": dict(sorted(cost_by_month.items())),
        },
    }


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
