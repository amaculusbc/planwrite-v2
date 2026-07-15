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
        draw_ml = _format_american(moneyline.get("draw_odds"))
        if away_ml and home_ml:
            book_label = book
            if draw_ml:
                # Soccer's match result is three-way; omitting the draw invites
                # the writer to treat it as a two-way market.
                points.append(
                    f"Match result (1X2): {away_team} {away_ml} / Draw {draw_ml} / {home_team} {home_ml}"
                )
            else:
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
    if not odds_points:
        odds_points = [
            "No posted prices are available for this match. Build the breakdown on form, tactics, lineups, and matchup logic. "
            "Do NOT name any betting market or pick (no 'draw no bet', 'anytime goalscorer', over/under calls) - a pick "
            "without a posted price never publishes. NEVER mention that prices are missing or unavailable."
        ]

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
                "ONE short paragraph only: what is on today's card and why this match is the one to look at",
                "Do NOT analyse the match here - the analysis belongs under the match heading below",
            ],
            "avoid": [
                "Repeating the offer mechanics",
                "Team news, tactics, form, prices or any pick - those belong in the match section",
            ],
        },
        {
            # GOAL's note: the analysis belongs under the match title, not spread
            # across the H2 above it.
            "level": "h3",
            "title": match_heading,
            "talking_points": [
                "This is the article's analysis section: 2-3 paragraphs breaking the match down",
                "Argue from the posted prices below, team news and tactics, then land a clear pick",
                # GOAL's note: naming who is out is not insight on its own.
                "An absence is only worth writing if you say what it changes - who covers that role, "
                "and what it does to the team's shape, defence or attack. Name a replacement ONLY if "
                "the notes below name one; never guess who comes in.",
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


# Requested by GOAL (Tom Fuller, 8 Jul). The page blocker is verbatim from his
# note; the sticky unit follows the Ultimate SEO Guide's pattern, where a
# property's sticky CTA reuses that property's own placement/property ids
# (Action 2037/1, Raptors Republic 2315/385) - so Goal.com is 2066/326.
GOAL_PAGE_BLOCKER = '<bam-page-blocker property-id="326" identifiers="goal-canada-compliance"></bam-page-blocker>'


def render_goal_sticky_cta(offer: dict[str, Any]) -> str:
    """Sticky BAM unit for the foot of every GOAL article (US and CA)."""
    affiliate = str(offer.get("brand") or "").strip()
    if not affiliate:
        return ""
    affiliate_type = str(offer.get("affiliate_type") or "sportsbook").strip() or "sportsbook"
    internal_id = str(offer.get("internal_id") or "evergreen").strip() or "evergreen"
    return (
        '<bam-sticky-cta placement-id="2066" property-id="326" '
        'context="web-article-top-stories" '
        f'internal-id="{escape(internal_id, quote=True)}" '
        f'affiliate-type="{escape(affiliate_type, quote=True)}" '
        f'affiliate="{escape(affiliate, quote=True)}"></bam-sticky-cta>'
    )


def render_goal_article_footer(offer: dict[str, Any], *, market: str = "US") -> str:
    """Sticky CTA on every article; the compliance page blocker on US articles."""
    parts = [render_goal_sticky_cta(offer)]
    if str(market or "US").strip().upper() != "CA":
        parts.append(GOAL_PAGE_BLOCKER)
    return "\n".join(part for part in parts if part)


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


# GOAL's brief for this block: lead with the fact that these are for *all*
# players, not just new sign-ups, and name the sport.
_SPORT_WORDS: dict[str, str] = {
    "soccer": "soccer",
    "mlb": "MLB",
    "nba": "NBA",
    "nfl": "NFL",
    "nhl": "NHL",
    "ncaafb": "college football",
    "ncaamb": "college basketball",
    "wnba": "WNBA",
}


def _promos_intro(display_brand: str, bonus_code: str, keyword: str, sport: str) -> str:
    code_part = f" {escape(bonus_code)}" if bonus_code else ""
    sport_word = _SPORT_WORDS.get(str(sport or "").strip().lower(), "sports")
    return (
        f"<p>Aside from the {escape(keyword or display_brand)}{code_part} offer for new players, "
        f"{escape(display_brand)} runs many other {escape(sport_word)} promos and bonuses available "
        f"to all players, such as:</p>"
    )


def render_operator_promos_section(
    brand: str,
    promos: list[dict[str, Any]],
    primary_offer_id: str = "",
    *,
    keyword: str = "",
    bonus_code: str = "",
    sport: str = "",
    boosts: list[dict[str, Any]] | None = None,
    standing_promos: list[str] | None = None,
) -> str:
    """The operator's other live promotions - boosts and standing offers first.

    GOAL's note: this block reads better as recurring promos any player can use
    than as a second list of sign-up offers.
    """
    display_brand = str(brand or "the operator").strip()
    items: list[str] = []

    for boost in (boosts or [])[:3]:
        legs = " + ".join(str(leg) for leg in boost.get("legs") or [])
        original = str(boost.get("original_odds") or "")
        boosted = str(boost.get("boosted_odds") or "")
        if not legs or not original or not boosted:
            continue
        items.append(
            f"<li>Odds boost: {escape(legs)} - boosted from {escape(original)} to {escape(boosted)}</li>"
        )

    for promo_text in (standing_promos or [])[:3]:
        if str(promo_text).strip():
            items.append(f"<li>{escape(str(promo_text).strip())}</li>")

    seen_texts: set[str] = set()
    for promo in promos or []:
        if len(items) >= 5:
            break
        if str(promo.get("id") or "") == str(primary_offer_id or ""):
            continue
        text = str(promo.get("offer_text") or "").strip()
        if not text:
            continue
        # International/localized campaigns leak through brand-only filtering.
        if re.search(r"[¡¿ñáéíóúü]|\bapuestas\b|\bgratis\b|[£€]", text, flags=re.IGNORECASE):
            continue
        text_key = re.sub(r"\s+", " ", text.lower())
        if text_key in seen_texts:
            continue
        seen_texts.add(text_key)
        code = str(promo.get("bonus_code") or "").strip()
        code_part = f" (code {escape(code)})" if code else ""
        items.append(f"<li>{escape(text)}{code_part}</li>")

    if not items:
        return ""
    return (
        f"<h2>More {escape(display_brand)} Promos Today</h2>\n"
        + _promos_intro(display_brand, bonus_code, keyword, sport) + "\n"
        "<ul>\n" + "\n".join(items) + "\n</ul>"
    )
