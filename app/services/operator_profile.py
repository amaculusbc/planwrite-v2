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

PREDICTION_MARKET_OPERATORS = {"kalshi", "polymarket"}


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


def is_prediction_market_offer(
    offer: dict[str, Any] | None,
    *,
    keyword: str = "",
    title: str = "",
) -> bool:
    """Return True if offer context maps to Kalshi/Polymarket."""
    offer = offer or {}
    return is_prediction_market_context(
        offer.get("operator"),
        offer.get("brand"),
        offer.get("offer_text"),
        offer.get("affiliate_offer"),
        keyword,
        title,
    )

