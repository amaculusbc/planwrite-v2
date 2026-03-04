"""Operator profile helpers for generation mode decisions."""

from __future__ import annotations

import re
from typing import Any

_OPERATOR_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bkalshi\b", re.IGNORECASE), "kalshi"),
    (re.compile(r"\bpolymarket\b", re.IGNORECASE), "polymarket"),
    (re.compile(r"\bbet365\b", re.IGNORECASE), "bet365"),
    (re.compile(r"\bfanduel\b", re.IGNORECASE), "fanduel"),
    (re.compile(r"\bdraftkings\b", re.IGNORECASE), "draftkings"),
    (re.compile(r"\bbetmgm\b", re.IGNORECASE), "betmgm"),
    (re.compile(r"\bcaesars\b", re.IGNORECASE), "caesars"),
    (re.compile(r"\bfanatics\b", re.IGNORECASE), "fanatics"),
    (re.compile(r"\bunderdog\b", re.IGNORECASE), "underdog"),
    (re.compile(r"\bnovig\b", re.IGNORECASE), "novig"),
    (re.compile(r"\bsleeper\b", re.IGNORECASE), "sleeper"),
]

CONTENT_MODE_SPORTSBOOK = "sportsbook"
CONTENT_MODE_PREDICTION_MARKET = "prediction_market"
CONTENT_MODE_DFS = "dfs"

# Editorial rule: Novig should use prediction-market language in V2.
PREDICTION_MARKET_OPERATORS = {"kalshi", "polymarket", "novig"}
DFS_OPERATORS = {"sleeper", "underdog", "dabble"}


def normalize_operator(*values: Any) -> str:
    """Infer canonical operator key from freeform values."""
    text = " ".join(str(v) for v in values if v).strip()
    if not text:
        return ""
    for pattern, operator in _OPERATOR_PATTERNS:
        if pattern.search(text):
            return operator
    return ""


def is_prediction_market_context(*values: Any) -> bool:
    """Return True when context clearly refers to a prediction market operator."""
    return normalize_operator(*values) in PREDICTION_MARKET_OPERATORS


def is_dfs_context(*values: Any) -> bool:
    """Return True when context clearly refers to a DFS operator/app."""
    return normalize_operator(*values) in DFS_OPERATORS


def get_content_mode_context(*values: Any) -> str:
    """Return generation language mode for freeform context values."""
    operator = normalize_operator(*values)
    if operator in PREDICTION_MARKET_OPERATORS:
        return CONTENT_MODE_PREDICTION_MARKET
    if operator in DFS_OPERATORS:
        return CONTENT_MODE_DFS
    return CONTENT_MODE_SPORTSBOOK


def is_prediction_market_offer(
    offer: dict[str, Any] | None,
    *,
    keyword: str = "",
    title: str = "",
) -> bool:
    """Return True if offer context maps to Kalshi/Polymarket."""
    return get_content_mode_offer(offer, keyword=keyword, title=title) == CONTENT_MODE_PREDICTION_MARKET


def is_dfs_offer(
    offer: dict[str, Any] | None,
    *,
    keyword: str = "",
    title: str = "",
) -> bool:
    """Return True if offer context maps to a DFS operator (e.g., Sleeper, Underdog)."""
    return get_content_mode_offer(offer, keyword=keyword, title=title) == CONTENT_MODE_DFS


def get_content_mode_offer(
    offer: dict[str, Any] | None,
    *values: Any,
    keyword: str = "",
    title: str = "",
) -> str:
    """Return content mode for an offer payload plus optional fallback values."""
    offer_dict = offer if isinstance(offer, dict) else {}
    extra_values: tuple[Any, ...]
    if values:
        extra_values = values
    else:
        extra_values = ()
    return get_content_mode_context(
        offer_dict.get("operator"),
        offer_dict.get("brand"),
        offer_dict.get("offer_text"),
        offer_dict.get("affiliate_offer"),
        keyword,
        title,
        *extra_values,
    )
