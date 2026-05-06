"""Curated operator fact overrides for cases where feed terms are too thin for copy."""

from __future__ import annotations

import re
from typing import Any


_OPERATOR_FACTS: dict[str, dict[str, Any]] = {
    "underdog": {
        "content_mode": "dfs",
        "allowed_states": [
            "AL", "AK", "AR", "CA", "DC", "FL", "GA", "IL", "IN", "KS",
            "ME", "MS", "MN", "NE", "NM", "NC", "ND", "OK", "OR", "RI",
            "SC", "SD", "TX", "UT", "VA", "VT", "WI", "WY",
        ],
        "excluded_states": ["MD", "MI", "NJ", "NY", "OH", "PA"],
        "age_summary_short": "18+ (age varies by state)",
        "age_summary_full": "Must be 18+ (19+ in AL, NE; 19+ in CO for some games; 21+ in AZ, MA, and VA).",
    },
    "novig": {
        "content_mode": "prediction_market",
        "reward_label": "Novig Coins",
    },
}


def _normalize_operator_key(brand: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(brand or "").strip().lower())


def get_operator_facts(brand: str | None, *, content_mode: str = "") -> dict[str, Any]:
    """Return curated operator facts when the brand/content-mode pair is known."""
    key = _normalize_operator_key(brand)
    facts = dict(_OPERATOR_FACTS.get(key) or {})
    if not facts:
        return {}
    expected_mode = str(facts.get("content_mode") or "").strip().lower()
    actual_mode = str(content_mode or "").strip().lower()
    if expected_mode and actual_mode and expected_mode != actual_mode:
        return {}
    return facts
