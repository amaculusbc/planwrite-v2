"""Curated operator fact overrides for cases where feed terms are too thin for copy."""

from __future__ import annotations

import re
from typing import Any


# Standing promotions available to existing players. These are permanent product
# features rather than campaigns, so no feed carries them: BAM lists acquisition
# offers only, and BC Core has boosts but no early-payout/insurance concept.
# Curated per operator, and only claims that are on the operator's own promo page.
_STANDING_PROMOS: dict[str, list[str]] = {
    "bet365": [
        "Early Payout on pre-match single bets when your team goes two goals ahead in soccer",
        "Bet Boost tokens on selected pre-match and in-play parlays",
        "2 Goals Ahead Early Payout across the major soccer competitions",
    ],
    "draftkings": [
        "Profit boost tokens issued to existing players",
        "Same Game Parlay boosts on featured matchups",
    ],
    "fanduel": [
        "Profit boosts issued to existing players",
        "Same Game Parlay insurance on selected multi-leg bets",
    ],
    "betmgm": [
        "Odds boosts on featured daily markets",
        "Parlay insurance on selected multi-leg bets",
    ],
    "caesars": [
        "Profit boosts on featured markets for existing players",
        "Parlay insurance on selected multi-leg bets",
    ],
}


def get_standing_promos(brand: str | None) -> list[str]:
    """Recurring promotions an existing player can use, not welcome offers."""
    return list(_STANDING_PROMOS.get(_normalize_operator_key(brand)) or [])


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
