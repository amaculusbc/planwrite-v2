"""Deterministic quick-facts block for Action Network offer articles.

Nick's content audit asked for a self-contained, citable block near the top: code, what
triggers the offer, what you get, age, event, eligibility. It is also the fix for the
truncated state list - the model was reciting states alphabetically and stopping partway
(at MO, before Montana). Rendering "all US states except X" from the offer's own data gives
the clean, complete format that prose enumeration never manages.

Every row is omitted rather than guessed: an absent fact must not become an invented one.
"""

from __future__ import annotations

import re
from html import escape
from typing import Any

from app.services.offer_parsing import (
    extract_bonus_expiration_days,
    extract_excluded_states_from_terms,
    extract_minimum_age,
    parse_states,
)


def _event_text(event_context: str) -> str:
    """Render 'Spain vs. France, 3:00 PM ET on FOX' from the event context block."""
    if not event_context:
        return ""
    label_match = re.search(
        r"Featured (?:event|game):\s*(.+?)(?:\.\s+(?:Game time|Network):|$)",
        event_context,
        flags=re.IGNORECASE,
    )
    if not label_match:
        return ""
    parts = [re.sub(r"\s+", " ", label_match.group(1).strip().rstrip("."))]
    time_match = re.search(r"Game time:\s*([^\.]+)", event_context, flags=re.IGNORECASE)
    if time_match:
        parts.append(re.sub(r"\s+", " ", time_match.group(1).strip()))
    network_match = re.search(r"Network:\s*([^\.]+)", event_context, flags=re.IGNORECASE)
    if network_match:
        parts.append("on " + re.sub(r"\s+", " ", network_match.group(1).strip()))
    return ", ".join(parts[:2]) + (f" {parts[2]}" if len(parts) > 2 else "")


def eligible_states_text(offer: dict[str, Any]) -> str:
    """Describe eligibility completely, or say nothing.

    A nationwide offer with exclusions reads as "all US states except X" rather than an
    enumeration of the ~42 that remain, which is what the model kept truncating.
    """
    states = parse_states(offer.get("states_list") or offer.get("states"))
    excluded = offer.get("excluded_states_list") or extract_excluded_states_from_terms(
        str(offer.get("terms") or "")
    )
    nationwide = not states or "ALL" in states
    if nationwide:
        if excluded:
            return "All US states except " + ", ".join(excluded)
        return ""
    listed = ", ".join(states)
    if excluded:
        return f"{listed} (not available in {', '.join(excluded)})"
    return listed


def is_nationwide_offer(offer: dict[str, Any]) -> bool:
    """True when the offer runs everywhere bar an exclusion list, rather than a named set."""
    states = parse_states((offer or {}).get("states_list") or (offer or {}).get("states"))
    return not states or "ALL" in states


def _qualifying_text(offer: dict[str, Any]) -> str:
    amount = str(offer.get("qualifying_amount") or "").strip()
    if not amount:
        return ""
    action = str(offer.get("qualifying_action") or "").strip().replace("_", " ")
    return f"A {amount} {action}".strip() if action else amount


def _reward_text(offer: dict[str, Any], reward_noun: str) -> str:
    amount = str(offer.get("bonus_amount") or offer.get("reward_amount") or "").strip()
    if not amount:
        return ""
    label = str(offer.get("reward_label") or "").strip() or reward_noun
    return f"{amount} in {label}"


def render_quick_facts_table(
    offer: dict[str, Any],
    *,
    event_context: str = "",
    reward_noun: str = "promo credits",
) -> str:
    """Render the quick-facts table, or '' when too little is known to be useful."""
    offer = offer or {}
    brand = str(offer.get("brand") or "").strip()
    terms = str(offer.get("terms") or "")
    offer_text = str(offer.get("offer_text") or offer.get("affiliate_offer") or "").strip()
    reward = _reward_text(offer, reward_noun)
    # "No code required" is a real answer, but only once we know what the offer is. Without
    # that, the block would announce nothing at all.
    if not offer_text and not reward:
        return ""

    expiry_days = offer.get("bonus_expiration_days") or extract_bonus_expiration_days(terms)
    rows: list[tuple[str, str]] = [
        (f"{brand} promo code" if brand else "Promo code", str(offer.get("bonus_code") or "").strip() or "No code required"),
        ("Offer", offer_text),
        ("What triggers it", _qualifying_text(offer)),
        ("What you get", reward),
        ("Minimum age", str(offer.get("minimum_age") or extract_minimum_age(terms) or "").strip()),
        ("Event", _event_text(event_context)),
        ("Eligible states", eligible_states_text(offer)),
        ("Credits expire", f"{expiry_days} days after they post" if expiry_days else ""),
    ]

    body = "".join(
        f"<tr><td><strong>{escape(label)}</strong></td><td>{escape(value)}</td></tr>\n"
        for label, value in rows
        if label and value
    )
    if not body:
        return ""
    return f"<table>\n{body}</table>"
