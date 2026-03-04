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
    paired = extract_offer_amount_details(offer_text)
    reward_amount = str(paired.get("reward_amount") or "").strip()
    if reward_amount:
        return reward_amount
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


def extract_offer_amount_details(offer_text: str | None) -> dict[str, str]:
    """Extract qualifying/reward amounts for paired promos like 'Bet $5, Get $150'.

    Returns an empty dict when no reliable paired pattern is detected.
    """
    if not offer_text:
        return {}

    text = re.sub(r"\s+", " ", str(offer_text)).strip()
    if not text:
        return {}

    # Forward order: "Spend/Bet/Play $X ... Get/Unlock $Y ..."
    forward_patterns = [
        r"(?P<action>bet|wager|spend|play|deposit|purchase|buy)\s*\$?(?P<qual>\d+(?:,\d+)?(?:\.\d+)?)\b"
        r".{0,100}?"
        r"(?:get|unlock|receive|earn|claim|snag|net|secure|use)\s*\$?(?P<reward>\d+(?:,\d+)?(?:\.\d+)?)"
        r"(?:\s+in\s+(?P<label>[A-Za-z][A-Za-z ]{1,60}))?",
        r"(?P<action>make(?:\s+a)?\s+purchase(?:\s+of)?)\s*\$?(?P<qual>\d+(?:,\d+)?(?:\.\d+)?)\b"
        r".{0,100}?"
        r"(?:get|unlock|receive|earn|claim|snag|net|secure|use)\s*\$?(?P<reward>\d+(?:,\d+)?(?:\.\d+)?)"
        r"(?:\s+in\s+(?P<label>[A-Za-z][A-Za-z ]{1,60}))?",
        r"make(?:\s+a)?\s+\$?(?P<qual>\d+(?:,\d+)?(?:\.\d+)?)\s+purchase\b"
        r".{0,100}?"
        r"(?:get|unlock|receive|earn|claim|snag|net|secure|use)\s*\$?(?P<reward>\d+(?:,\d+)?(?:\.\d+)?)"
        r"(?:\s+in\s+(?P<label>[A-Za-z][A-Za-z ]{1,60}))?",
    ]
    for pattern in forward_patterns:
        forward = re.search(pattern, text, flags=re.IGNORECASE)
        if not forward:
            continue
        details = {
            "qualifying_action": _normalize_qualifying_action(forward.groupdict().get("action")),
            "qualifying_amount": _fmt_money(forward.group("qual")),
            "reward_amount": _fmt_money(forward.group("reward")),
        }
        label = _clean_reward_label(forward.groupdict().get("label"))
        if label:
            details["reward_label"] = label
        return details

    # Reverse order: "Get $Y ... when you spend/bet $X"
    reverse_patterns = [
        r"(?:get|unlock|receive|earn|claim|snag|net|secure|use)\s*\$?(?P<reward>\d+(?:,\d+)?(?:\.\d+)?)"
        r"(?:\s+in\s+(?P<label>[A-Za-z][A-Za-z ]{1,60}))?"
        r".{0,140}?"
        r"(?:when you|after you|if you)?\s*"
        r"(?P<action>bet|wager|spend|play|deposit|purchase|buy)\s*\$?(?P<qual>\d+(?:,\d+)?(?:\.\d+)?)",
        r"(?:get|unlock|receive|earn|claim|snag|net|secure|use)\s*\$?(?P<reward>\d+(?:,\d+)?(?:\.\d+)?)"
        r"(?:\s+in\s+(?P<label>[A-Za-z][A-Za-z ]{1,60}))?"
        r".{0,140}?"
        r"(?:when you|after you|if you)?\s*"
        r"make(?:\s+a)?\s+\$?(?P<qual>\d+(?:,\d+)?(?:\.\d+)?)\s+purchase\b",
    ]
    for pattern in reverse_patterns:
        reverse = re.search(pattern, text, flags=re.IGNORECASE)
        if not reverse:
            continue
        details = {
            "qualifying_action": _normalize_qualifying_action(reverse.groupdict().get("action") or "purchase"),
            "qualifying_amount": _fmt_money(reverse.group("qual")),
            "reward_amount": _fmt_money(reverse.group("reward")),
        }
        label = _clean_reward_label(reverse.groupdict().get("label"))
        if label:
            details["reward_label"] = label
        return details

    return {}


def _fmt_money(raw: str | None) -> str:
    if not raw:
        return ""
    return f"${str(raw).replace(',', '').strip()}"


def _normalize_qualifying_action(raw: str | None) -> str:
    value = (raw or "").strip().lower()
    if value.startswith("make"):
        return "purchase"
    if value == "buy":
        return "purchase"
    return value


def _clean_reward_label(raw: str | None) -> str:
    if not raw:
        return ""
    label = re.sub(r"\s+", " ", raw).strip(" .,:;")
    label = re.split(r"\b(?:for|on|today|tonight|this|during|via|with|when)\b", label, maxsplit=1, flags=re.IGNORECASE)[0].strip(" .,:;")
    # Keep short, noun-like labels only (e.g., "Novig Coins", "bonus bets").
    if len(label.split()) > 5:
        return ""
    return label


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
    amount_details = extract_offer_amount_details(offer_text)
    if amount_details:
        if amount_details.get("qualifying_action") and not enriched.get("qualifying_action"):
            enriched["qualifying_action"] = amount_details["qualifying_action"]
        if amount_details.get("qualifying_amount") and not enriched.get("qualifying_amount"):
            enriched["qualifying_amount"] = amount_details["qualifying_amount"]
        if amount_details.get("reward_amount") and not enriched.get("reward_amount"):
            enriched["reward_amount"] = amount_details["reward_amount"]
        if amount_details.get("reward_label") and not enriched.get("reward_label"):
            enriched["reward_label"] = amount_details["reward_label"]
    enriched["bonus_amount"] = offer.get("bonus_amount") or amount_details.get("reward_amount") or extract_bonus_amount(offer_text)
    return enriched
