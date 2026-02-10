"""Offer parsing helpers.

Extracts structured details from offer text/terms so prompts can stay factual.
"""

from __future__ import annotations

import re
from typing import Any


def extract_bonus_expiration_days(terms: str | None) -> int | None:
    """Extract bonus expiration days from terms text.

    Returns None if no explicit day value is found.
    """
    if not terms:
        return None

    text = terms.lower()
    patterns = [
        r"expire[sd]?\s+(?:in|within)\s+(\d+)\s+days?",
        r"valid\s+for\s+(\d+)\s+days?",
        r"must\s+be\s+used\s+within\s+(\d+)\s+days?",
        r"(\d+)[-\s]day\s+expiration",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            try:
                return int(match.group(1))
            except (ValueError, IndexError):
                continue
    return None


def extract_minimum_odds(terms: str | None) -> str:
    """Extract minimum odds requirement from terms."""
    if not terms:
        return ""
    text = terms.lower()
    patterns = [
        r"minimum\s+odds\s+(?:of\s+)?([+-]?\d+)",
        r"odds\s+of\s+([+-]?\d+)\s+or\s+(?:longer|better|higher)",
        r"([+-]?\d+)\s+odds\s+(?:or\s+(?:longer|better))?",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return ""


def extract_wagering_requirement(terms: str | None) -> str:
    """Extract wagering requirement from terms."""
    if not terms:
        return ""
    text = terms.lower()
    patterns = [
        r"(\d+)x\s+(?:playthrough|rollover|wagering)",
        r"(?:playthrough|rollover|wagering)\s+(?:requirement\s+of\s+)?(\d+)x",
        r"must\s+be\s+wagered\s+(\d+)\s+time[s]?",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return f"{match.group(1)}x"
    return ""


def extract_bonus_amount(offer_text: str | None) -> str:
    """Extract bonus amount from offer text."""
    if not offer_text:
        return ""
    patterns = [
        r"\$(\d+(?:,\d+)?(?:\.\d+)?)",
        r"(\d+(?:,\d+)?)\s+(?:dollars?|bucks)",
    ]
    for pattern in patterns:
        match = re.search(pattern, offer_text)
        if match:
            amount = match.group(1).replace(",", "")
            return f"${amount}"
    return ""


def parse_states(states: Any) -> list[str]:
    """Parse states field to a normalized list."""
    if states is None:
        return []
    if isinstance(states, list):
        vals = [str(s).strip().upper() for s in states if str(s).strip()]
        return vals or []
    if not isinstance(states, str) or not states.strip():
        return []
    txt = states.strip()
    if txt.upper() == "ALL":
        return ["ALL"]
    parts = [p.strip().upper() for p in re.split(r"[,\|/]+", txt) if p.strip()]
    seen: set[str] = set()
    result: list[str] = []
    for p in parts:
        if p not in seen:
            seen.add(p)
            result.append(p)
    return result


def enrich_offer_dict(offer: dict) -> dict:
    """Return a copy of offer with parsed terms fields added."""
    if not offer:
        return {}
    offer_text = str(offer.get("offer_text") or offer.get("affiliate_offer") or "")
    terms = str(offer.get("terms") or "")
    states = offer.get("states") or offer.get("states_list") or ""

    enriched = dict(offer)
    enriched["states_list"] = parse_states(states)
    enriched["bonus_expiration_days"] = offer.get("bonus_expiration_days") or extract_bonus_expiration_days(terms)
    enriched["minimum_odds"] = offer.get("minimum_odds") or extract_minimum_odds(terms)
    enriched["wagering_requirement"] = offer.get("wagering_requirement") or extract_wagering_requirement(terms)
    enriched["bonus_amount"] = offer.get("bonus_amount") or extract_bonus_amount(offer_text)
    return enriched
