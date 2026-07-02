"""GOAL.com Top Stories article template.

GOAL's brief specifies a fixed structure that differs from the default
expert-pick layout: a very short intro, a fixed H2 sequence, a per-match H3
with real crawled lines, and a three-row terms table. This module builds the
deterministic outline and the GOAL-specific deterministic blocks.
"""

from __future__ import annotations

import re
from html import escape
from typing import Any

GOAL_PROPERTY_KEY = "goal_com"

# GOAL brief: 550-600 words total.
GOAL_TARGET_WORDS = 575


def is_goal_property(property_key: str | None) -> bool:
    return str(property_key or "").strip().lower() == GOAL_PROPERTY_KEY


def _format_american(value: Any) -> str:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return ""
    return f"{number:+d}"


def _first_book_lines(odds: dict[str, Any] | None, market_key: str, preferred_book: str = "") -> tuple[str, dict]:
    """Return (book, lines) for a market, preferring the article's operator."""
    market = (odds or {}).get(market_key) or {}
    if not isinstance(market, dict):
        return "", {}
    if preferred_book and isinstance(market.get(preferred_book), dict) and market.get(preferred_book):
        return preferred_book, market[preferred_book]
    for book, lines in market.items():
        if isinstance(lines, dict) and lines:
            return book, lines
    return "", {}


def format_odds_talking_points(
    odds: dict[str, Any] | None,
    away_team: str,
    home_team: str,
    preferred_book: str = "",
) -> list[str]:
    """Turn the crawled odds board into exact-price talking points."""
    points: list[str] = []
    book_label = ""

    book, moneyline = _first_book_lines(odds, "moneylines", preferred_book)
    if moneyline:
        away_ml = _format_american(moneyline.get("away_odds"))
        home_ml = _format_american(moneyline.get("home_odds"))
        if away_ml and home_ml:
            book_label = book
            points.append(f"Moneyline: {away_team} {away_ml} / {home_team} {home_ml}")

    book, spread = _first_book_lines(odds, "spreads", preferred_book)
    if spread:
        line = spread.get("home_line") or spread.get("line")
        home_price = _format_american(spread.get("home_odds"))
        away_price = _format_american(spread.get("away_odds"))
        if line not in (None, ""):
            price_part = f" ({home_price})" if home_price else ""
            away_part = f", {away_team} {away_price}" if away_price else ""
            book_label = book_label or book
            points.append(f"Spread: {home_team} {line}{price_part}{away_part}")

    book, total = _first_book_lines(odds, "totals", preferred_book)
    if total:
        line = total.get("total") or total.get("line")
        over = _format_american(total.get("over_odds"))
        under = _format_american(total.get("under_odds"))
        if line not in (None, ""):
            price_part = f" (O {over} / U {under})" if over and under else ""
            book_label = book_label or book
            points.append(f"Total: {line}{price_part}")

    if points and book_label:
        points.append(f"Quote these exact {book_label} prices; never write generic odds like 'typically -110'.")
    return points


