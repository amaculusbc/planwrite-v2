"""Build property-specific internal link indexes from a raw seed text list.

Usage:
    python scripts/build_property_link_indexes.py --seed data/property_internal_links_seed.txt
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from app.services.internal_links import get_links_store


PROPERTY_BY_DOMAIN = {
    "actionnetwork.com": "action_network",
    "vegasinsider.com": "vegas_insider",
    "sportshandle.com": "sportshandle",
    "rotogrinders.com": "rotogrinders",
    "fantasylabs.com": "fantasy_labs",
}

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
    (re.compile(r"\bcrypto\.com\b|\bcrypto\b", re.IGNORECASE), "crypto"),
    (re.compile(r"\bfliff\b", re.IGNORECASE), "fliff"),
    (re.compile(r"\bpolymarket\b", re.IGNORECASE), "polymarket"),
    (re.compile(r"\bdabble\b", re.IGNORECASE), "dabble"),
    (re.compile(r"\bprophetx\b|\bprophet\b", re.IGNORECASE), "prophetx"),
]


def _detect_operator(text: str) -> str:
    for pattern, value in OPERATOR_PATTERNS:
        if pattern.search(text):
            return value
    return ""


def _split_url_and_title(raw: str) -> tuple[str, str]:
    line = re.sub(r"\s+", " ", raw).strip()
    if not line:
        return "", ""
    match = re.search(r"https?://\S+", line)
    if not match:
        return "", ""
    url = match.group(0).strip()
    label = line[match.end():].strip()
    return url, label


def _normalize_domain(netloc: str) -> str:
    netloc = netloc.lower().strip()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc


def _property_for_url(url: str) -> str:
    domain = _normalize_domain(urlsplit(url).netloc)
    for known, prop in PROPERTY_BY_DOMAIN.items():
        if domain == known or domain.endswith("." + known):
            return prop
    return ""


def _derive_title_from_url(url: str) -> str:
    path = urlsplit(url).path.strip("/")
    tail = path.split("/")[-1] if path else "resource"
    tail = re.sub(r"[-_]+", " ", tail).strip()
    tail = re.sub(r"\s+", " ", tail)
    return tail.title() if tail else "Resource"


def _clean_url_and_title(url: str, title: str) -> tuple[str, str]:
    url = url.strip().rstrip(".,)")
    url = url.replace("http://actionnetwork.com", "https://www.actionnetwork.com")
    parsed = urlsplit(url)
    if not parsed.scheme or not parsed.netloc:
        return "", title

    segments = [s for s in parsed.path.split("/") if s]
    if not segments:
        clean_path = "/"
        return urlunsplit((parsed.scheme, parsed.netloc, clean_path, parsed.query, parsed.fragment)), title

    tail = segments[-1]
    label = title.strip()

    # Handle accidentally concatenated title fragments at the end of URL paths.
    camel_tail = re.match(r"^(.*?)([A-Z][A-Za-z0-9.\-]+)$", tail)
    if camel_tail and camel_tail.group(1):
        tail = camel_tail.group(1)
        label = f"{camel_tail.group(2)} {label}".strip()

    # Handle repeated slug tails: e.g. bet365bet365, nflnfl.
    repeated = re.match(r"^([a-z0-9\-]{2,})\1$", tail)
    if repeated:
        base = repeated.group(1)
        tail = base
        if label and not label.lower().startswith(base.lower()):
            label = f"{base} {label}"
        elif not label:
            label = base

    # Handle "/best + title" collisions.
    if tail.lower() == "best" and label:
        segments = segments[:-1]
        if not label.lower().startswith("best "):
            label = f"best {label}"
        tail = ""
    elif tail.lower().endswith("best") and tail.lower() != "best" and label:
        tail = tail[:-4]
        if tail:
            if not label.lower().startswith("best "):
                label = f"best {label}"

    if segments:
        segments[-1] = tail or segments[-1]
    segments = [s for s in segments if s]
    clean_path = "/" + "/".join(segments)
    clean_url = urlunsplit((parsed.scheme, parsed.netloc, clean_path, parsed.query, parsed.fragment))
    clean_url = clean_url.rstrip("/") if clean_path != "/" else clean_url

    label = re.sub(r"\s+", " ", label).strip(" -")
    return clean_url, label


def _always_include(property_key: str, title: str) -> bool:
    text = title.lower()
    if property_key == "action_network":
        return (
            "best betting sites" in text
            or "legal sports betting" in text
            or "best sportsbooks" in text
        )
    if property_key == "vegas_insider":
        return (
            "best sportsbook promos" in text
            or "best online casinos" in text
            or "new sweepstakes casinos" in text
            or "best prediction markets" in text
        )
    if property_key == "sportshandle":
        return (
            "best betting sites" in text
            or "best sports betting apps" in text
            or "best prediction market apps" in text
        )
    if property_key == "rotogrinders":
        return ("best prediction market apps" in text or "best dfs apps" in text)
    if property_key == "fantasy_labs":
        return ("nfl dfs" in text or "best dfs apps" in text or "top dfs sites" in text)
    return False


async def build_indexes(seed_file: Path) -> None:
    if not seed_file.exists():
        raise FileNotFoundError(f"Seed file not found: {seed_file}")

    output_by_property: dict[str, list[dict]] = {k: [] for k in PROPERTY_BY_DOMAIN.values()}
    seen_by_property: dict[str, set[str]] = {k: set() for k in PROPERTY_BY_DOMAIN.values()}

    for raw in seed_file.read_text(encoding="utf-8").splitlines():
        url, title = _split_url_and_title(raw)
        if not url:
            continue
        url, title = _clean_url_and_title(url, title)
        if not url:
            continue

        property_key = _property_for_url(url)
        if not property_key:
            continue

        if not title:
            title = _derive_title_from_url(url)
        title = re.sub(r"\s+", " ", title).strip()

        dedupe_key = f"{url}::{title.lower()}"
        if dedupe_key in seen_by_property[property_key]:
            continue
        seen_by_property[property_key].add(dedupe_key)

        operator = _detect_operator(f"{title} {url}")
        record = {
            "id": hashlib.sha1(dedupe_key.encode("utf-8")).hexdigest()[:16],
            "url": url,
            "title": title,
            "summary": title,
            "recommended_anchors": [title.lower()],
            "operator": operator,
            "always_include": _always_include(property_key, title),
        }
        output_by_property[property_key].append(record)

    # Write property jsonl files.
    for property_key, rows in output_by_property.items():
        out_path = Path("data") / f"evergreen_{property_key}.jsonl"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"Wrote {len(rows)} rows -> {out_path}")

    # Rebuild each property index.
    for property_key in output_by_property:
        store = get_links_store(property_key=property_key)
        count = await store.ingest_from_jsonl(path=Path("data") / f"evergreen_{property_key}.jsonl")
        print(f"Indexed {count} links for {property_key}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build per-property internal link indexes.")
    parser.add_argument(
        "--seed",
        type=str,
        default="data/property_internal_links_seed.txt",
        help="Path to raw seed text file with URL and optional title per line.",
    )
    args = parser.parse_args()
    asyncio.run(build_indexes(Path(args.seed)))


if __name__ == "__main__":
    main()
