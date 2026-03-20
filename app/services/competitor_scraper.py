"""Competitor URL scraper for outline context."""

from __future__ import annotations

import asyncio
from typing import Iterable

import httpx
from bs4 import BeautifulSoup
import trafilatura


async def _fetch_html(url: str, timeout: float = 12.0) -> str:
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=headers) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.text


def _extract_headings(html: str, max_items: int = 8) -> list[str]:
    """Extract a short heading list so competitor context affects outline planning."""
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    seen: set[str] = set()
    headings: list[str] = []
    for tag in soup.select("h1, h2, h3"):
        txt = tag.get_text(" ", strip=True)
        txt = " ".join(txt.split())
        if not txt or len(txt) > 120:
            continue
        key = txt.lower()
        if key in seen:
            continue
        seen.add(key)
        headings.append(txt)
        if len(headings) >= max_items:
            break
    return headings


def _extract_text(html: str, max_chars: int = 2000) -> str:
    # Try trafilatura first
    extracted = trafilatura.extract(html, include_comments=False, include_tables=False)
    if extracted:
        return extracted.strip()[:max_chars]

    # Fallback: simple soup extraction
    soup = BeautifulSoup(html, "html.parser")
    parts = []
    for tag in soup.select("h1, h2, h3, p, li"):
        txt = tag.get_text(" ", strip=True)
        if txt:
            parts.append(txt)
        if sum(len(p) for p in parts) > max_chars:
            break
    return " ".join(parts).strip()[:max_chars]


async def scrape_competitor(url: str, max_chars: int = 1500) -> str:
    """Fetch and extract competitor content for a single URL."""
    try:
        html = await _fetch_html(url)
        headings = _extract_headings(html)
        body = _extract_text(html, max_chars=max_chars)
        parts: list[str] = []
        if headings:
            parts.append("HEADINGS:\n- " + "\n- ".join(headings))
        if body:
            parts.append("BODY:\n" + body)
        return "\n\n".join(parts).strip()
    except Exception as exc:
        return f"[FETCH_FAILED] {url} :: {exc}"


async def scrape_competitors(urls: Iterable[str], max_chars_per_url: int = 1500) -> str:
    """Scrape multiple URLs concurrently and return combined text."""
    tasks = [scrape_competitor(url, max_chars=max_chars_per_url) for url in urls]
    results = await asyncio.gather(*tasks, return_exceptions=False)

    combined_parts = []
    for url, text in zip(urls, results):
        if not text:
            continue
        combined_parts.append(f"URL: {url}\n{text}")
    return "\n\n".join(combined_parts)