def build_goal_outline(
    *,
    keyword: str,
    brand: str,
    bonus_code: str,
    away_team: str = "",
    home_team: str = "",
    event_label: str = "",
    start_time_display: str = "",
    event_date: str = "",
    odds: dict[str, Any] | None = None,
    same_day_titles: list[str] | None = None,
) -> list[dict]:
    """Deterministic outline matching GOAL's Top Stories brief (writer-editable)."""
    display_brand = brand or (keyword.split()[0].title() if keyword.split() else "the operator")
    matchup = event_label or (f"{away_team} vs {home_team}" if away_team and home_team else "the featured event")
    code_note = f"promo code {bonus_code}" if bonus_code else "no promo code required"
    date_bits = " - ".join(bit for bit in [event_date, start_time_display] if bit)
    match_heading = f"{matchup}" + (f" - {date_bits}" if date_bits else "")

    avoid_cannibalism = [
        f"Do not reuse angles or phrasing from today's other articles: {title}"
        for title in (same_day_titles or [])[:6]
    ]

    odds_points = format_odds_talking_points(odds, away_team, home_team, preferred_book=_brand_book_key(brand))

    return [
        {
            "level": "intro",
            "title": "",
            "talking_points": [
                f"One short paragraph, 150 characters max: {keyword}, {code_note}, the bonus amount, {matchup} and its start time, and the date",
            ],
            "avoid": ["Second paragraph", "Legal language", *avoid_cannibalism],
        },
        {"level": "shortcode", "title": "", "talking_points": [], "avoid": []},
        {
            "level": "h2",
            "title": f"{display_brand} Promo Code",
            "talking_points": [
                f"Preview {matchup} in two short paragraphs and use the keyword once more",
                "End with the offer value tied to the match",
            ],
            "avoid": ["Sign-up steps", "Terms details", *avoid_cannibalism],
        },
        {
            "level": "h2",
            "title": f"How to Use the {display_brand} Promo Code",
            "talking_points": ["Numbered sign-up steps with deposit, wager and payout mechanics"],
            "avoid": [],
        },
        {
            "level": "h2",
            "title": f"Today's Sports Betting with {display_brand}",
            "talking_points": [
                "One short paragraph establishing sports-betting expertise for this sport and framing the wager ideas below",
            ],
            "avoid": ["Repeating the offer mechanics"],
        },
        {
            "level": "h3",
            "title": match_heading,
            "talking_points": [
                "Break down the match with betting options and a clear pick, quoting only the exact prices below",
                *odds_points,
            ],
            "avoid": ["Generic odds like 'typically -110'", *avoid_cannibalism],
        },
        {
            "level": "h2",
            "title": f"Full {display_brand} Promo Code Terms and Conditions",
            "talking_points": [],
            "avoid": [],
        },
    ]


def _brand_book_key(brand: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(brand or "").lower())


def render_goal_terms_table(offer: dict[str, Any]) -> str:
    """GOAL's compliance must-have: the three-row terms table."""
    brand = str(offer.get("brand") or "Operator").strip()
    bonus_code = str(offer.get("bonus_code") or "").strip() or "No code required"
    offer_text = str(offer.get("offer_text") or offer.get("affiliate_offer") or "").strip()
    terms = str(offer.get("terms") or "").replace("\\n", " ").strip()
    if not terms:
        terms = "See the operator's site for full offer terms, eligibility rules, and restrictions."
    return (
        "<table>\n"
        f"<tr><td><strong>{escape(brand)} promo code</strong></td><td>{escape(bonus_code)}</td></tr>\n"
        f"<tr><td><strong>{escape(brand)} promo code offer</strong></td><td>{escape(offer_text)}</td></tr>\n"
        f"<tr><td><strong>{escape(brand)} promo terms and conditions</strong></td><td>{escape(terms)}</td></tr>\n"
        "</table>"
    )


def render_operator_promos_section(
    brand: str,
    promos: list[dict[str, Any]],
    primary_offer_id: str = "",
) -> str:
    """Optional 'more promos today' block from the operator's other live BAM offers."""
    display_brand = str(brand or "the operator").strip()
    extra: list[str] = []
    for promo in promos or []:
        if str(promo.get("id") or "") == str(primary_offer_id or ""):
            continue
        text = str(promo.get("offer_text") or "").strip()
        if not text:
            continue
        code = str(promo.get("bonus_code") or "").strip()
        code_part = f" (code {escape(code)})" if code else ""
        extra.append(f"<li>{escape(text)}{code_part}</li>")
        if len(extra) >= 4:
            break
    if not extra:
        return ""
    return (
        f"<h2>More {escape(display_brand)} Promos Today</h2>\n"
        f"<p>{escape(display_brand)} runs more than one live promotion. Current offers new users can weigh up:</p>\n"
        "<ul>\n" + "\n".join(extra) + "\n</ul>"
    )
