"""Offer parsing helpers.

Extracts structured details from offer text/terms so prompts can stay factual.
"""

from __future__ import annotations

import re
from typing import Any


_STATE_CODES: tuple[str, ...] = (
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC",
    "FL", "GA", "HI", "ID", "IL", "IN", "IA", "KS", "KY",
    "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT",
    "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH",
    "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT",
    "VT", "VA", "WA", "WV", "WI", "WY", "ON", "PR",
)
_STATE_CODE_SET = set(_STATE_CODES)
_STATE_CODE_PATTERN = re.compile(r"\b(" + "|".join(_STATE_CODES) + r")\b")

_STATE_NAME_TO_CODE = {
    "alabama": "AL",
    "alaska": "AK",
    "arizona": "AZ",
    "arkansas": "AR",
    "california": "CA",
    "colorado": "CO",
    "connecticut": "CT",
    "delaware": "DE",
    "district of columbia": "DC",
    "florida": "FL",
    "georgia": "GA",
    "hawaii": "HI",
    "idaho": "ID",
    "illinois": "IL",
    "indiana": "IN",
    "iowa": "IA",
    "kansas": "KS",
    "kentucky": "KY",
    "louisiana": "LA",
    "maine": "ME",
    "maryland": "MD",
    "massachusetts": "MA",
    "michigan": "MI",
    "minnesota": "MN",
    "mississippi": "MS",
    "missouri": "MO",
    "montana": "MT",
    "nebraska": "NE",
    "nevada": "NV",
    "new hampshire": "NH",
    "new jersey": "NJ",
    "new mexico": "NM",
    "new york": "NY",
    "north carolina": "NC",
    "north dakota": "ND",
    "ohio": "OH",
    "oklahoma": "OK",
    "oregon": "OR",
    "pennsylvania": "PA",
    "rhode island": "RI",
    "south carolina": "SC",
    "south dakota": "SD",
    "tennessee": "TN",
    "texas": "TX",
    "utah": "UT",
    "vermont": "VT",
    "virginia": "VA",
    "washington": "WA",
    "west virginia": "WV",
    "wisconsin": "WI",
    "wyoming": "WY",
    "ontario": "ON",
    "puerto rico": "PR",
    "washington dc": "DC",
    "washington d c": "DC",
    "d c": "DC",
    "d.c.": "DC",
    "dc": "DC",
}
_STATE_NAME_ITEMS = sorted(_STATE_NAME_TO_CODE.items(), key=lambda item: len(item[0]), reverse=True)


def _dedupe_preserve(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _extract_state_codes_from_fragment(fragment: str) -> list[str]:
    if not fragment:
        return []

    found: list[str] = []
    for match in _STATE_CODE_PATTERN.finditer(fragment):
        found.append(match.group(1).upper())

    normalized = re.sub(r"[^\w\s]", " ", fragment.lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if normalized:
        for name, code in _STATE_NAME_ITEMS:
            if re.search(rf"\b{re.escape(name)}\b", normalized):
                found.append(code)

    return _dedupe_preserve(found)


def extract_states_from_terms(terms: str | None) -> list[str]:
    """Extract a canonical state list from terms text.

    Returns [] when no reliable eligibility states can be identified.
    """
    if not terms:
        return []

    text = str(terms)
    lower = text.lower()
    patterns = [
        r"\bin the following states:\s*(.+?)(?:\.|;|$)",
        r"\bavailable in\s+(.+?)(?:\bonly\b|\.|;|$)",
        r"\bnew (?:customers|players|users) in\s+(.+?)(?:\bonly\b|\.|;|$)",
        r"\bphysically present in\s+(.+?)(?:\bin order to wager\b|\.|;|$)",
    ]

    for pattern in patterns:
        for match in re.finditer(pattern, lower, flags=re.IGNORECASE):
            start = match.start()
            prefix = lower[max(0, start - 12):start]
            if "not " in prefix:
                continue

            fragment = text[match.start(1):match.end(1)]
            codes = _extract_state_codes_from_fragment(fragment)
            if codes:
                return codes

    has_positive_available = bool(re.search(r"(?<!not\s)available in", lower))
    positive_cues = (
        "in the following states:",
        "new customers in",
        "new players in",
        "new users in",
        "physically present in",
    )
    if has_positive_available or any(cue in lower for cue in positive_cues):
        return _extract_state_codes_from_fragment(text)
    return []


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

    raw_values: list[str]
    if isinstance(states, list):
        raw_values = [str(s).strip() for s in states if str(s).strip()]
    elif isinstance(states, str):
        txt = states.strip()
        if not txt:
            return []
        if txt.upper() in {"ALL", "NATIONWIDE"}:
            return ["ALL"]
        raw_values = [part.strip() for part in re.split(r"[,\|/;]+", txt) if part.strip()]
    else:
        txt = str(states).strip()
        raw_values = [txt] if txt else []

    if not raw_values:
        return []

    codes: list[str] = []
    for raw in raw_values:
        upper = raw.upper()
        if upper in {"ALL", "NATIONWIDE"}:
            return ["ALL"]
        if upper in _STATE_CODE_SET:
            codes.append(upper)
            continue
        codes.extend(_extract_state_codes_from_fragment(raw))

    codes = _dedupe_preserve(codes)
    if codes:
        return codes

    if len(raw_values) == 1 and raw_values[0].strip().lower() in {"all", "nationwide"}:
        return ["ALL"]

    return []


def enrich_offer_dict(offer: dict) -> dict:
    """Return a copy of offer with parsed terms fields added."""
    if not offer:
        return {}
    offer_text = str(offer.get("offer_text") or offer.get("affiliate_offer") or "")
    terms = str(offer.get("terms") or "")
    states = offer.get("states") or offer.get("states_list") or ""

    enriched = dict(offer)
    enriched["states_list"] = parse_states(states) or extract_states_from_terms(terms)
    enriched["bonus_expiration_days"] = offer.get("bonus_expiration_days") or extract_bonus_expiration_days(terms)
    enriched["minimum_odds"] = offer.get("minimum_odds") or extract_minimum_odds(terms)
    enriched["wagering_requirement"] = offer.get("wagering_requirement") or extract_wagering_requirement(terms)
    enriched["bonus_amount"] = offer.get("bonus_amount") or extract_bonus_amount(offer_text)
    return enriched
