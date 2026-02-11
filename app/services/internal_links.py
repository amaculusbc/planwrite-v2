"""Internal links service with property-specific indexes and operator filtering.

Suggests relevant evergreen links for article generation, scoped by property.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

import numpy as np

from app.config import get_settings
from app.services.bam_offers import DEFAULT_PROPERTY, PROPERTIES
from app.services.llm import get_embedding
from app.services.operator_profile import is_prediction_market_context

settings = get_settings()

STORAGE_DIR = settings.storage_dir
DATA_DIR = settings.data_dir

LEGACY_INDEX_JSON = STORAGE_DIR / "evergreen_index.json"
LEGACY_INDEX_VEC = STORAGE_DIR / "evergreen_vectors.npy"
LEGACY_SOURCE_JSONL = DATA_DIR / "evergreen.jsonl"

# Common operator aliases to prevent cross-operator link leakage in articles.
OPERATOR_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bbet365\b", re.IGNORECASE), "bet365"),
    (re.compile(r"\bfanduel\b", re.IGNORECASE), "fanduel"),
    (re.compile(r"\bdraftkings\b", re.IGNORECASE), "draftkings"),
    (re.compile(r"\bbetmgm\b", re.IGNORECASE), "betmgm"),
    (re.compile(r"\bcaesars\b", re.IGNORECASE), "caesars"),
    (re.compile(r"\bfanatics\b", re.IGNORECASE), "fanatics"),
    (re.compile(r"\bunderdog\b", re.IGNORECASE), "underdog"),
    (re.compile(r"\bsleeper\b", re.IGNORECASE), "sleeper"),
    (re.compile(r"\bkalshi\b", re.IGNORECASE), "kalshi"),
    (re.compile(r"\bnovig\b", re.IGNORECASE), "novig"),
    (re.compile(r"\bthescore\b|\bthe score\b", re.IGNORECASE), "thescore"),
    (re.compile(r"\bhard[\s-]?rock\b", re.IGNORECASE), "hard_rock"),
    (re.compile(r"\bcrypto\.com\b|\bcrypto\b", re.IGNORECASE), "crypto"),
    (re.compile(r"\bfliff\b", re.IGNORECASE), "fliff"),
    (re.compile(r"\bpolymarket\b", re.IGNORECASE), "polymarket"),
    (re.compile(r"\bdabble\b", re.IGNORECASE), "dabble"),
    (re.compile(r"\bprophetx\b|\bprophet\b", re.IGNORECASE), "prophetx"),
]

# Property-level evergreen links that must be available every generation.
REQUIRED_LINKS_BY_PROPERTY: dict[str, list[dict[str, object]]] = {
    "action_network": [
        {
            "title": "Best Betting Sites",
            "url": "https://www.actionnetwork.com/online-sports-betting/reviews",
            "recommended_anchors": ["best betting sites", "best sportsbooks"],
            "description": "Action Network's sportsbook review hub.",
        },
        {
            "title": "Legal Sports Betting States",
            "url": "https://www.actionnetwork.com/online-sports-betting",
            "recommended_anchors": ["legal sports betting states", "where betting is legal"],
            "description": "State-by-state legal sports betting coverage.",
        },
        {
            "title": "Best Sportsbooks",
            "url": "https://www.actionnetwork.com/online-sports-betting/reviews",
            "recommended_anchors": ["best sportsbooks", "top sportsbooks"],
            "description": "Best sportsbook resources and comparisons.",
        },
    ],
    "vegas_insider": [
        {
            "title": "Best Sportsbook Promos",
            "url": "https://www.vegasinsider.com/sportsbooks/bonus-codes/",
            "recommended_anchors": ["best sportsbook promos"],
            "description": "VegasInsider sportsbook promos hub.",
        },
        {
            "title": "Best Online Casinos",
            "url": "https://www.vegasinsider.com/casinos/",
            "recommended_anchors": ["best online casinos"],
            "description": "VegasInsider casino hub.",
        },
        {
            "title": "New Sweepstakes Casinos",
            "url": "https://www.vegasinsider.com/sweepstakes-casinos/new/",
            "recommended_anchors": ["new sweepstakes casinos"],
            "description": "VegasInsider sweepstakes coverage.",
        },
    ],
    "sportshandle": [
        {
            "title": "Best Betting Sites",
            "url": "https://sportshandle.com/best-sports-betting-sites/",
            "recommended_anchors": ["best betting sites"],
            "description": "SportsHandle best sites page.",
        },
        {
            "title": "Best Sports Betting Apps",
            "url": "https://sportshandle.com/mobile-sportsbooks/",
            "recommended_anchors": ["best sports betting apps"],
            "description": "SportsHandle app coverage.",
        },
    ],
    "rotogrinders": [
        {
            "title": "Best Prediction Market Apps",
            "url": "https://rotogrinders.com/best-prediction-market-apps",
            "recommended_anchors": ["best prediction market apps"],
            "description": "RotoGrinders prediction market guide.",
        },
        {
            "title": "Best DFS Apps",
            "url": "https://rotogrinders.com/fantasy",
            "recommended_anchors": ["best dfs apps", "dfs apps"],
            "description": "RotoGrinders DFS resources.",
        },
    ],
    "fantasy_labs": [
        {
            "title": "NFL DFS",
            "url": "https://www.fantasylabs.com/daily-fantasy-football/",
            "recommended_anchors": ["nfl dfs", "daily fantasy football"],
            "description": "FantasyLabs NFL DFS hub.",
        },
        {
            "title": "Best DFS Apps",
            "url": "https://www.fantasylabs.com/articles/top-dfs-sites/",
            "recommended_anchors": ["best dfs apps", "top dfs sites"],
            "description": "FantasyLabs DFS picks and resources.",
        },
    ],
}


def _normalize_property_key(property_key: str | None) -> str:
    key = (property_key or DEFAULT_PROPERTY).strip().lower()
    if key not in PROPERTIES:
        return DEFAULT_PROPERTY
    return key


def _property_index_json(property_key: str) -> Path:
    return STORAGE_DIR / f"evergreen_index_{property_key}.json"


def _property_index_vec(property_key: str) -> Path:
    return STORAGE_DIR / f"evergreen_vectors_{property_key}.npy"


def _property_source_jsonl(property_key: str) -> Path:
    return DATA_DIR / f"evergreen_{property_key}.jsonl"


def _normalize_operator(text: str) -> str:
    clean = (text or "").strip().lower()
    if not clean:
        return ""
    for pattern, value in OPERATOR_PATTERNS:
        if pattern.search(clean):
            return value
    return ""


def _link_operator(item: dict) -> str:
    explicit = str(item.get("operator") or "").strip().lower()
    if explicit:
        return explicit
    raw = " ".join([
        str(item.get("title", "")),
        str(item.get("url", "")),
        " ".join(str(x) for x in item.get("recommended_anchors", [])[:5]),
    ])
    return _normalize_operator(raw)


class InternalLinkSpec:
    """Specification for an internal link suggestion."""

    def __init__(
        self,
        title: str,
        url: str,
        recommended_anchors: list[str] | None = None,
        description: str = "",
        score: float = 0.0,
        operator: str = "",
        always_include: bool = False,
    ):
        self.title = title
        self.url = url
        self.recommended_anchors = recommended_anchors or [title, title.lower()]
        self.description = description
        self.score = score
        self.operator = operator
        self.always_include = always_include

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "url": self.url,
            "recommended_anchors": self.recommended_anchors,
            "description": self.description,
            "score": self.score,
            "operator": self.operator,
            "always_include": self.always_include,
        }


class InternalLinksStore:
    """Store for internal link suggestions scoped to a property."""

    def __init__(self, property_key: str | None = None):
        self.property_key = _normalize_property_key(property_key)
        self._items: list[dict] = []
        self._vectors: Optional[np.ndarray] = None
        self._loaded = False

    def _read_index_items(self) -> list[dict]:
        """Read persisted index items without loading vectors."""
        candidates = [self.index_json_path]
        if self.property_key == DEFAULT_PROPERTY:
            candidates.append(LEGACY_INDEX_JSON)

        for path in candidates:
            if not path.exists():
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    parsed = json.load(f)
                if isinstance(parsed, list):
                    return parsed
            except Exception:
                continue
        return []

    def _read_source_items(self) -> list[dict]:
        """Read source JSONL records as a fallback for guaranteed links."""
        source = self.source_jsonl_path
        if not source.exists():
            return []

        records: list[dict] = []
        try:
            with open(source, "r", encoding="utf-8") as f:
                for raw in f:
                    line = raw.strip()
                    if not line:
                        continue
                    rec = json.loads(line)
                    title = str(rec.get("title") or "").strip()
                    url = str(rec.get("url") or "").strip()
                    if not title or not url:
                        continue
                    records.append({
                        "title": title,
                        "url": url,
                        "summary": str(rec.get("summary") or rec.get("description") or "").strip(),
                        "recommended_anchors": rec.get("recommended_anchors") or rec.get("anchors") or [],
                        "operator": str(rec.get("operator") or "").strip().lower(),
                        "always_include": bool(rec.get("always_include", False)),
                    })
        except Exception:
            return []
        return records

    def _always_include_links(self) -> list[InternalLinkSpec]:
        """Load operator-scoped links flagged for guaranteed insertion."""
        if self._items:
            items = self._items
        else:
            items = self._read_index_items()
            if not items:
                items = self._read_source_items()

        links: list[InternalLinkSpec] = []
        for item in items:
            if not bool(item.get("always_include", False)):
                continue
            title = str(item.get("title") or "").strip()
            url = str(item.get("url") or "").strip()
            if not title or not url:
                continue
            anchors = item.get("recommended_anchors") or item.get("anchors") or [title]
            anchors = [str(a).strip() for a in anchors if str(a).strip()] or [title]
            links.append(
                InternalLinkSpec(
                    title=title,
                    url=url,
                    recommended_anchors=anchors,
                    description=str(item.get("summary") or item.get("description") or "").strip(),
                    score=1.0,
                    operator=str(item.get("operator") or "").strip().lower() or _link_operator(item),
                    always_include=True,
                )
            )
        return links

    @property
    def index_json_path(self) -> Path:
        return _property_index_json(self.property_key)

    @property
    def index_vec_path(self) -> Path:
        return _property_index_vec(self.property_key)

    @property
    def source_jsonl_path(self) -> Path:
        source = _property_source_jsonl(self.property_key)
        if source.exists():
            return source
        if self.property_key == DEFAULT_PROPERTY:
            return LEGACY_SOURCE_JSONL
        return source

    def _ensure_loaded(self) -> bool:
        """Load index if not already loaded."""
        if self._loaded:
            return True

        if self.index_json_path.exists() and self.index_vec_path.exists():
            return self._load_index(self.index_json_path, self.index_vec_path)

        # Backward compatibility for legacy single-index setups.
        if self.property_key == DEFAULT_PROPERTY and LEGACY_INDEX_JSON.exists() and LEGACY_INDEX_VEC.exists():
            return self._load_index(LEGACY_INDEX_JSON, LEGACY_INDEX_VEC)

        return False

    def _load_index(self, index_json: Path, index_vec: Path) -> bool:
        """Load pre-built index files."""
        try:
            with open(index_json, "r", encoding="utf-8") as f:
                self._items = json.load(f)
            self._vectors = np.load(str(index_vec))
            self._loaded = True
            return True
        except Exception as e:
            print(f"Failed to load internal links index for {self.property_key}: {e}")
            return False

    async def ingest_from_jsonl(self, path: Path | str | None = None) -> int:
        """Ingest internal links from JSONL and build embeddings index."""
        source = Path(path) if path else self.source_jsonl_path
        if not source.exists():
            return 0

        seen_ids: set[str] = set()
        items: list[dict] = []
        with open(source, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line:
                    continue
                rec = json.loads(line)
                url = str(rec.get("url") or "").strip()
                title = str(rec.get("title") or "").strip()
                if not url or not title:
                    continue

                rec_id = str(rec.get("id") or f"{url}::{title}").strip()
                if rec_id in seen_ids:
                    continue
                seen_ids.add(rec_id)

                anchors = rec.get("recommended_anchors") or rec.get("anchors") or []
                anchors = [str(a).strip() for a in anchors if str(a).strip()]
                operator = str(rec.get("operator") or "").strip().lower() or _normalize_operator(
                    " ".join([title, url, " ".join(anchors[:4])])
                )
                item = {
                    "id": rec_id,
                    "title": title,
                    "url": url,
                    "summary": str(rec.get("summary") or rec.get("description") or "").strip(),
                    "recommended_anchors": anchors,
                    "operator": operator,
                    "always_include": bool(rec.get("always_include", False)),
                }
                items.append(item)

        if not items:
            return 0

        from app.services.llm import get_embeddings_batch

        docs = [
            " | ".join(
                [
                    item.get("title", ""),
                    item.get("summary", ""),
                    " ".join(item.get("recommended_anchors", [])[:4]),
                ]
            ).strip(" |")
            for item in items
        ]
        vectors = await get_embeddings_batch(docs)
        vectors_arr = np.array(vectors, dtype=np.float32)
        norms = np.linalg.norm(vectors_arr, axis=1, keepdims=True)
        vectors_arr = vectors_arr / (norms + 1e-12)

        STORAGE_DIR.mkdir(parents=True, exist_ok=True)
        with open(self.index_json_path, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
        np.save(str(self.index_vec_path), vectors_arr)

        # Keep legacy files in sync for action_network compatibility.
        if self.property_key == DEFAULT_PROPERTY:
            with open(LEGACY_INDEX_JSON, "w", encoding="utf-8") as f:
                json.dump(items, f, ensure_ascii=False, indent=2)
            np.save(str(LEGACY_INDEX_VEC), vectors_arr)

        self._items = items
        self._vectors = vectors_arr
        self._loaded = True
        return len(items)

    def _required_links(self) -> list[InternalLinkSpec]:
        records = REQUIRED_LINKS_BY_PROPERTY.get(self.property_key, [])
        links: list[InternalLinkSpec] = []
        for rec in records:
            links.append(
                InternalLinkSpec(
                    title=str(rec.get("title") or ""),
                    url=str(rec.get("url") or ""),
                    recommended_anchors=[str(a) for a in rec.get("recommended_anchors", [])],
                    description=str(rec.get("description") or ""),
                    score=1.0,
                    always_include=True,
                )
            )

        # Admin-managed guaranteed links (always_include=true) are also required.
        links.extend(self._always_include_links())

        # Deduplicate by URL while preserving ordering.
        deduped: list[InternalLinkSpec] = []
        seen_urls: set[str] = set()
        for link in links:
            url = (link.url or "").strip().lower()
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            deduped.append(link)

        return deduped

    def get_required_links(self) -> list[InternalLinkSpec]:
        """Public accessor for required links by property."""
        return self._required_links()

    async def suggest_links(
        self,
        title: str,
        context: list[str] | None = None,
        k: int = 3,
        brand: str = "",
    ) -> list[InternalLinkSpec]:
        """Suggest internal links based on title/context with operator filtering."""
        required = self._required_links()
        if not self._ensure_loaded():
            return required

        if self._vectors is None or len(self._items) == 0:
            return required

        query_parts = [title or ""]
        if context:
            query_parts.extend(context[:3])
        query = " | ".join(query_parts)

        query_vec = await get_embedding(query)
        query_arr = np.array([query_vec], dtype=np.float32)
        norm = np.linalg.norm(query_arr)
        if norm > 0:
            query_arr = query_arr / norm

        sims = (self._vectors @ query_arr.T).flatten()
        ranked_idx = np.argsort(-sims)

        target_operator = _normalize_operator(brand)
        picked: list[InternalLinkSpec] = []
        seen_urls: set[str] = {r.url for r in required if r.url}

        max_candidates = min(len(ranked_idx), max(50, k * 10))
        for idx in ranked_idx[:max_candidates]:
            if idx >= len(self._items):
                continue
            item = self._items[idx]
            url = str(item.get("url", "")).strip()
            if not url or url in seen_urls:
                continue

            item_operator = _link_operator(item)
            # If article is for a known operator, do not surface competitor operator links.
            if target_operator and item_operator and item_operator != target_operator:
                continue

            spec = InternalLinkSpec(
                title=str(item.get("title", "")),
                url=url,
                recommended_anchors=item.get("recommended_anchors") or [str(item.get("title", ""))],
                description=str(item.get("summary", "")),
                score=float(sims[idx]),
                operator=item_operator,
                always_include=bool(item.get("always_include", False)),
            )
            picked.append(spec)
            seen_urls.add(url)
            if len(picked) >= k:
                break

        # Required links are prepended so prompt always has them.
        return required + picked


_link_stores: dict[str, InternalLinksStore] = {}


def get_links_store(property_key: str | None = None) -> InternalLinksStore:
    """Get or create a property-scoped internal links store."""
    key = _normalize_property_key(property_key)
    store = _link_stores.get(key)
    if store is None:
        store = InternalLinksStore(property_key=key)
        _link_stores[key] = store
    return store


async def suggest_links_for_section(
    title: str,
    must_include: list[str] | None = None,
    k: int = 3,
    property_key: str | None = None,
    brand: str = "",
) -> list[InternalLinkSpec]:
    """Convenience function for suggesting links."""
    store = get_links_store(property_key=property_key)
    return await store.suggest_links(title, context=must_include, k=k, brand=brand)


def get_required_links_for_property(property_key: str | None = None) -> list[InternalLinkSpec]:
    """Return deterministic required links for a property."""
    store = get_links_store(property_key=property_key)
    return store.get_required_links()


def _prediction_market_safe_text(text: str) -> str:
    """Replace sportsbook-heavy phrasing with prediction-market wording."""
    if not text:
        return text
    replacements = [
        (r"\bbetting\b", "market"),
        (r"\bbet\b", "trade"),
        (r"\bsportsbooks?\b", "operators"),
        (r"\bbonus bets?\b", "promo credits"),
    ]
    result = text
    for pattern, repl in replacements:
        result = re.sub(pattern, repl, result, flags=re.IGNORECASE)
    return result


def format_links_markdown(
    links: list[InternalLinkSpec],
    brand: str = "",
    prediction_market: bool | None = None,
) -> str:
    """Format link suggestions as markdown bullets for prompts."""
    if prediction_market is None:
        prediction_market = is_prediction_market_context(brand)

    lines: list[str] = []
    has_guaranteed = any(bool(link.url) and link.always_include for link in links)
    if links:
        for link in links:
            if not link.url:
                continue
            title = link.title
            anchor_hint = ""
            if link.recommended_anchors:
                anchors = link.recommended_anchors[:3]
                if prediction_market:
                    anchors = [_prediction_market_safe_text(a) for a in anchors]
                anchor_hint = f" - anchors: {', '.join(anchors)}"
            if prediction_market:
                title = _prediction_market_safe_text(title)
            display = f"[{title}]({link.url})"
            prefix = "GUARANTEED: " if link.always_include else ""
            lines.append(f"- {prefix}{display}{anchor_hint}")

    if has_guaranteed:
        lines.append("- GUARANTEED links above must appear in the final article.")

    brand_name = brand or "BRAND"
    if prediction_market:
        contextual_suggestions = [
            f"- [{brand_name} sign-up guide](#) - use when explaining registration steps",
            "- [how market contracts settle](#) - use when explaining outcome mechanics",
            f"- [check your state's {brand_name} eligibility](#) - use when mentioning state-specific rules",
        ]
    else:
        contextual_suggestions = [
            f"- [{brand_name} sign-up guide](#) - use when explaining registration steps",
            "- [how bonus bets work](#) - use when explaining bonus bet mechanics",
            f"- [check your state's {brand_name} terms](#) - use when mentioning state-specific rules",
        ]
    lines.extend(contextual_suggestions)
    return "\n".join(lines)
