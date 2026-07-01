"""Draft generation service (Execute stage).

Expands structured outlines with talking points into full article draft.
Outputs HTML format for direct publishing.
"""

import hashlib
import re
import markdown
from datetime import datetime
from html import escape
from typing import AsyncGenerator, Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.services.llm import generate_completion, generate_completion_structured
from app.services.rag import query_articles
from app.services.internal_links import (
    format_links_markdown,
    get_links_by_urls,
    get_operator_evergreen_link,
    suggest_links_for_section,
)
from app.services.compliance import get_disclaimer_for_state
from app.services.bam_offers import PROPERTIES, normalize_bam_affiliate_type, render_bam_offer_block
from app.services.content_guidelines import get_style_instructions, get_temperature_by_section
from app.services.style import get_rag_usage_guidance
from app.services.switchboard_links import inject_switchboard_links, build_switchboard_url
from app.services.operator_facts import get_operator_facts
from app.services.operator_profile import (
    CONTENT_MODE_DFS,
    CONTENT_MODE_PREDICTION_MARKET,
    CONTENT_MODE_SPORTSBOOK,
    get_content_mode_offer,
)
from app.services.offer_parsing import (
    extract_bonus_amount,
    extract_bonus_expiration_days,
    extract_excluded_states_from_terms,
    extract_minimum_odds,
    extract_offer_amount_details,
    extract_states_from_terms,
    extract_wagering_requirement,
    parse_states,
)


TOP_STORY_TRACKING_TAG = """<script>
  gtag('event', 'view_top_story');
</script>"""


def today_long(tz: str = "US/Eastern") -> str:
    """Get today's date in long format."""
    try:
        now = datetime.now(ZoneInfo(tz))
    except Exception:
        now = datetime.now()
    return f"{now.strftime('%A')}, {now.strftime('%B')} {now.day}, {now.year}"


def md_to_html(md_text: str) -> str:
    """Convert markdown to HTML."""
    return markdown.markdown(
        md_text,
        extensions=["extra", "sane_lists"],
        output_format="html5",
    )


def _count_keyword(text: str, keyword: str) -> int:
    if not text or not keyword:
        return 0
    pattern = re.compile(re.escape(keyword), re.IGNORECASE)
    return len(pattern.findall(text))


def _shortcode_index(level: str) -> int:
    """Map shortcode tokens to selected offer index: shortcode -> 0, shortcode_1 -> 1."""
    raw = str(level or "").strip().lower()
    if raw == "shortcode":
        return 0
    match = re.match(r"shortcode[_-](\d+)$", raw)
    if not match:
        return 0
    try:
        return int(match.group(1))
    except ValueError:
        return 0


def _naturalize_bc_core_editorial_point(point: str) -> str:
    """Rewrite internal BC Core notes into reader-facing editorial language."""
    text = str(point or "").strip()
    if not text:
        return ""
    text = re.sub(r"\bBC Core\b", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(
        r"^(?P<team>.+?) is (?P<record>\d+-\d+) ATS in .*?(?:Last10|last 10).*?trend sample\.$",
        lambda m: f"{m.group('team')} has gone {m.group('record')} against the spread over the last 10 games.",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"^(?P<team>.+?) is (?P<record>\d+-\d+) straight up in .*?trend sample\.$",
        lambda m: f"{m.group('team')} is {m.group('record')} straight up in the recent sample.",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"^In .*?matchup trend sample against (?P<opp>.+?), (?P<team>.+?) is (?P<record>\d+-\d+) ATS\.$",
        lambda m: f"Against {m.group('opp')}, {m.group('team')} is {m.group('record')} against the spread in the matchup sample.",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"^In .*?matchup trend sample against (?P<opp>.+?), (?P<team>.+?) is (?P<record>\d+-\d+) straight up\.$",
        lambda m: f"Against {m.group('opp')}, {m.group('team')} is {m.group('record')} straight up in the matchup sample.",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"has (?P<count>\d+) active .*?injury listings",
        lambda m: f"has {m.group('count')} active injury listings",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\s*weather at the venue points to\s*", " Weather context points to ", text, flags=re.IGNORECASE)
    text = re.sub(
        r"^Market percents show (?P<pct>\d+)% of (?P<kind>ticket|tickets|handle|market activity) on (?P<market>[^.]+?) tied to (?P<side>[^.]+)\.$",
        lambda m: f"Ticket data shows {m.group('pct')}% of {('tickets' if m.group('kind').lower().startswith('ticket') else m.group('kind').lower())} on {m.group('market')} tied to {m.group('side')}.",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\bbaseball_pitchingouts\b", "pitching outs", text, flags=re.IGNORECASE)
    text = re.sub(r"\bbaseball_pitchinghits\b", "pitching hits allowed", text, flags=re.IGNORECASE)
    text = re.sub(r"\bbaseball_pitchingruns\b", "runs allowed", text, flags=re.IGNORECASE)
    text = re.sub(r"\bbaseball_pitchingstrikeouts\b", "strikeouts", text, flags=re.IGNORECASE)
    text = text.replace("FromRight", "from right field").replace("FromLeft", "from left field")
    text = text.replace("LeftToRight", "left-to-right").replace("RightToLeft", "right-to-left")
    text = re.sub(r"\btrend sample\b", "recent sample", text, flags=re.IGNORECASE)
    text = re.sub(r"\boverall sample\b", "recent sample", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*\bin the selected event\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s{2,}", " ", text).strip()
    if text and not text.endswith("."):
        text += "."
    return text


def _bc_core_point_category(point: str) -> str:
    """Classify an editorial point so surfaced facts vary in type."""
    text = str(point or "").lower()
    if any(token in text for token in ["weather", "degrees", "wind", "precipitation", "cloudy", "rain"]):
        return "weather"
    if any(token in text for token in ["listed score", "latest listed score", "completed games", "went "]):
        return "matchup"
    if any(token in text for token in ["lineup", "formation", "starters"]):
        return "lineup"
    if any(token in text for token in ["injury", "absence", "out", "questionable"]):
        return "injury"
    if any(token in text for token in ["against the spread", "straight up", "ats", "covered", "last 10", "recent sample", "matchup sample"]):
        return "trend"
    if any(token in text for token in ["projects for", "projection", "projected stat"]):
        return "projection"
    if any(token in text for token in ["dfs lines", "fantasy users", "prop angle"]):
        return "dfs_line"
    if any(token in text for token in ["market percents", "tickets", "handle", "market activity"]):
        return "market_percent"
    if any(token in text for token in ["points per game", "scoring margin", "rebounds", "assists", "true shooting", "efg"]):
        return "stat"
    if any(token in text for token in ["playoffs", "season", "window", "network", "nbc", "espn", "peacock"]):
        return "schedule"
    return "general"


def _prioritize_bc_core_points(points: list[str], max_points: int) -> list[str]:
    """Prefer a spread of categories before taking extra same-type notes."""
    category_priority = {
        "matchup": 0,
        "lineup": 1,
        "projection": 2,
        "dfs_line": 3,
        "market_percent": 4,
        "trend": 5,
        "stat": 6,
        "injury": 7,
        "weather": 8,
        "schedule": 9,
        "general": 10,
    }
    ordered_points = sorted(points, key=lambda point: category_priority.get(_bc_core_point_category(point), 99))
    chosen: list[str] = []
    used_categories: set[str] = set()
    for point in ordered_points:
        category = _bc_core_point_category(point)
        if category not in used_categories:
            chosen.append(point)
            used_categories.add(category)
        if len(chosen) >= max_points:
            return chosen
    for point in ordered_points:
        if point in chosen:
            continue
        chosen.append(point)
        if len(chosen) >= max_points:
            break
    return chosen


def _filter_bc_core_points_for_mode(
    points: list[str],
    *,
    prediction_market: bool = False,
    dfs_mode: bool = False,
) -> list[str]:
    """Keep internal data notes aligned with the article's content mode."""
    if not points:
        return []
    blocked_categories: set[str] = set()
    if prediction_market:
        blocked_categories.update({"dfs_line", "trend"})
    elif dfs_mode:
        blocked_categories.update({"market_percent", "trend"})
    if not blocked_categories:
        return points
    return [point for point in points if _bc_core_point_category(point) not in blocked_categories]


def _select_bc_core_editorial_points(
    bc_core_context: dict[str, Any] | None,
    *,
    section_kind: str,
    max_points: int = 3,
    prediction_market: bool = False,
    dfs_mode: bool = False,
) -> list[str]:
    """Pick a few BC-backed editorial notes to surface in copy."""
    bc_core_context = bc_core_context or {}
    expertise = bc_core_context.get("expertise") if isinstance(bc_core_context, dict) else {}
    event = bc_core_context.get("event") if isinstance(bc_core_context, dict) else {}
    if not isinstance(expertise, dict) or not expertise.get("matched"):
        return []

    raw_points = [_naturalize_bc_core_editorial_point(point) for point in (expertise.get("editorial_points") or [])]
    points = [point for point in raw_points if point]
    if isinstance(event, dict) and event.get("matched"):
        if event.get("network"):
            points.insert(0, f"The matchup is set for {event.get('network')}.")
        season_name = str(event.get("season_name") or "").strip()
        schedule_name = str(event.get("schedule_name") or "").strip()
        if season_name or schedule_name:
            label = " ".join(part for part in [season_name, schedule_name] if part).strip()
            if label:
                points.insert(0, f"This spot falls in the {label} window.")

    deduped: list[str] = []
    seen: set[str] = set()
    for point in points:
        normalized = re.sub(r"\s+", " ", point.lower()).strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            deduped.append(point)
    deduped = _filter_bc_core_points_for_mode(
        deduped,
        prediction_market=prediction_market,
        dfs_mode=dfs_mode,
    )

    if section_kind == "claim":
        return _prioritize_bc_core_points(deduped, 1)
    if section_kind == "intro":
        return _prioritize_bc_core_points(deduped, min(max_points, 2))
    return _prioritize_bc_core_points(deduped, max_points)


def _bc_core_marker_coverage(text: str, points: list[str]) -> int:
    """Count how many selected BC-backed notes appear in visible copy."""
    haystack = str(text or "").lower()
    if not haystack or not points:
        return 0
    coverage = 0
    for point in points:
        matched = False
        for marker in re.findall(r"\b\d+(?:\.\d+)?(?:-\d+)?%?\b", point):
            if marker.lower() in haystack:
                matched = True
        for marker in re.findall(r"\b(?:playoffs?|nbc|espn|peacock|record|straight up|against the spread|injury|absence|lineup|formation|starter|starters|score|weather|projects?|projection|dfs|fantasy|market percents?|tickets?|handle|market activity)\b", point, flags=re.IGNORECASE):
            if marker.lower() in haystack:
                matched = True
        if matched:
            coverage += 1
    return coverage


def _inject_bc_core_points_into_html(html: str, points: list[str], *, max_injections: int = 2) -> str:
    """Guarantee one or more natural BC-backed sentences appear in visible copy."""
    cleaned_points = [_naturalize_bc_core_editorial_point(point) for point in points if _naturalize_bc_core_editorial_point(point)]
    cleaned_points = cleaned_points[:max_injections]
    if not cleaned_points:
        return html
    paragraphs = re.findall(r"<p>.*?</p>", html, flags=re.IGNORECASE | re.DOTALL)
    injection = " ".join(cleaned_points)
    if len(paragraphs) >= 2:
        target = paragraphs[1]
        updated = re.sub(r"</p>\s*$", f" {injection}</p>", target, count=1, flags=re.IGNORECASE)
        return html.replace(target, updated, 1)
    if paragraphs:
        target = paragraphs[0]
        updated = re.sub(r"</p>\s*$", f" {injection}</p>", target, count=1, flags=re.IGNORECASE)
        return html.replace(target, updated, 1)
    return f"<p>{injection}</p>{html}"


def _normalize_article_preferences(article_preferences: dict[str, Any] | None = None) -> dict[str, Any]:
    """Normalize writer-controlled preferences used during draft generation."""
    prefs = dict(article_preferences or {})
    section_count = prefs.get("section_count")
    try:
        section_count = int(section_count) if section_count not in (None, "") else 5
    except (TypeError, ValueError):
        section_count = 5
    section_count = max(3, min(section_count, 6))
    return {
        "market": str(prefs.get("market") or "US").strip().upper() or "US",
        "secondary_keywords": [str(x).strip() for x in (prefs.get("secondary_keywords") or []) if str(x).strip()][:6],
        "preferred_internal_urls": [str(x).strip() for x in (prefs.get("preferred_internal_urls") or []) if str(x).strip()][:5],
        "section_count": section_count,
        "allow_h3": bool(prefs.get("allow_h3", False)),
        "include_daily_promos": bool(prefs.get("include_daily_promos", False)),
        "include_bullets": bool(prefs.get("include_bullets", False)),
        "include_table": bool(prefs.get("include_table", False)),
        "enforce_active_voice": prefs.get("enforce_active_voice", True) is not False,
        "structure_notes": str(prefs.get("structure_notes") or "").strip(),
    }


def _dedupe_link_specs_by_url(links):
    """Preserve order while de-duplicating InternalLinkSpec-like objects by URL."""
    seen: set[str] = set()
    kept = []
    for link in links or []:
        url = str(getattr(link, "url", "") or "").strip().lower()
        if not url or url in seen:
            continue
        seen.add(url)
        kept.append(link)
    return kept


def _extract_common_phrases(text: str) -> list[str]:
    """Extract common filler phrases to avoid repetition."""
    if not text:
        return []
    patterns = [
        r"To (?:qualify|claim|get|take advantage|access|receive|sign up) (?:for|this|the) [\w\s]{1,30}",
        r"In order to [\w\s]{1,30}",
        r"(?:This|The) (?:offer|promo|bonus) (?:is|allows|gives|provides) [\w\s]{1,30}",
        r"(?:New|Eligible) (?:users|customers|bettors) can [\w\s]{1,30}",
        r"available (?:to|for) (?:new|eligible) [\w\s]{1,30}",
    ]
    found: list[str] = []
    for pattern in patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        found.extend([m.strip() for m in matches if len(m.strip()) > 10])
    return list(set(found))[:6]


def _normalize_heading(text: str) -> str:
    """Normalize a heading for de-duplication checks."""
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _sanitize_heading_text(text: str) -> str:
    """Strip links/HTML from section headings so headings stay plain text."""
    if not text:
        return ""
    cleaned = re.sub(r"<[^>]+>", " ", text)
    cleaned = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip(" -:\t\r\n")


def _html_to_plain_text(html: str) -> str:
    """Collapse HTML into compact plain text for internal prompt references."""
    if not html:
        return ""
    text = re.sub(r"<br\s*/?>", " ", html, flags=re.IGNORECASE)
    text = re.sub(r"</p>\s*<p>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"</li>\s*<li>", "; ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _body_word_count_for_editorial_target(html: str) -> int:
    """Count article body words excluding signup steps, shortcodes, terms, scripts, and disclaimers."""
    if not html:
        return 0
    count = 0
    blocked_heading = ""
    for match in re.finditer(
        r"<h[1-6]\b[^>]*>(.*?)</h[1-6]>|<p\b[^>]*>(.*?)</p>",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        heading = match.group(1)
        paragraph = match.group(2)
        if heading is not None:
            blocked_heading = _html_to_plain_text(heading).lower()
            continue
        if paragraph is None:
            continue
        paragraph_lc = paragraph.lower()
        plain = _html_to_plain_text(paragraph)
        plain_lc = plain.lower()
        if not plain:
            continue
        if any(token in blocked_heading for token in ("sign up", "sign-up", "signup", "claim", "terms", "conditions", "fine print", "rules")):
            continue
        if "[bam-inline-promotion" in paragraph_lc or "switchboard_tracking" in paragraph_lc:
            continue
        if "gambling problem" in plain_lc or "terms apply" in plain_lc or "21+" in plain_lc:
            continue
        count += len(re.findall(r"\b[\w'-]+\b", plain))
    return count


def _first_paragraph_plain_text(html: str) -> str:
    """Return the first paragraph as compact plain text for validation."""
    if not html:
        return ""
    match = re.search(r"<p>(.*?)</p>", html, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return _html_to_plain_text(html)
    return _html_to_plain_text(match.group(1))


def _sportsbook_claim_fact_markers(
    bet_example_data: dict[str, Any] | None,
) -> dict[str, str]:
    """Build the exact markers a sportsbook worked example must preserve."""
    data = dict(bet_example_data or {})
    if not data:
        return {}
    selection = str(data.get("selection") or "").strip()
    odds_raw = data.get("odds")
    bet_amount_raw = data.get("bet_amount")
    if not selection or bet_amount_raw in (None, "") or odds_raw in (None, ""):
        return {}

    markers: dict[str, str] = {"selection": selection}
    try:
        bet_amount = float(bet_amount_raw)
        markers["bet_amount"] = f"${bet_amount:.0f}"
    except (TypeError, ValueError):
        markers["bet_amount"] = str(bet_amount_raw).strip()

    try:
        odds_value = int(float(odds_raw))
        markers["odds"] = f"{odds_value:+d}"
    except (TypeError, ValueError):
        markers["odds"] = str(odds_raw).strip()

    try:
        profit = float(data.get("potential_profit"))
    except (TypeError, ValueError):
        try:
            odds_value = int(float(odds_raw))
            bet_amount = float(bet_amount_raw)
            if odds_value > 0:
                profit = (bet_amount * odds_value) / 100.0
            else:
                profit = (bet_amount * 100.0) / abs(odds_value) if odds_value != 0 else 0.0
        except (TypeError, ValueError, ZeroDivisionError):
            profit = None
    if profit is not None:
        total_return = float(bet_amount_raw) + profit
        markers["profit"] = f"${profit:.2f}"
        markers["total_return"] = f"${total_return:.2f}"
    return markers


def _sportsbook_claim_matches_input(
    html: str,
    bet_example_data: dict[str, Any] | None,
) -> bool:
    """Return True when the first sportsbook example paragraph keeps the exact input facts."""
    markers = _sportsbook_claim_fact_markers(bet_example_data)
    if not markers:
        return True
    first_para = _first_paragraph_plain_text(html).lower()
    required = [
        markers.get("bet_amount", "").lower(),
        markers.get("selection", "").lower(),
        markers.get("odds", "").lower(),
    ]
    return all(marker and marker in first_para for marker in required)


def _preferred_code_term(brand: str) -> str:
    """Return the preferred keyword label for operators with house-style rules."""
    if str(brand or "").strip().lower() == "bet365":
        return "bonus code"
    return "promo code"


def _normalize_brand_keyword_text(text: str, brand: str) -> str:
    """Apply operator-specific keyword wording rules in visible copy."""
    if not text:
        return text
    if str(brand or "").strip().lower() == "bet365":
        return re.sub(r"\bbet365\s+promo code\b", "bet365 bonus code", text, flags=re.IGNORECASE)
    return text


def _offer_value_summary(
    offer: dict[str, Any] | None,
    *,
    prediction_market: bool = False,
    dfs_mode: bool = False,
) -> str:
    """Return a concise offer summary without repeating the raw offer headline."""
    offer = offer or {}
    offer_text = str(offer.get("offer_text") or offer.get("affiliate_offer") or "").strip()
    if not offer_text:
        return "the listed offer"

    details = extract_offer_amount_details(offer_text)
    reward_amount = (
        offer.get("bonus_amount")
        or offer.get("reward_amount")
        or details.get("reward_amount")
        or extract_bonus_amount(offer_text)
    )
    reward_label = str(offer.get("reward_label") or details.get("reward_label") or "").strip().lower()
    qualifying_amount = str(offer.get("qualifying_amount") or details.get("qualifying_amount") or "").strip()
    qualifying_action = str(offer.get("qualifying_action") or details.get("qualifying_action") or "").strip().lower()

    reward_noun = (
        "promo credits"
        if prediction_market
        else "bonus entries"
        if dfs_mode
        else "bonus bets"
    )
    if reward_label:
        reward_noun = reward_label

    if reward_amount and qualifying_amount and qualifying_action:
        action_label = qualifying_action.replace("_", " ")
        return f"{reward_amount} in {reward_noun} after a {qualifying_amount} {action_label}"
    if reward_amount:
        return f"{reward_amount} in {reward_noun}"
    return offer_text


def _is_signup_heading(title_lower: str) -> bool:
    """Return True if the section title indicates sign-up steps."""
    if not title_lower:
        return False
    if re.search(r"^\s*how to claim\b(?!.*\bfor\b)", title_lower):
        return True
    return bool(re.search(
        r"\b(sign ?up|sign-up|signup|register|registration|create an? account|open an? account|"
        r"get started|set ?up|setup|how to sign|how to register|how to join)\b",
        title_lower,
    ))


def _is_claim_heading(title_lower: str, is_signup: bool) -> bool:
    """Return True if the section title indicates a claim/usage example."""
    if is_signup:
        return False
    if not title_lower:
        return False
    return bool(re.search(
        r"\b(how to claim|claim|worked example|bet example|example|how to use)\b|"
        r"(bonus bets play out|welcome offer looks like|offer in action)",
        title_lower,
    ))


def _is_daily_promos_heading(title_lower: str) -> bool:
    """Return True for any daily-promo placeholder heading variant."""
    if not title_lower:
        return False
    return any(
        phrase in title_lower
        for phrase in (
            "daily promo",
            "promos today",
            "promo update placeholder",
            "daily promos placeholder",
            "today's promo placeholder",
            "promo placeholder",
        )
    )


def _get_content_mode(
    *,
    offer: dict[str, Any] | None = None,
    offers: list[dict[str, Any]] | None = None,
    keyword: str = "",
    title: str = "",
) -> str:
    """Return content mode for the article context."""
    if offer:
        mode = get_content_mode_offer(offer, keyword=keyword, title=title)
        if mode != CONTENT_MODE_SPORTSBOOK:
            return mode
    for candidate in offers or []:
        mode = get_content_mode_offer(candidate, keyword=keyword, title=title)
        if mode != CONTENT_MODE_SPORTSBOOK:
            return mode
    # Fallback for cases where offer payload is unavailable.
    return get_content_mode_offer(None, keyword=keyword, title=title)


def _prediction_market_safe_text(text: str) -> str:
    """Replace sportsbook-heavy wording with prediction-market language."""
    if not text:
        return text
    replacements = [
        (r"\bbetting\b", "market"),
        (r"\bbet\b", "trade"),
        (r"\bsportsbooks?\b", "operators"),
        (r"\bbonus bets?\b", "promo credits"),
    ]
    result = text
    for pattern, repl in replacements:
        result = re.sub(pattern, repl, result, flags=re.IGNORECASE)
    return result


def _dfs_safe_text(text: str) -> str:
    """Replace sportsbook-heavy wording with DFS language."""
    if not text:
        return text
    replacements = [
        (r"\bbetting\b", "daily fantasy"),
        (r"\bbets\b", "entries"),
        (r"\bbet\b", "entry"),
        (r"\bwager(?:ing)?\b", "entry"),
        (r"\bbonus bets?\b", "bonus entries"),
        (r"\bsportsbooks?\b", "DFS apps"),
        (r"\bplace a bet\b", "enter a contest"),
        (r"\bfirst bet\b", "first entry"),
        (r"\bbet responsibly\b", "play responsibly"),
    ]
    result = text
    for pattern, repl in replacements:
        result = re.sub(pattern, repl, result, flags=re.IGNORECASE)
    return result


def _apply_content_mode_language_guardrails(html: str, content_mode: str) -> str:
    """Deterministically clean up sportsbook wording for non-sportsbook operators."""
    if not html:
        return html
    if content_mode == CONTENT_MODE_SPORTSBOOK:
        return html

    replacer = (
        _prediction_market_safe_text
        if content_mode == CONTENT_MODE_PREDICTION_MARKET
        else _dfs_safe_text
        if content_mode == CONTENT_MODE_DFS
        else None
    )
    if replacer is None:
        return html

    protected_shortcodes: dict[str, str] = {}

    def _protect_shortcode(match: re.Match[str]) -> str:
        key = f"__BAM_SHORTCODE_{len(protected_shortcodes)}__"
        protected_shortcodes[key] = match.group(0)
        return key

    protected_html = re.sub(
        r"\[bam-inline-promotion[^\]]+\]",
        _protect_shortcode,
        html,
        flags=re.IGNORECASE,
    )

    # Only rewrite visible text nodes so href/src attributes and URLs remain intact.
    tokens = re.findall(r"<[^>]+>|[^<]+", protected_html, flags=re.DOTALL)
    out: list[str] = []
    for token in tokens:
        out.append(token if token.startswith("<") else replacer(token))
    result = "".join(out)
    for key, shortcode in protected_shortcodes.items():
        result = result.replace(key, shortcode)
    return result


def _adapt_disclaimer_for_prediction_market(disclaimer: str) -> str:
    """Tone down sportsbook wording for prediction-market pages."""
    if not disclaimer:
        return disclaimer
    out = re.sub(r"please bet responsibly\.?", "Please participate responsibly.", disclaimer, flags=re.IGNORECASE)
    return out


def _adapt_disclaimer_for_dfs(disclaimer: str) -> str:
    """Tone down sportsbook wording for DFS pages."""
    if not disclaimer:
        return disclaimer
    out = re.sub(r"^\s*21\+\.\s*", "", disclaimer, flags=re.IGNORECASE)
    out = re.sub(r"gambling problem\?", "Need help?", out, flags=re.IGNORECASE)
    out = re.sub(r"please bet responsibly\.?", "Please play responsibly.", out, flags=re.IGNORECASE)
    return out


def _inject_switchboard_links_for_offers(
    html_output: str,
    offers: list[dict[str, Any]],
    state: str,
    property_key: str = "action_network",
    max_links: int = 12,
) -> str:
    """Inject switchboard links for offers with a GLOBAL cap across the article."""
    if not html_output or not offers:
        return html_output

    for offer in offers:
        if _count_switchboard_links(html_output) >= max_links:
            break
        brand = offer.get("brand", "")
        bonus_code = offer.get("bonus_code", "")
        switchboard_url = _offer_switchboard_url(offer, state=state, property_key=property_key)
        if not (brand and switchboard_url):
            continue
        remaining = max(0, max_links - _count_switchboard_links(html_output))
        if remaining <= 0:
            break
        html_output = inject_switchboard_links(
            html_output,
            brand=brand,
            bonus_code=bonus_code,
            switchboard_url=switchboard_url,
            max_links=remaining,
        )
    return html_output


def _offer_switchboard_url(
    offer: dict[str, Any] | None,
    *,
    state: str,
    property_key: str,
) -> str:
    """Return a property-correct switchboard URL for an offer."""
    offer = offer or {}
    affiliate_id = offer.get("affiliate_id")
    campaign_id = offer.get("campaign_id")
    if affiliate_id and campaign_id:
        prop_key = str(property_key or "action_network").strip().lower()
        prop = PROPERTIES.get(prop_key)
        if not prop:
            return ""
        return build_switchboard_url(
            affiliate_id,
            campaign_id,
            state_code=state if state != "ALL" else "",
            property_id=prop.get("property_id", "1"),
            switchboard_domain=prop.get("switchboard_domain", ""),
        )

    return str(offer.get("switchboard_link") or "").strip()


def _shortcode_attr(shortcode: str, attr: str) -> str:
    match = re.search(rf'\b{re.escape(attr)}\s*=\s*(["\'])(.*?)\1', shortcode or "", flags=re.IGNORECASE)
    return match.group(2).strip() if match else ""


def _build_property_correct_bam_shortcode(offer: dict[str, Any], property_key: str) -> str:
    """Build a BAM shortcode with the selected property's placement/property IDs."""
    prop = PROPERTIES.get(str(property_key or "action_network").strip().lower())
    if not prop:
        return str(offer.get("shortcode") or "").strip()
    brand = str(offer.get("brand") or "").strip()
    if not brand:
        return ""
    internal_id = str(
        offer.get("internal_id")
        or _shortcode_attr(str(offer.get("shortcode") or ""), "internal-id")
        or "evergreen"
    ).strip()
    affiliate_type = normalize_bam_affiliate_type(
        offer.get("affiliate_type")
        or _shortcode_attr(str(offer.get("shortcode") or ""), "affiliate-type")
        or "sportsbook"
    )
    context = str(prop.get("default_context") or "web-article-top-stories").strip()
    return (
        f'[bam-inline-promotion placement-id="{prop.get("placement_id", "2037")}" '
        f'property-id="{prop.get("property_id", "1")}" '
        f'context="{context}" internal-id="{escape(internal_id, quote=True)}" '
        f'affiliate-type="{escape(affiliate_type, quote=True)}" '
        f'affiliate="{escape(brand, quote=True)}"]'
    )


def _is_property_correct_bam_shortcode(shortcode: str, property_key: str) -> bool:
    """Return True if a shortcode's property, placement and affiliate type are safe."""
    if not shortcode or "[bam-inline-promotion" not in shortcode.lower():
        return False
    prop = PROPERTIES.get(str(property_key or "action_network").strip().lower())
    if not prop:
        return False
    return (
        _shortcode_attr(shortcode, "property-id") == str(prop.get("property_id"))
        and _shortcode_attr(shortcode, "placement-id") == str(prop.get("placement_id"))
        and _shortcode_attr(shortcode, "affiliate-type") == normalize_bam_affiliate_type(_shortcode_attr(shortcode, "affiliate-type"))
    )


def _build_signup_list(
    brand: str,
    has_code: bool,
    code_strong: str,
    state: str = "",
    event_context: str = "",
    signup_url: str = "",
    qualifying_amount: str = "",
    minimum_odds: str = "",
    reward_phrase: str = "",
    prediction_market: bool = False,
    dfs_mode: bool = False,
    variation_key: str = "",
    market: str = "US",
    offer_mechanic: str = "generic",
) -> str:
    """Build a deterministic 5-step signup list as HTML."""
    brand_label = brand or ("the operator" if prediction_market else "the DFS app" if dfs_mode else "the sportsbook")
    event_label = _extract_featured_label_from_event_context(event_context)
    is_canada_market = str(market or "US").strip().upper() == "CA"
    location_label = state if state and state != "ALL" else "your province" if is_canada_market else "your state"
    mechanics_ref = (
        "how market contracts settle"
        if prediction_market
        else "how pick'em entries work"
        if dfs_mode
        else ""
    )
    qualifying_display = str(qualifying_amount or "").strip()
    min_odds_display = str(minimum_odds or "").strip()
    reward_display = str(reward_phrase or "").strip()

    step_one_options = [
        f'Tap this <a data-id="switchboard_tracking" href="{signup_url}" rel="nofollow">{brand_label} sign-up link</a> to start registration in {location_label}.',
        f'Open the <a data-id="switchboard_tracking" href="{signup_url}" rel="nofollow">{brand_label} registration page</a> and confirm it is available in {location_label}.',
        f'Use the <a data-id="switchboard_tracking" href="{signup_url}" rel="nofollow">{brand_label} offer link</a> to begin the account flow for {location_label}.',
    ] if signup_url else [
        f"Open {brand_label} in {location_label} and start registration.",
        f"Go to {brand_label} and begin the account flow for {location_label}.",
        f"Start from {brand_label}'s registration screen and confirm access in {location_label}.",
    ]
    step_two_options = [
        f"Create your account and enter {code_strong} in the promo field.",
        f"Add your email and password, then apply {code_strong} before you finish signup.",
        f"Register your account and attach {code_strong} when the promo-code field appears.",
    ] if has_code else [
        "Create your account; no promo code is required for this offer.",
        "Register with your basic account details and continue without a promo code.",
        "Set up the account profile, then continue because this offer does not require a code.",
    ]
    verify_options = [
        "Complete identity and location checks, then log in.",
        "Verify your identity and location so the app can show eligible offers.",
        "Finish the required account verification before adding funds or entering a market.",
    ]
    sportsbook_fund_options = [
        f"Deposit at least {qualifying_display} using an approved payment method before choosing your first bet.",
        f"Add at least {qualifying_display}, then head to the sportsbook lobby for the qualifying bet.",
        f"Fund the account with {qualifying_display} or more so the first wager can satisfy the offer.",
    ] if qualifying_display else [
        "Deposit the required amount using an approved payment method.",
        "Fund the account before choosing your first wager.",
        "Add the minimum required deposit, then head to the sportsbook lobby.",
    ]
    fund_options = (
        [
            "Add funds or coins using an approved payment method.",
            "Load the account with the required amount before opening a position.",
            "Fund the account so you can complete the qualifying market action.",
        ]
        if prediction_market
        else [
            "Add funds if the app requires a paid entry.",
            "Fund the account with an approved payment method before building your entry.",
            "Load the account, then move to the contest lobby.",
        ]
        if dfs_mode
        else sportsbook_fund_options
    )

    sportsbook_requirement = ""
    if qualifying_display and min_odds_display:
        sportsbook_requirement = f" The first wager must be at least {qualifying_display} and meet the {min_odds_display} minimum odds requirement."
    elif qualifying_display:
        sportsbook_requirement = f" The first wager must be at least {qualifying_display}."
    elif min_odds_display:
        sportsbook_requirement = f" The first wager must meet the {min_odds_display} minimum odds requirement."
    if offer_mechanic == "bet_and_get":
        bonus_timing = (
            f" Once you place the qualifying wager, confirm the {reward_display} posts according to the offer terms."
            if reward_display
            else " Once you place the qualifying wager, confirm the bonus timing in the promo tracker."
        )
    elif offer_mechanic == "money_back":
        bonus_timing = (
            f" If the first bet loses, confirm the losing stake is matched with {reward_display} after settlement according to the offer terms."
            if reward_display
            else " If the first bet loses, confirm the matched bonus timing after settlement in the promo tracker."
        )
    else:
        bonus_timing = (
            f" After that qualifying bet settles, confirm the {reward_display} posts according to the offer terms."
            if reward_display
            else " After that qualifying bet settles, confirm the bonus timing in the promo tracker."
        )
    final_options = (
        [
            f"Open a qualifying position on {event_label or 'the featured market'} and review contract terms before settlement.",
            f"Choose a contract for {event_label or 'the featured market'}, confirm the position size, and submit it before the market closes.",
            f"Use the first {qualifying_display or 'qualifying'} action on {event_label or 'an eligible market'}, then track how the contract settles.",
        ]
        if prediction_market
        else [
            f"Enter your first {qualifying_display or 'qualifying'} fantasy entry for {event_label or 'the featured slate'} to trigger the bonus entries.",
            f"Build an eligible entry for {event_label or 'the featured slate'}, review the picks, and submit it before lock.",
            f"Join a qualifying contest on {event_label or 'the featured slate'} and confirm how bonus entries apply.",
        ]
        if dfs_mode
        else [
            f"Place your first qualifying bet on {event_label or 'the featured event'} or any eligible market.{sportsbook_requirement}{bonus_timing}",
            f"Choose an eligible wager for {event_label or 'the featured event'}, confirm the stake, and submit it before the market closes.{sportsbook_requirement}{bonus_timing}",
            f"Make the first qualifying bet tied to the offer.{sportsbook_requirement}{bonus_timing}",
        ]
    )
    seed_key = variation_key or f"{brand}|{state}|{event_label}|{prediction_market}|{dfs_mode}"
    steps = [
        _choose_variant(seed_key, "signup_1", step_one_options, brand_label, location_label),
        _choose_variant(seed_key, "signup_2", step_two_options, brand_label, code_strong),
        _choose_variant(seed_key, "signup_3", verify_options, brand_label),
        _choose_variant(seed_key, "signup_4", fund_options, brand_label),
        _choose_variant(seed_key, "signup_5", final_options, brand_label, event_label, mechanics_ref),
    ]

    items = "\n".join(f"<li>{step}</li>" for step in steps)
    return f"<ol>\n{items}\n</ol>"

def _steps_to_html(steps: list[str]) -> str:
    items = "\n".join(f"<li>{step}</li>" for step in steps)
    return f"<ol>\n{items}\n</ol>"


def _strip_placeholder_hash_links(html: str) -> str:
    """Remove placeholder anchor links like href=\"#\" from generated HTML."""
    if not html:
        return html
    return re.sub(
        r'<a\b[^>]*href\s*=\s*(["\'])#\1[^>]*>(.*?)</a>',
        r"\2",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )


def _count_switchboard_links(html: str) -> int:
    """Count injected switchboard tracking links in HTML."""
    if not html:
        return 0
    return len(re.findall(r'data-id\s*=\s*(["\'])switchboard_tracking\1', html, flags=re.IGNORECASE))


def _is_switchboard_link(url: str, attrs_text: str = "") -> bool:
    """Return True when a link is a switchboard CTA, even without tracking attrs."""
    url_lc = (url or "").strip().lower()
    attrs_lc = (attrs_text or "").lower()
    return (
        "switchboard_tracking" in attrs_lc
        or ("switchboard." in url_lc and "/offers" in url_lc)
        or ("us-betting.goal.com/offers" in url_lc)
    )


def _count_non_switchboard_links(html: str) -> int:
    """Count non-switchboard links in HTML."""
    if not html:
        return 0
    links = re.findall(r'<a\b([^>]*)href\s*=\s*(["\'])(https?://[^"\']+)\2([^>]*)>', html, flags=re.IGNORECASE)
    count = 0
    for before_attrs, _, url, after_attrs in links:
        attrs_text = f"{before_attrs} {after_attrs}".lower()
        if _is_switchboard_link(url, attrs_text):
            continue
        count += 1
    return count


def _link_first_keyword_internal(
    html: str,
    keyword: str,
    url: str,
) -> str:
    """Link the first exact keyword mention in body text to an internal evergreen URL.

    Skips headings and existing anchors so it does not break CTA/link injection logic.
    """
    if not html or not keyword or not url:
        return html

    tokens = re.findall(r"<[^>]+>|[^<]+", html, flags=re.DOTALL)
    pattern = re.compile(re.escape(keyword), flags=re.IGNORECASE)

    inside_anchor = 0
    inside_heading = 0
    inserted = False
    out: list[str] = []

    for token in tokens:
        if token.startswith("<"):
            tag = token.strip().lower()
            if re.match(r"<a\b", tag):
                inside_anchor += 1
            elif re.match(r"</a\b", tag):
                inside_anchor = max(0, inside_anchor - 1)
            elif re.match(r"<h[1-6]\b", tag):
                inside_heading += 1
            elif re.match(r"</h[1-6]\b", tag):
                inside_heading = max(0, inside_heading - 1)
            out.append(token)
            continue

        if not inserted and not inside_anchor and not inside_heading and pattern.search(token):
            token = pattern.sub(lambda m: f'<a href="{url}">{m.group(0)}</a>', token, count=1)
            inserted = True
        out.append(token)

    return "".join(out)


def _ensure_primary_keyword_internal_link(html: str, keyword: str, url: str) -> str:
    """Ensure the first keyword link points to the chosen evergreen URL."""
    if not html or not keyword or not url:
        return html

    found = False
    anchor_pattern = re.compile(
        r'<a\b([^>]*)href\s*=\s*(["\'])(https?://[^"\']+)\2([^>]*)>(.*?)</a>',
        flags=re.IGNORECASE | re.DOTALL,
    )
    keyword_pattern = re.compile(re.escape(keyword), flags=re.IGNORECASE)

    def _replace(match: re.Match[str]) -> str:
        nonlocal found
        attrs_text = f"{match.group(1) or ''} {match.group(4) or ''}".lower()
        if "switchboard_tracking" in attrs_text:
            return match.group(0)
        inner = match.group(5) or ""
        plain = re.sub(r"<[^>]+>", " ", inner)
        if found or not keyword_pattern.search(plain):
            return match.group(0)
        found = True
        return re.sub(
            r'href\s*=\s*(["\'])(https?://[^"\']+)\1',
            f'href="{url}"',
            match.group(0),
            count=1,
            flags=re.IGNORECASE,
        )

    updated = anchor_pattern.sub(_replace, html)
    if found:
        return updated
    return _link_first_keyword_internal(updated, keyword, url)


def _ensure_first_paragraph_keyword_internal_link(html: str, keyword: str, url: str) -> str:
    """Bias the primary evergreen link into the first paragraph when the keyword is present."""
    if not html or not keyword or not url:
        return html

    first_para_match = re.search(r"<p\b[^>]*>.*?</p>", html, flags=re.IGNORECASE | re.DOTALL)
    if not first_para_match:
        return _ensure_primary_keyword_internal_link(html, keyword, url)

    first_para = first_para_match.group(0)
    keyword_pattern = re.compile(re.escape(keyword), flags=re.IGNORECASE)
    plain_first = _html_to_plain_text(first_para)
    if not keyword_pattern.search(plain_first):
        return _ensure_primary_keyword_internal_link(html, keyword, url)

    # If the first paragraph already links the keyword, normalize that link to the selected URL.
    normalized_first = _ensure_primary_keyword_internal_link(first_para, keyword, url)
    if normalized_first != first_para:
        return html[: first_para_match.start()] + normalized_first + html[first_para_match.end():]

    linked_first = _link_first_keyword_internal(first_para, keyword, url)
    if linked_first != first_para:
        return html[: first_para_match.start()] + linked_first + html[first_para_match.end():]

    return _ensure_primary_keyword_internal_link(html, keyword, url)


def _dedupe_non_switchboard_links_by_url(html: str) -> str:
    """Unwrap duplicate non-switchboard links after the first occurrence."""
    if not html:
        return html

    seen_urls: set[str] = set()
    anchor_pattern = re.compile(
        r'<a\b([^>]*)href\s*=\s*(["\'])(https?://[^"\']+)\2([^>]*)>(.*?)</a>',
        flags=re.IGNORECASE | re.DOTALL,
    )

    def _replace(match: re.Match[str]) -> str:
        before_attrs = match.group(1) or ""
        url = match.group(3) or ""
        after_attrs = match.group(4) or ""
        inner = match.group(5) or ""
        attrs_text = f"{before_attrs} {after_attrs}".lower()
        if _is_switchboard_link(url, attrs_text):
            return match.group(0)
        url_key = url.strip().lower()
        if not url_key:
            return match.group(0)
        if url_key in seen_urls:
            return inner
        seen_urls.add(url_key)
        return match.group(0)

    return anchor_pattern.sub(_replace, html)


def _strip_invalid_non_switchboard_links(html: str) -> str:
    """Unwrap model-invented relative or non-http links while preserving switchboard CTAs."""
    if not html:
        return html

    anchor_pattern = re.compile(
        r'<a\b([^>]*)href\s*=\s*(["\'])([^"\']+)\2([^>]*)>(.*?)</a>',
        flags=re.IGNORECASE | re.DOTALL,
    )

    def _replace(match: re.Match[str]) -> str:
        before_attrs = match.group(1) or ""
        url = (match.group(3) or "").strip()
        after_attrs = match.group(4) or ""
        inner = match.group(5) or ""
        attrs_text = f"{before_attrs} {after_attrs}".lower()
        if _is_switchboard_link(url, attrs_text):
            return match.group(0)
        if re.match(r"^https?://", url, flags=re.IGNORECASE):
            return match.group(0)
        return inner

    return anchor_pattern.sub(_replace, html)


def _limit_non_switchboard_links(html: str, max_links: int = 1) -> str:
    """Keep only the first N non-switchboard links and unwrap the rest."""
    if not html or max_links < 0:
        return html

    kept = 0
    anchor_pattern = re.compile(
        r'<a\b([^>]*)href\s*=\s*(["\'])(https?://[^"\']+)\2([^>]*)>(.*?)</a>',
        flags=re.IGNORECASE | re.DOTALL,
    )

    def _replace(match: re.Match[str]) -> str:
        nonlocal kept
        before_attrs = match.group(1) or ""
        url = match.group(3) or ""
        after_attrs = match.group(4) or ""
        attrs_text = f"{before_attrs} {after_attrs}".lower()
        if _is_switchboard_link(url, attrs_text):
            return match.group(0)
        if kept >= max_links:
            return match.group(5) or ""
        kept += 1
        return match.group(0)

    return anchor_pattern.sub(_replace, html)


def _keep_only_primary_non_switchboard_link(html: str, primary_url: str) -> str:
    """Keep only the chosen evergreen/internal link and unwrap other non-switchboard links."""
    if not html or not primary_url:
        return html

    kept_primary = False
    primary_key = primary_url.strip().lower()
    anchor_pattern = re.compile(
        r'<a\b([^>]*)href\s*=\s*(["\'])(https?://[^"\']+)\2([^>]*)>(.*?)</a>',
        flags=re.IGNORECASE | re.DOTALL,
    )

    def _replace(match: re.Match[str]) -> str:
        nonlocal kept_primary
        before_attrs = match.group(1) or ""
        url_raw = match.group(3) or ""
        url = url_raw.strip().lower()
        after_attrs = match.group(4) or ""
        attrs_text = f"{before_attrs} {after_attrs}".lower()
        if _is_switchboard_link(url_raw, attrs_text):
            return match.group(0)
        if url == primary_key and not kept_primary:
            kept_primary = True
            return match.group(0)
        return match.group(5) or ""

    return anchor_pattern.sub(_replace, html)


def _keep_selected_non_switchboard_links(
    html: str,
    allowed_urls: list[str] | None,
    *,
    fallback_primary_url: str = "",
) -> str:
    """Keep only writer-selected non-switchboard links, preserving order and de-duping by URL."""
    if not html:
        return html

    normalized_allowed = []
    seen_allowed: set[str] = set()
    for url in allowed_urls or []:
        key = str(url or "").strip().lower()
        if not key or key in seen_allowed:
            continue
        seen_allowed.add(key)
        normalized_allowed.append(key)

    if not normalized_allowed:
        if fallback_primary_url:
            return _keep_only_primary_non_switchboard_link(html, fallback_primary_url)
        return _limit_non_switchboard_links(_dedupe_non_switchboard_links_by_url(html), max_links=1)

    kept: set[str] = set()
    anchor_pattern = re.compile(
        r'<a\b([^>]*)href\s*=\s*(["\'])(https?://[^"\']+)\2([^>]*)>(.*?)</a>',
        flags=re.IGNORECASE | re.DOTALL,
    )

    def _replace(match: re.Match[str]) -> str:
        before_attrs = match.group(1) or ""
        url_raw = match.group(3) or ""
        url_key = url_raw.strip().lower()
        after_attrs = match.group(4) or ""
        attrs_text = f"{before_attrs} {after_attrs}".lower()
        if _is_switchboard_link(url_raw, attrs_text):
            return match.group(0)
        if url_key in normalized_allowed and url_key not in kept:
            kept.add(url_key)
            return match.group(0)
        return match.group(5) or ""

    return anchor_pattern.sub(_replace, html)


def _align_selected_link_anchors(
    html: str,
    selected_links: list[Any] | None,
    preferred_phrases: list[str] | None = None,
) -> str:
    """Force selected internal links to use their preferred anchor text."""
    if not html or not selected_links:
        return html

    phrases = [str(x).strip().lower() for x in (preferred_phrases or []) if str(x).strip()]
    anchor_map: dict[str, str] = {}
    for link in selected_links:
        url = str(getattr(link, "url", "") or "").strip().lower()
        anchors = [str(a).strip() for a in (getattr(link, "recommended_anchors", []) or []) if str(a).strip()]
        if not url or not anchors:
            continue
        matched_anchor = next((anchor for anchor in anchors if anchor.lower() in phrases), "")
        anchor_map[url] = matched_anchor or anchors[0]

    if not anchor_map:
        return html

    anchor_pattern = re.compile(
        r'<a\b([^>]*)href\s*=\s*(["\'])(https?://[^"\']+)\2([^>]*)>(.*?)</a>',
        flags=re.IGNORECASE | re.DOTALL,
    )

    def _replace(match: re.Match[str]) -> str:
        before_attrs = match.group(1) or ""
        quote = match.group(2) or '"'
        url_raw = match.group(3) or ""
        after_attrs = match.group(4) or ""
        inner = match.group(5) or ""
        attrs_text = f"{before_attrs} {after_attrs}".lower()
        if _is_switchboard_link(url_raw, attrs_text):
            return match.group(0)
        preferred_anchor = anchor_map.get(url_raw.strip().lower())
        if not preferred_anchor:
            return match.group(0)
        plain_inner = re.sub(r"<[^>]+>", "", inner).strip().lower()
        accepted = {a.strip().lower() for a in [preferred_anchor] if a.strip()}
        if plain_inner in accepted:
            return match.group(0)
        before = before_attrs
        if before and not before.endswith(" "):
            before = f"{before} "
        elif not before:
            before = " "
        return f'<a{before}href={quote}{url_raw}{quote}{after_attrs}>{escape(preferred_anchor)}</a>'

    return anchor_pattern.sub(_replace, html)


def _offer_expiration_prompt_line(expiration_days: int | None) -> str:
    """Build a safe reward-expiration prompt line for source-of-truth sections."""
    if expiration_days is None:
        return "- Reward Expiration: Not provided (do not mention expiration unless it is explicit in source terms)"
    return f"- Reward Expiration: {expiration_days} days (this refers to the bonus/credit, not the main offer)"


def _format_offer_for_prompt(
    offer: dict[str, Any],
    state: str,
    prediction_market: bool = False,
    dfs_mode: bool = False,
) -> str:
    """Format one offer as a compact source-of-truth row for prompts."""
    brand = str(offer.get("brand") or "[not provided]")
    offer_text = str(offer.get("offer_text") or offer.get("affiliate_offer") or "[not provided]")
    code = str(offer.get("bonus_code") or "No code required")
    terms = str(offer.get("terms") or "")
    expiration_days = offer.get("bonus_expiration_days")
    if expiration_days is None:
        expiration_days = extract_bonus_expiration_days(terms)
    amount_details = extract_offer_amount_details(offer_text)
    bonus_amount = (
        offer.get("bonus_amount")
        or offer.get("reward_amount")
        or amount_details.get("reward_amount")
        or extract_bonus_amount(offer_text)
    )
    reward_label = str(offer.get("reward_label") or amount_details.get("reward_label") or "").strip()
    qualifying_action = str(offer.get("qualifying_action") or amount_details.get("qualifying_action") or "").strip()
    qualifying_amount = str(offer.get("qualifying_amount") or amount_details.get("qualifying_amount") or "").strip()
    states_text = _offer_states_text(
        offer,
        state,
        prediction_market=prediction_market,
        dfs_mode=dfs_mode,
    )
    excluded_states_text = _offer_excluded_states_text(
        offer,
        current_state=state,
        prediction_market=prediction_market,
        dfs_mode=dfs_mode,
    )
    age_summary = _operator_age_summary(
        offer,
        prediction_market=prediction_market,
        dfs_mode=dfs_mode,
    )
    excluded_states_text = _offer_excluded_states_text(
        offer,
        current_state=state,
        prediction_market=prediction_market,
        dfs_mode=dfs_mode,
    )
    age_summary = _operator_age_summary(
        offer,
        prediction_market=prediction_market,
        dfs_mode=dfs_mode,
    )
    expiration_text = (
        f"{expiration_days} days"
        if expiration_days is not None
        else "Not provided (omit unless explicit)"
    )
    bonus_amount_display = str(bonus_amount or "[not provided]")
    if reward_label and bonus_amount and reward_label.lower() not in bonus_amount_display.lower():
        bonus_amount_display = f"{bonus_amount_display} ({reward_label})"
    qualifying_action_line = (
        f"{qualifying_action.title()} {qualifying_amount} to unlock {bonus_amount or '[listed reward]'}"
        + (f" in {reward_label}" if reward_label else "")
        if qualifying_action and qualifying_amount
        else "[see terms - do not guess]"
    )

    if prediction_market:
        return (
            f"- Brand: {brand}\n"
            f"  Offer: {offer_text}\n"
            f"  Bonus Amount: {bonus_amount_display}\n"
            f"  Bonus Code: {code}\n"
            f"  Available in: {states_text}\n"
            f"  Credit Expiration: {expiration_text}\n"
            f"  Qualifying Action: {qualifying_action_line}"
        )
    if dfs_mode:
        return (
            f"- Brand: {brand}\n"
            f"  Offer: {offer_text}\n"
            f"  Bonus Amount: {bonus_amount_display}\n"
            f"  Bonus Code: {code}\n"
            f"  Available in: {states_text}\n"
            f"  Bonus Entry Expiration: {expiration_text}\n"
            f"  Contest/Entry Requirements: {qualifying_action_line if qualifying_action_line != '[see terms - do not guess]' else '[see terms - do not guess]'}"
        )

    min_odds = offer.get("minimum_odds") or extract_minimum_odds(terms)
    wagering = offer.get("wagering_requirement") or extract_wagering_requirement(terms)
    return (
        f"- Brand: {brand}\n"
        f"  Offer: {offer_text}\n"
        f"  Bonus Amount: {bonus_amount_display}\n"
        f"  Bonus Code: {code}\n"
        f"  Available in: {states_text}\n"
        f"  Bonus Bet Expiration: {expiration_text}\n"
        f"  Qualifying Action: {qualifying_action_line}\n"
        f"  Minimum Odds: {min_odds if min_odds else '[see terms - do not guess]'}\n"
        f"  Wagering: {wagering if wagering else '[see terms - do not guess]'}"
    )

def _build_multi_offer_prompt_context(
    offers: list[dict[str, Any]],
    state: str,
    prediction_market: bool = False,
    dfs_mode: bool = False,
) -> str:
    """Build source-of-truth prompt context for one or more offers."""
    normalized = [o for o in offers if o]
    if not normalized:
        return ""
    rows = [
        _format_offer_for_prompt(
            offer,
            state,
            prediction_market=prediction_market,
            dfs_mode=dfs_mode,
        )
        for offer in normalized[:3]
    ]
    return "\n".join(rows)

def _normalize_states(raw_states: Any) -> list[str]:
    """Normalize states from offer payload into canonical codes."""
    return parse_states(raw_states)


def _offer_states_text(
    offer: dict[str, Any],
    fallback_state: str = "ALL",
    *,
    prediction_market: bool = False,
    dfs_mode: bool = False,
) -> str:
    """Build a human-readable state list for prompts."""
    content_mode = (
        CONTENT_MODE_PREDICTION_MARKET
        if prediction_market
        else CONTENT_MODE_DFS
        if dfs_mode
        else CONTENT_MODE_SPORTSBOOK
    )
    operator_facts = get_operator_facts(offer.get("brand"), content_mode=content_mode)
    if content_mode in {CONTENT_MODE_DFS, CONTENT_MODE_PREDICTION_MARKET}:
        states = _normalize_states(operator_facts.get("allowed_states"))
        if not states:
            states = _normalize_states(offer.get("states_list") or offer.get("states"))
        if not states:
            states = extract_states_from_terms(str(offer.get("terms") or ""))
    else:
        states = _normalize_states(offer.get("states_list") or offer.get("states"))
        if not states:
            states = extract_states_from_terms(str(offer.get("terms") or ""))
        if not states:
            states = _normalize_states(operator_facts.get("allowed_states"))
    if not states:
        if fallback_state and fallback_state != "ALL":
            return fallback_state
        return "eligible states listed by the operator"
    if "ALL" in states and len(states) > 1:
        states = [s for s in states if s != "ALL"]
    if "ALL" in states:
        if fallback_state and fallback_state != "ALL":
            return fallback_state
        return "eligible states listed by the operator"
    return ", ".join(states)


def _offer_excluded_states_text(
    offer: dict[str, Any],
    *,
    current_state: str = "",
    prediction_market: bool = False,
    dfs_mode: bool = False,
) -> str:
    """Build a human-readable excluded-state list when source data provides one."""
    content_mode = (
        CONTENT_MODE_PREDICTION_MARKET
        if prediction_market
        else CONTENT_MODE_DFS
        if dfs_mode
        else CONTENT_MODE_SPORTSBOOK
    )
    operator_facts = get_operator_facts(offer.get("brand"), content_mode=content_mode)
    states = _normalize_states(operator_facts.get("excluded_states"))
    if not states:
        states = extract_excluded_states_from_terms(str(offer.get("terms") or ""))
    normalized_state = str(current_state or "").strip().upper()
    if normalized_state and normalized_state != "ALL":
        return normalized_state if normalized_state in states else ""
    return ", ".join(states)


def _operator_age_summary(
    offer: dict[str, Any],
    *,
    prediction_market: bool = False,
    dfs_mode: bool = False,
) -> str:
    """Return curated age summary text when available for the operator."""
    content_mode = (
        CONTENT_MODE_PREDICTION_MARKET
        if prediction_market
        else CONTENT_MODE_DFS
        if dfs_mode
        else CONTENT_MODE_SPORTSBOOK
    )
    operator_facts = get_operator_facts(offer.get("brand"), content_mode=content_mode)
    return str(operator_facts.get("age_summary_short") or "").strip()


def _render_daily_promos_placeholder(
    offers: list[dict[str, Any]],
    state: str,
    prediction_market: bool = False,
    dfs_mode: bool = False,
) -> str:
    """Render a deterministic daily-promos section for manual daily updates."""
    lines = [
        "<p><strong>Daily Promos Update:</strong> Refresh this section before publishing with today's rotating promos, limits, and expiration windows.</p>"
    ]

    if prediction_market:
        items = [
            "<li><strong>[Operator 1]:</strong> [Promo details]. Code: [CODE]. Available in [state list].</li>",
            "<li><strong>[Operator 2]:</strong> [Promo details]. Code: [CODE]. Available in [state list].</li>",
            "<li><strong>[Operator 3]:</strong> [Promo details]. Code: [CODE]. Available in [state list].</li>",
        ]
    elif dfs_mode:
        items = [
            "<li><strong>[DFS App 1]:</strong> [Promo details]. Code: [CODE]. Available in [state list].</li>",
            "<li><strong>[DFS App 2]:</strong> [Promo details]. Code: [CODE]. Available in [state list].</li>",
            "<li><strong>[DFS App 3]:</strong> [Promo details]. Code: [CODE]. Available in [state list].</li>",
        ]
    else:
        items = [
            "<li><strong>[Sportsbook 1]:</strong> [Promo details]. Code: [CODE]. Available in [state list].</li>",
            "<li><strong>[Sportsbook 2]:</strong> [Promo details]. Code: [CODE]. Available in [state list].</li>",
            "<li><strong>[Sportsbook 3]:</strong> [Promo details]. Code: [CODE]. Available in [state list].</li>",
        ]

    lines.append("<ul>")
    lines.extend(items)
    lines.append("</ul>")
    return "\n".join(lines)

def _render_terms_section_html(
    *,
    offers: list[dict[str, Any]] | None = None,
    terms: str,
    expiration_days: int | None,
    min_odds: str,
    wagering: str,
    state: str = "ALL",
    prediction_market: bool = False,
    dfs_mode: bool = False,
) -> str:
    """Render a deterministic terms section to avoid legal hallucinations."""
    normalized_offers = [offer for offer in (offers or []) if offer]
    if len(normalized_offers) > 1:
        blocks: list[str] = []
        for offer in normalized_offers[:3]:
            offer_terms = str(offer.get("terms") or "")
            offer_expiration = offer.get("bonus_expiration_days") or extract_bonus_expiration_days(offer_terms)
            offer_min_odds = str(offer.get("minimum_odds") or extract_minimum_odds(offer_terms) or "").strip()
            offer_wagering = str(offer.get("wagering_requirement") or extract_wagering_requirement(offer_terms) or "").strip()
            brand = str(offer.get("brand") or "Offer").strip()
            code = str(offer.get("bonus_code") or "").strip()
            states_text = _offer_states_text(
                offer,
                state,
                prediction_market=prediction_market,
                dfs_mode=dfs_mode,
            )
            header_parts = [f"<strong>{brand}</strong>"]
            if code:
                header_parts.append(f"Code: {code}")
            elif not prediction_market and not dfs_mode:
                header_parts.append("No promo code required")
            if states_text:
                header_parts.append(f"Available in {states_text}")
            blocks.append(f"<p>{'. '.join(header_parts)}.</p>")

            if offer_terms:
                cleaned_terms = offer_terms.replace("\\n", "\n")
                paras = [p.strip() for p in cleaned_terms.splitlines() if p.strip()]
                if paras:
                    blocks.extend(f"<p>{p}</p>" for p in paras)
                    continue

            fallback_parts: list[str] = []
            if offer_expiration is not None:
                fallback_parts.append(
                    f"Promotional credits expire in {offer_expiration} days."
                    if prediction_market
                    else f"Bonus entries expire in {offer_expiration} days."
                    if dfs_mode
                    else f"Bonus bets expire in {offer_expiration} days."
                )
            if not prediction_market and not dfs_mode:
                if offer_min_odds:
                    fallback_parts.append(f"Minimum odds requirement: {offer_min_odds}.")
                if offer_wagering:
                    fallback_parts.append(f"Wagering requirement: {offer_wagering}.")
            if fallback_parts:
                blocks.append(f"<p>{' '.join(fallback_parts)}</p>")
        return "\n".join(blocks)

    if terms:
        cleaned = terms.replace("\\n", "\n")
        paras = [p.strip() for p in cleaned.splitlines() if p.strip()]
        if paras:
            states_text = _offer_states_text(
                normalized_offers[0] if normalized_offers else {},
                state,
                prediction_market=prediction_market,
                dfs_mode=dfs_mode,
            )
            if states_text and not any(re.search(r"states available|available in", p, flags=re.IGNORECASE) for p in paras):
                paras.insert(0, _availability_prose(states_text))
            return "\n".join(f"<p>{p}</p>" for p in paras)

    points: list[str] = []
    if expiration_days is not None:
        points.append(
            f"Promotional credits expire in {expiration_days} days."
            if prediction_market
            else f"Bonus entries expire in {expiration_days} days."
            if dfs_mode
            else f"Bonus bets expire in {expiration_days} days."
        )
    if not prediction_market and not dfs_mode:
        if min_odds:
            points.append(f"Minimum odds requirement: {min_odds}.")
        if wagering:
            points.append(f"Wagering requirement: {wagering}.")
    points.append(
        "See the operator's app or site for full offer terms, eligibility rules, and restrictions before betting."
        if not prediction_market and not dfs_mode
        else "See the app's official contest rules for full terms and eligibility requirements before entering."
        if dfs_mode
        else "See the platform's official rules for full market terms and eligibility requirements before trading."
    )
    return f"<p>{' '.join(points)}</p>"


def _naturalize_event_context(event_context: str) -> str:
    """Convert label-heavy event context into a natural prompt snippet."""
    if not event_context:
        return ""

    featured = _extract_featured_label_from_event_context(event_context)
    game_time_match = re.search(r"Game time:\s*([^\.]+)", event_context, flags=re.IGNORECASE)
    network_match = re.search(r"Network:\s*([^\.]+)", event_context, flags=re.IGNORECASE)

    parts: list[str] = []
    if featured:
        parts.append(f"{featured} is the featured event.")
    if game_time_match:
        parts.append(f"It starts {game_time_match.group(1).strip()}.")
    if network_match:
        parts.append(f"It airs on {network_match.group(1).strip()}.")
    return " ".join(parts).strip() or event_context


async def _generate_signup_steps_structured(
    *,
    brand: str,
    keyword: str,
    state: str,
    has_code: bool,
    code_strong: str,
    style_guide: str,
    links_md: str,
    prediction_market: bool = False,
    dfs_mode: bool = False,
) -> list[str] | None:
    """Generate a structured 5-step sign-up list."""
    schema = {
        "type": "object",
        "properties": {
            "steps": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 5,
                "maxItems": 5,
            }
        },
        "required": ["steps"],
        "additionalProperties": False,
    }

    code_line = (
        f"Step 2 must include {code_strong}."
        if has_code
        else "Step 2 must say no promo code is required."
    )

    mechanics_line = (
        "Step 5 must describe the first qualifying market position and settlement mechanics."
        if prediction_market
        else "Step 5 must describe the first qualifying fantasy entry and contest mechanics."
        if dfs_mode
        else "Step 5 must describe the first qualifying bet and bonus payout mechanics."
    )

    user_prompt = f"""Write a 5-step sign-up list for this promo.
Output JSON with a single key: "steps" (array of 5 strings).
Each step should be 1-2 sentences and plain language.

Brand: {brand}
Keyword: {keyword}
State: {state}
{code_line}
{mechanics_line}

Use a REAL URL only if it directly helps the step.
At most one step may include an internal link.
Never use placeholder links such as href="#".
Available internal links (optional):
{links_md}

Do NOT include responsible gaming disclaimers here.
{'Use prediction-market language (trade, market, position, contract). Avoid sportsbook/betting/wager terms.' if prediction_market else 'Use DFS language (entry, contest, picks, lineup) and avoid sportsbook/betting/wager terms.' if dfs_mode else ''}

STYLE GUIDE:
{style_guide}
"""

    try:
        data = await generate_completion_structured(
            prompt=user_prompt,
            system_prompt=(
                "You are a concise prediction-market editor. Output only valid JSON."
                if prediction_market
                else "You are a concise DFS editor. Output only valid JSON."
                if dfs_mode
                else "You are a concise sports betting editor. Output only valid JSON."
            ),
            schema=schema,
            name="signup_steps",
            description="Five-step signup list for a promo article",
            temperature=0.2,
            max_tokens=400,
        )
        steps = data.get("steps", []) if isinstance(data, dict) else []
        steps = [s.strip() for s in steps if isinstance(s, str) and s.strip()]
        if len(steps) == 5 and not any(re.search(r"https?://", step, flags=re.IGNORECASE) for step in steps):
            return steps
    except Exception:
        pass
    return None


def _remove_inline_compliance_fragments(html: str) -> str:
    """Remove standalone compliance fragments from body copy before final disclaimer append."""
    if not html:
        return html

    cleaned = html
    patterns = [
        r"\s*21\+\s+only\.?",
        r"\s*please\s+bet\s+responsibly\.?",
        r"\s*gambling\s+problem\?\s+call\s+1-800-gambler\.?",
        r"\s*full\s+operator\s+terms\s+apply\.?",
        r"\s*see\s+(?:full\s+)?terms(?:\s+for\s+full\s+details)?\.?",
        r"\s*see\s+caesars\.com/promos\s+for\s+full\s+terms\.?",
        r"\s*see\s+terms\s+for\s+full\s+details\.?",
    ]
    for pattern in patterns:
        cleaned = _rewrite_html_text_nodes(
            cleaned,
            lambda text, pattern=pattern: re.sub(pattern, "", text, flags=re.IGNORECASE),
        )
    return cleaned

def _ensure_two_paragraphs(
    html: str,
    brand: str,
    offer_text: str,
    has_code: bool,
    code_strong: str,
    states_text: str,
) -> str:
    """Ensure intro has at least two paragraphs."""
    if not html:
        return html

    paragraphs = re.findall(r"<p>.*?</p>", html, flags=re.DOTALL)
    if len(paragraphs) >= 2:
        return html

    # Normalize to a single paragraph body
    if paragraphs:
        body = re.sub(r"^<p>|</p>$", "", paragraphs[0].strip(), flags=re.DOTALL)
    else:
        body = html.strip()

    sentences = re.split(r"(?<=[.!?])\s+", body)
    sentences = [s.strip() for s in sentences if s.strip()]

    if len(sentences) >= 3:
        first = " ".join(sentences[:2]).strip()
        second = " ".join(sentences[2:]).strip()
    elif len(sentences) == 2:
        first, second = sentences
    else:
        first = body
        offer_summary = _offer_value_summary({"brand": brand, "offer_text": offer_text})
        details = []
        if brand and offer_summary:
            details.append(f"{brand} is highlighting {offer_summary}.")
        if has_code:
            details.append(f"Enter {code_strong} when you register.")
        else:
            details.append("No promo code is required.")
        if states_text:
            details.append(_availability_prose(states_text))
        second = " ".join(details) or "Full operator terms apply."

    return f"<p>{first}</p>\n<p>{second}</p>"


def _ensure_intro_state_specificity(html: str, states_text: str) -> str:
    """Ensure intro copy names explicit states, phrased as prose rather than a label."""
    if not html or not states_text:
        return html

    normalized_states = states_text.strip()
    if not normalized_states or normalized_states.lower().startswith("all eligible states"):
        return html

    html = _convert_availability_labels_to_prose(html)

    state_code_list = r"(?:[A-Z]{2}|District of Columbia)(?:,\s*(?:[A-Z]{2}|District of Columbia))*"
    if re.fullmatch(r"[A-Z]{2}|District of Columbia", normalized_states):
        html = _rewrite_html_text_nodes(
            html,
            lambda text: re.sub(
                rf"\bavailable in {state_code_list}",
                f"available in {normalized_states}",
                text,
            ),
        )

    html = re.sub(r"\bavailable nationwide\b", f"available in {normalized_states}", html, flags=re.IGNORECASE)
    html = re.sub(r"\bnationwide states\b", normalized_states, html, flags=re.IGNORECASE)

    state_tokens = [s.strip().upper() for s in normalized_states.split(",") if s.strip()]
    plain = re.sub(r"<[^>]+>", " ", html)
    has_availability_phrase = bool(re.search(r"\bavailable in\b", plain, flags=re.IGNORECASE))
    plain_upper = plain.upper()
    has_explicit_state = any(re.search(rf"\b{re.escape(token)}\b", plain_upper) for token in state_tokens)
    if has_explicit_state and has_availability_phrase:
        return html

    addition = f" {_availability_prose(normalized_states)}"
    paragraphs = re.findall(r"<p>.*?</p>", html, flags=re.DOTALL)
    if paragraphs:
        last_para = paragraphs[-1]
        updated_last = re.sub(r"</p>\s*$", f"{addition}</p>", last_para, flags=re.DOTALL)
        return html.replace(last_para, updated_last, 1)
    return f"<p>{html.strip()}{addition}</p>"


def _rewrite_html_text_nodes(html: str, transform: callable) -> str:
    """Apply a text transform to visible text nodes only, preserving tags/attributes."""
    if not html:
        return html
    tokens = re.findall(r"<[^>]+>|[^<]+", html, flags=re.DOTALL)
    out: list[str] = []
    for token in tokens:
        out.append(token if token.startswith("<") else transform(token))
    return "".join(out)


def _soften_repetitive_intro_opener(html: str) -> str:
    """Reduce repeated 'Put the X to work' intros with a neutral rewrite."""
    if not html:
        return html
    paragraphs = re.findall(r"<p>.*?</p>", html, flags=re.IGNORECASE | re.DOTALL)
    if not paragraphs:
        return html
    first = paragraphs[0]
    inner = re.sub(r"^<p>|</p>$", "", first.strip(), flags=re.IGNORECASE | re.DOTALL)
    if not re.match(r"\s*put the\b", inner, flags=re.IGNORECASE):
        return html
    updated = re.sub(r"^\s*put the\s+", "The ", inner, count=1, flags=re.IGNORECASE)
    updated = re.sub(r"\s+to work\b", " is live", updated, count=1, flags=re.IGNORECASE)
    updated_para = f"<p>{updated}</p>"
    return html.replace(first, updated_para, 1)


def _ensure_keyword_in_first_paragraph(html: str, keyword: str) -> str:
    """Ensure the exact keyword appears in the first paragraph (sentence 1 or 2 coverage)."""
    if not html or not keyword:
        return html
    match = re.search(r"<p>(.*?)</p>", html, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return html
    first_para_start = match.start()
    first_h2 = re.search(r"<h2\b", html, flags=re.IGNORECASE)
    if first_h2 and first_h2.start() < first_para_start:
        return html
    first_para = match.group(0)
    first_inner = match.group(1)
    plain = re.sub(r"<[^>]+>", " ", first_inner)
    if re.search(re.escape(keyword), plain, flags=re.IGNORECASE):
        return html
    variants = [
        f"For readers tracking {keyword}, ",
        f"For this event, the {keyword} angle is straightforward: ",
        f"The latest {keyword} angle ties into this event: ",
        f"Readers checking {keyword} get the main offer details up front: ",
    ]
    prefix = variants[sum(ord(ch) for ch in f"{keyword}|{plain}") % len(variants)]
    updated_para = f"<p>{prefix}{first_inner.strip()}</p>"
    return html.replace(first_para, updated_para, 1)


def _polish_intro_fallback_phrases(html: str) -> str:
    """Rewrite awkward deterministic intro prefixes into cleaner editorial phrasing."""
    if not html:
        return html
    replacements = [
        (r"That puts\s+(.+?)\s+front and center\.\s*", r"For readers tracking \1, "),
        (r"With\s+(.+?)\s+in focus,\s*", r"For readers tracking \1, "),
        (r"(.+?)\s+is part of the story here\.\s*", r"For readers tracking \1, "),
    ]
    cleaned = html
    cleaned = re.sub(
        r"(<p\b[^>]*>)\s*If you(?:'|’|&rsquo;|&#8217;)re following\s+((?:<a\b[^>]*>.*?</a>|[^,]+)),\s*",
        r"\1For readers tracking \2, ",
        cleaned,
        count=1,
        flags=re.IGNORECASE | re.DOTALL,
    )
    cleaned = re.sub(
        r"(<p\b[^>]*>.*?)\bIf you(?:'|â€™|&rsquo;|&#8217;)re looking for\s+((?:<a\b[^>]*>.*?</a>|[^,]+)),\s*",
        r"\1For readers looking for \2, ",
        cleaned,
        count=1,
        flags=re.IGNORECASE | re.DOTALL,
    )
    for pattern, replacement in replacements:
        cleaned = re.sub(pattern, replacement, cleaned, count=1, flags=re.IGNORECASE)
    return cleaned


def _polish_worked_example_conditionals(html: str) -> str:
    """Rewrite common worked-example 'If...' constructions into direct active phrasing."""
    if not html:
        return html

    replacements = [
        (
            r"\bIf my bet wins at ([^,\.]+),\s+I profit\b",
            r"A win at \1 profits",
        ),
        (
            r"\bIf it wins,\s+my profit is\b",
            "A win puts the profit at",
        ),
        (
            r"\bIf it wins,\s+I profit\b",
            "A win profits",
        ),
        (
            r"\bIf (?:it|the bet) loses,\s+I(?:'|’|&rsquo;|&#8217;)?m\s+down\b",
            "A loss leaves me down",
        ),
        (
            r"\bIf (?:it|the bet) loses,\s+I am\s+down\b",
            "A loss leaves me down",
        ),
        (
            r"\bIf it does not cash,\s+I lose\b",
            "A losing entry costs",
        ),
        (
            r"\bIf it settles at (\$[\d.]+),\s+the payout is\b",
            r"A \1 settlement pays",
        ),
        (
            r"\bIf it settles the other way,\s+I can lose\b",
            "The opposite settlement risks",
        ),
        (
            r"\bIf I put (\$[\d,]+) in bonus bets on ([^\.]+?) and it wins,\s+the payout is profit-only:\s*[^\.]*(?:\.|$)",
            r"A later \1 bonus bet on \2 pays profit-only; the bonus-bet stake itself does not return.",
        ),
        (
            r"\bI put (\$[\d,]+) in bonus bets on ([^\.]+?) and it wins,\s+the payout is profit-only:\s*[^\.]*(?:\.|$)",
            r"A later \1 bonus bet on \2 pays profit-only; the bonus-bet stake itself does not return.",
        ),
    ]
    cleaned = html
    for pattern, replacement in replacements:
        cleaned = _rewrite_html_text_nodes(
            cleaned,
            lambda text, pattern=pattern, replacement=replacement: re.sub(
                pattern,
                replacement,
                text,
                flags=re.IGNORECASE,
            ),
        )
    return _normalize_visible_punctuation(cleaned)


def _polish_conditional_user_openers(html: str) -> str:
    """Reduce repetitive 'If you're...' user-facing openers."""
    if not html:
        return html
    apostrophe = r"(?:'|’|�|&rsquo;|&#8217;|â€™)"
    replacements = [
        (
            rf"\bIf you{apostrophe}re already targeting ([^,]+),\s+this is\b",
            r"For \1, this is",
        ),
        (
            rf"\bIf you{apostrophe}re signing up for ([^,]+),\s+",
            r"When signing up for \1, ",
        ),
        (
            rf"\bIf you{apostrophe}re using\b",
            "When using",
        ),
        (
            rf"\bIf you{apostrophe}re eligible and want\b",
            "For eligible readers who want",
        ),
    ]
    cleaned = html
    for pattern, replacement in replacements:
        cleaned = _rewrite_html_text_nodes(
            cleaned,
            lambda text, pattern=pattern, replacement=replacement: re.sub(
                pattern,
                replacement,
                text,
                flags=re.IGNORECASE,
            ),
        )
    return _normalize_visible_punctuation(cleaned)


def _strip_unprovided_article_date(html: str, article_date: str = "") -> str:
    """Remove today's date when no article date was supplied by the request."""
    if not html or str(article_date or "").strip():
        return html
    today = today_long()
    if today not in _html_to_plain_text(html):
        return html
    today_escaped = re.escape(today)

    def _transform(text: str) -> str:
        original = text
        text = re.sub(rf"\bAs of\s+{today_escaped},\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(rf"\b{today_escaped}:\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(rf"\b{today_escaped}\s+pairs cleanly with\s+", "", text, flags=re.IGNORECASE)
        text = re.sub(rf"\b{today_escaped}\s+pairs(?:\s+\w+)?\s+with\s+", "", text, flags=re.IGNORECASE)
        text = re.sub(rf"\b{today_escaped}\s+turns attention to\s+", "", text, flags=re.IGNORECASE)
        text = re.sub(rf"\b{today_escaped}\s+sets up\s+", "", text, flags=re.IGNORECASE)
        text = re.sub(rf"\b{today_escaped}\s+lines up(?:\s+\w+)?\s+with\s+", "", text, flags=re.IGNORECASE)
        text = re.sub(rf"\s+ahead of\s+{today_escaped}\b", "", text, flags=re.IGNORECASE)
        if text != original and re.match(r"^\s*the\b", text):
            text = re.sub(r"^(\s*)the\b", r"\1The", text, count=1)
        return text

    return _normalize_visible_punctuation(_rewrite_html_text_nodes(html, _transform))


def _normalize_visible_punctuation(html: str) -> str:
    """Clean up obvious punctuation artifacts in visible text without touching URLs."""
    if not html:
        return html

    def _transform(text: str) -> str:
        text = re.sub(r",\s*,+", ", ", text)
        text = re.sub(r"\s{2,}", " ", text)
        text = re.sub(r"\.\s+\.", ".", text)
        return text

    return _rewrite_html_text_nodes(html, _transform)


def _availability_prose(states_text: str, *, market: str = "US") -> str:
    """Reader-facing availability sentence instead of a 'States Available:' label."""
    states = str(states_text or "").strip().rstrip(".")
    if not states:
        return ""
    noun = "province" if str(market or "US").strip().upper() == "CA" else "state"
    if "listed by the operator" in states.lower() or states.lower().startswith("all eligible"):
        return f"Availability varies by {noun}, so confirm eligibility during signup."
    return f"The offer is available in {states}."


def _convert_availability_labels_to_prose(html: str) -> str:
    """Rewrite 'States Available: X, Y.' label output into reader-facing prose."""
    if not html:
        return html

    def _transform(text: str) -> str:
        def _repl(match: re.Match[str]) -> str:
            noun = "province" if match.group(1).lower().startswith("province") else "state"
            values = match.group(2).strip().rstrip(".")
            if not values:
                return ""
            if "listed by the operator" in values.lower():
                return f"Availability varies by {noun}, so confirm eligibility during signup."
            return f"The offer is available in {values}."

        text = re.sub(
            r"\b(States|Provinces)\s+Available:\s*([^.]*)\.?",
            _repl,
            text,
            flags=re.IGNORECASE,
        )
        return re.sub(
            r"\b(?:It(?:'|’)s|The offer is) available in eligible (states|provinces) listed by the operator\.?",
            lambda m: f"Availability varies by {m.group(1).lower().rstrip('s')}, so confirm eligibility during signup.",
            text,
            flags=re.IGNORECASE,
        )

    return _rewrite_html_text_nodes(html, _transform)


def _decapitalize_inline_reward_mentions(html: str) -> str:
    """Lowercase shouty offer-headline casing ('in Bonus Bets Instantly') in editorial copy."""
    if not html:
        return html
    # Operator terms are quoted verbatim - leave the terms block and below alone.
    split = re.search(
        r"<h[1-6]\b[^>]*>[^<]*(?:Terms|Conditions|Fine Print|Rules)[^<]*</h[1-6]>",
        html,
        flags=re.IGNORECASE,
    )
    head, tail = (html[: split.start()], html[split.start():]) if split else (html, "")

    tokens = re.findall(r"<[^>]+>|[^<]+", head, flags=re.DOTALL)
    inside_heading = 0
    out: list[str] = []
    for token in tokens:
        if token.startswith("<"):
            tag = token.strip().lower()
            if re.match(r"<h[1-6]\b", tag):
                inside_heading += 1
            elif re.match(r"</h[1-6]\b", tag):
                inside_heading = max(0, inside_heading - 1)
            out.append(token)
            continue
        if inside_heading:
            out.append(token)
            continue
        out.append(
            re.sub(
                r"(?<=[a-z0-9,$%] )Bonus (Bets?|Entries)(\s+Instantly)?\b",
                lambda m: m.group(0).lower(),
                token,
            )
        )
    return "".join(out) + tail


def _remove_generic_state_fallbacks(html: str) -> str:
    """Drop vague operator-state filler when exact state lists already appear in the same section."""
    if not html:
        return html
    plain = re.sub(r"<[^>]+>", " ", html)
    explicit_list = (
        r"(?:States Available:|Provinces Available:|available in)\s*"
        r"(?:[A-Z]{2}|District of Columbia)"
    )
    if not re.search(explicit_list, plain):
        return html

    cleaned = _rewrite_html_text_nodes(
        html,
        lambda text: re.sub(
            r"\bStates Available:\s*eligible states listed by the operator\.?",
            "",
            text,
            flags=re.IGNORECASE,
        ),
    )
    cleaned = _rewrite_html_text_nodes(
        cleaned,
        lambda text: re.sub(
            r"\bavailable in eligible states listed by the operator\.?",
            "",
            text,
            flags=re.IGNORECASE,
        ),
    )
    return _normalize_visible_punctuation(cleaned)


def _polish_intro_section_prose(html: str) -> str:
    """Lightly tighten intro copy so the lede stays promotional, not legalistic."""
    if not html:
        return html
    cleaned = html
    patterns = [
        (r"\s+as a new customer\b", ""),
        (r"\s*You(?:'|’|â€™|�)ll need to be 21\+[^.]*\.", ""),
        (r"\s*New customers only\.?", ""),
        (r"\b21\+\b(?:\s*(?:only|required))?", ""),
    ]
    for pattern, replacement in patterns:
        cleaned = _rewrite_html_text_nodes(
            cleaned,
            lambda text, pattern=pattern, replacement=replacement: re.sub(
                pattern,
                replacement,
                text,
                flags=re.IGNORECASE,
            ),
        )
    cleaned = _rewrite_html_text_nodes(
        cleaned,
        lambda text: re.sub(r"\bis a clean spot to use\b", "is a good spot to use", text, flags=re.IGNORECASE),
    )
    cleaned = _rewrite_html_text_nodes(
        cleaned,
        lambda text: re.sub(r"\bit's a clean spot to use\b", "it's a good spot to use", text, flags=re.IGNORECASE),
    )
    cleaned = _rewrite_html_text_nodes(
        cleaned,
        lambda text: re.sub(
            r"\bthis is 21\+\s+and\s+it(?:'|â€™|’)?s not valid in\b",
            "This offer is only available in supported states, and it isn't valid in",
            text,
            flags=re.IGNORECASE,
        ),
    )
    cleaned = _rewrite_html_text_nodes(
        cleaned,
        lambda text: re.sub(r"\bis the featured event\b", "is the focus", text, flags=re.IGNORECASE),
    )
    cleaned = _rewrite_html_text_nodes(
        cleaned,
        lambda text: re.sub(r"\bis for,\s+and\b", " requires a deposit, and", text, flags=re.IGNORECASE),
    )
    cleaned = _rewrite_html_text_nodes(
        cleaned,
        lambda text: re.sub(r"\bis for,\b", " requires a deposit,", text, flags=re.IGNORECASE),
    )
    cleaned = _remove_generic_state_fallbacks(cleaned)
    return _normalize_visible_punctuation(cleaned)


def _remove_irrelevant_excluded_state_mentions(html: str, current_state: str = "") -> str:
    """Strip excluded-state clauses that are irrelevant to a single-state article."""
    normalized_state = str(current_state or "").strip().upper()
    if not html or not normalized_state or normalized_state == "ALL":
        return html

    def _drop_irrelevant(text: str) -> str:
        patterns = [
            r"(?:,\s*|\s+and\s+)?it(?:'|â€™|Ã¢â‚¬â„¢|ï¿½)?s not available in [^.]+\.?",
            r"(?:,\s*|\s+and\s+)?it(?:'|â€™|Ã¢â‚¬â„¢|ï¿½)?s isn(?:'|â€™|Ã¢â‚¬â„¢|ï¿½)?t available in [^.]+\.?",
            r"(?:,\s*|\s+and\s+)?not available in [^.]+\.?",
            r"(?:,\s*|\s+and\s+)?isn(?:'|â€™|Ã¢â‚¬â„¢|ï¿½)?t available in [^.]+\.?",
            r"\s*Excluded:\s*[^.]+\.?",
        ]
        cleaned = text
        for pattern in patterns:
            def _replace(match: re.Match[str]) -> str:
                clause = match.group(0)
                normalized_clause = re.sub(
                    r"isn(?:'|â€™|Ã¢â‚¬â„¢|ï¿½)?t available in",
                    "not available in",
                    clause,
                    flags=re.IGNORECASE,
                )
                states = extract_excluded_states_from_terms(normalized_clause)
                if states and normalized_state not in states:
                    return ""
                return clause
            cleaned = re.sub(pattern, _replace, cleaned, flags=re.IGNORECASE)
        return cleaned

    cleaned = _rewrite_html_text_nodes(html, _drop_irrelevant)
    return _normalize_visible_punctuation(cleaned)


def _remove_irrelevant_single_state_exclusion_phrases(html: str, current_state: str = "") -> str:
    """Strip leftover 'isn't available in X' phrasing when the article is already scoped to another state."""
    normalized_state = str(current_state or "").strip().upper()
    if not html or not normalized_state or normalized_state == "ALL":
        return html

    def _drop_clause(text: str) -> str:
        def _replace(match: re.Match[str]) -> str:
            clause = match.group(0)
            normalized_clause = re.sub(r"isn\S{0,6}?t available in", "not available in", clause, flags=re.IGNORECASE)
            states = extract_excluded_states_from_terms(normalized_clause)
            if states and normalized_state not in states:
                return ""
            return clause

        cleaned = re.sub(r"[^.]*isn\S{0,6}?t available in [^.]+\.?", _replace, text, flags=re.IGNORECASE)
        cleaned = re.sub(r"[^.]*not available in [^.]+\.?", _replace, cleaned, flags=re.IGNORECASE)
        return cleaned

    cleaned = _rewrite_html_text_nodes(html, _drop_clause)
    return _normalize_visible_punctuation(cleaned)


def _resolve_intro_age_conflicts(html: str, age_summary: str = "") -> str:
    """Remove contradictory default 21+ phrasing when operator facts specify a lower minimum."""
    if not html or not age_summary:
        return html
    if "18+" not in age_summary:
        return html
    cleaned = _rewrite_html_text_nodes(
        html,
        lambda text: re.sub(
            r"\b21\+\s+required,\s+and\s+it(?:'|â€™|’)?s\b",
            "It's",
            text,
            flags=re.IGNORECASE,
        ),
    )
    return _normalize_visible_punctuation(cleaned)


def _polish_body_section_prose(html: str) -> str:
    """Strip legal/compliance fragments that make non-terms body copy read mechanically."""
    if not html:
        return html

    cleaned = html
    patterns = [
        r"\s*,?\s*as long as you(?:'|’)re 21\+\s+and\s+in\s+[A-Z]{2}\.?",
        r"\s*,?\s*as long as you(?:'|’)re 21\+\s+and\s+located in an eligible state\.?",
        r"\s*21\+\s+and\s+[A-Z]{2}\s+only\.?",
        r"\s*new customers only\.?",
        r"\s*deposit required\.?",
        r"\s*time limits and exclusions apply\.?",
        r"\s*make sure you(?:'|â€™|’)?re 21\+\s+and\s+physically located in an eligible state[^.]*\.?",
        r"\s*if you want a quick walkthrough,\s+the\s+.+?sign-up guide[^.]*\.?",
        r"\s*[^.]*sign-up guide[^.]*\.?",
    ]
    for pattern in patterns:
        cleaned = _rewrite_html_text_nodes(
            cleaned,
            lambda text, pattern=pattern: re.sub(pattern, "", text, flags=re.IGNORECASE),
        )
    cleaned = _rewrite_html_text_nodes(
        cleaned,
        lambda text: re.sub(r"\s*,?\s*and\s+21\+\.?", "", text, flags=re.IGNORECASE),
    )
    cleaned = _rewrite_html_text_nodes(
        cleaned,
        lambda text: re.sub(r"\bThe value is simple:\s*", "The offer adds ", text, flags=re.IGNORECASE),
    )
    cleaned = _rewrite_html_text_nodes(
        cleaned,
        lambda text: re.sub(r"\bis a clean spot\b", "is a good spot", text, flags=re.IGNORECASE),
    )
    cleaned = _rewrite_html_text_nodes(
        cleaned,
        lambda text: re.sub(r"\bclean window\b", "good window", text, flags=re.IGNORECASE),
    )
    cleaned = _remove_generic_state_fallbacks(cleaned)
    return _normalize_visible_punctuation(cleaned)


def _target_keyword_mentions(html: str, keyword: str) -> str:
    """Normalize exact keyword mentions to the requested case and bold body mentions."""
    if not html or not keyword:
        return html

    tokens = re.findall(r"<[^>]+>|[^<]+", html, flags=re.DOTALL)
    pattern = re.compile(re.escape(keyword), flags=re.IGNORECASE)
    inside_anchor = 0
    inside_strong = 0
    inside_heading = 0
    out: list[str] = []

    for token in tokens:
        if token.startswith("<"):
            tag = token.strip().lower()
            if re.match(r"<a\b", tag):
                inside_anchor += 1
            elif re.match(r"</a\b", tag):
                inside_anchor = max(0, inside_anchor - 1)
            elif re.match(r"<strong\b", tag):
                inside_strong += 1
            elif re.match(r"</strong\b", tag):
                inside_strong = max(0, inside_strong - 1)
            elif re.match(r"<h[1-6]\b", tag):
                inside_heading += 1
            elif re.match(r"</h[1-6]\b", tag):
                inside_heading = max(0, inside_heading - 1)
            out.append(token)
            continue

        def _repl(match: re.Match[str]) -> str:
            if inside_heading:
                return match.group(0)
            if inside_strong or inside_anchor:
                return keyword
            return f"<strong>{keyword}</strong>"

        out.append(pattern.sub(_repl, token))

    return "".join(out)


def _enforce_primary_keyword_density(html: str, keyword: str, min_count: int = 5, max_count: int = 9) -> str:
    """Add plain-text exact keyword mentions when generated copy falls below target."""
    if not html or not keyword:
        return html
    current = _count_keyword(html, keyword)
    if current >= min_count:
        return html

    additions_needed = min(max_count - current, min_count - current)
    if additions_needed <= 0:
        return html

    variants = [
        f" That is what the {keyword} unlocks right now.",
        f" The {keyword} applies to this exact offer, so match the first bet to its rules.",
        f" New users get the most from the {keyword} by starting here.",
        f" The {keyword} is tied to this offer, not older versions of the promo.",
    ]
    para_pattern = re.compile(r"<p\b([^>]*)>(.*?)</p>", flags=re.IGNORECASE | re.DOTALL)
    pieces: list[str] = []
    last = 0
    added = 0

    for match in para_pattern.finditer(html):
        pieces.append(html[last:match.start()])
        full = match.group(0)
        attrs = match.group(1) or ""
        inner = match.group(2) or ""
        plain = _html_to_plain_text(inner).lower()
        skip = (
            added >= additions_needed
            or "[bam-inline-promotion" in inner.lower()
            or "switchboard_tracking" in inner.lower()
            or "gambling problem" in plain
            or "terms apply" in plain
            or "minimum odds" in plain
            or ("states available" in plain and len(plain.split()) < 25)
        )
        if skip:
            pieces.append(full)
        else:
            pieces.append(f"<p{attrs}>{inner.rstrip()}{variants[added % len(variants)]}</p>")
            added += 1
        last = match.end()

    pieces.append(html[last:])
    return "".join(pieces)


def _cap_primary_keyword_density(html: str, keyword: str, max_count: int = 9) -> str:
    """Reduce exact keyword overage by converting later plain mentions to brand-only text."""
    if not html or not keyword or _count_keyword(html, keyword) <= max_count:
        return html
    brand = keyword.split()[0] if keyword.split() else keyword
    pattern = re.compile(re.escape(keyword), flags=re.IGNORECASE)
    count = 0
    inside_anchor = 0
    inside_strong = 0
    out: list[str] = []
    for token in re.findall(r"<[^>]+>|[^<]+", html, flags=re.DOTALL):
        if token.startswith("<"):
            tag = token.lower()
            if re.match(r"<a\b", tag):
                inside_anchor += 1
            elif re.match(r"</a\b", tag):
                inside_anchor = max(0, inside_anchor - 1)
            elif re.match(r"<strong\b", tag):
                inside_strong += 1
            elif re.match(r"</strong\b", tag):
                inside_strong = max(0, inside_strong - 1)
            out.append(token)
            continue

        def _repl(match: re.Match[str]) -> str:
            nonlocal count
            count += 1
            if count <= max_count or inside_anchor or inside_strong:
                return match.group(0)
            return brand

        out.append(pattern.sub(_repl, token))
    return "".join(out)


_HEADING_LOWERCASE_WORDS = {
    "a", "an", "and", "as", "at", "but", "by", "for", "from", "in",
    "nor", "of", "on", "or", "per", "the", "to", "via", "vs", "vs.", "with",
}


def _title_case_headings(html: str) -> str:
    """Apply house title case to h1-h3 headings without touching brand or acronym casing."""
    if not html:
        return html

    def _title_case_text(text: str, at_start: bool) -> tuple[str, bool]:
        parts = re.split(r"(\s+)", text)
        out: list[str] = []
        start = at_start
        for part in parts:
            if not part or part.isspace():
                out.append(part)
                continue
            core = part.strip(".,:;!?()\"'")
            transformed = part
            if not core or any(ch.isdigit() for ch in core) or "$" in core:
                pass  # $10, bet365, 24hr keep casing
            elif core.upper() == core and len(core) > 1:
                pass  # MLB, TOPACTION, acronyms and codes
            elif core.lower() in _HEADING_LOWERCASE_WORDS and not start:
                transformed = part.replace(core, core.lower(), 1)
            elif part[:1].islower():
                transformed = part[:1].upper() + part[1:]
            start = part.rstrip().endswith((":", "-", "—", "–"))
            out.append(transformed)
        return "".join(out), start

    def _fix_heading(match: re.Match[str]) -> str:
        open_tag, inner, close_tag = match.group(1), match.group(3), match.group(4)
        tokens = re.findall(r"<[^>]+>|[^<]+", inner, flags=re.DOTALL)
        rebuilt: list[str] = []
        at_start = True
        for token in tokens:
            if token.startswith("<"):
                rebuilt.append(token)
                continue
            fixed, at_start = _title_case_text(token, at_start)
            rebuilt.append(fixed)
        return f"{open_tag}{''.join(rebuilt)}{close_tag}"

    return re.sub(
        r"(<h([1-3])\b[^>]*>)(.*?)(</h\2>)",
        _fix_heading,
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )


def _normalize_brand_casing(html: str, brand: str) -> str:
    """Fix brand casing in visible copy (e.g. 'draftkings'/'Draftkings' -> 'DraftKings')."""
    raw = str(brand or "").strip()
    if not html or not raw:
        return html
    key = re.sub(r"[^a-z0-9]+", "", raw.lower())
    # Known books get house casing; otherwise the offer-provided casing is canonical.
    display = _SPORTSBOOK_DISPLAY_NAMES.get(key)
    if display is None:
        display = raw
        # An all-lowercase brand outside the house map is likely keyword-derived,
        # not canonical casing - don't force it onto correctly cased copy.
        if display == display.lower():
            return html
    pattern = re.compile(rf"\b{re.escape(display)}\b", flags=re.IGNORECASE)
    out: list[str] = []
    for token in re.findall(r"<[^>]+>|[^<]+", html, flags=re.DOTALL):
        if token.startswith("<"):
            out.append(token)
            continue
        out.append(pattern.sub(display, token))
    return "".join(out)


def _build_length_expansion_section(
    *,
    keyword: str,
    offer: dict[str, Any],
    event_context: str = "",
    bc_core_context: dict[str, Any] | None = None,
    content_mode: str = CONTENT_MODE_SPORTSBOOK,
    bet_example_data: dict[str, Any] | None = None,
) -> str:
    """Create a useful extra editorial section when body copy is under target length."""
    fallback_brand = (
        _sportsbook_display_name(keyword.split()[0]) if keyword.split() else "the operator"
    )
    brand = str(offer.get("brand") or fallback_brand).strip()
    event_label = _extract_featured_label_from_event_context(event_context)
    reward_phrase = _offer_reward_phrase_visible(offer)
    qualifying_amount = _offer_qualifying_amount_text(offer)
    min_odds = str(offer.get("minimum_odds") or extract_minimum_odds(str(offer.get("terms") or "")) or "").strip()
    bc_points = _select_bc_core_editorial_points(bc_core_context, section_kind="overview", max_points=6)
    extra_paragraphs: list[str] = []

    if content_mode == CONTENT_MODE_PREDICTION_MARKET:
        heading = f"What to Watch Before Using {brand}"
        first = (
            f"Before using {keyword}, start with the market you actually want to trade for {event_label or 'the featured event'}. "
            f"The offer works best when the first qualifying action lines up with a real view, not a rushed position just to trigger {reward_phrase}."
        )
        second = (
            "Price movement matters because small contract changes can alter the risk/reward profile quickly. "
            "Check the displayed price, settlement rules, and available liquidity before opening the position."
        )
        extra_paragraphs.extend([
            (
                "Stick to one clear market side instead of jumping across several contracts. "
                "Know the entry price and exactly what has to happen for the contract to settle in your favor before committing."
            ),
            (
                f"Use {reward_phrase} as extra flexibility after the qualifying action, not as a reason to force a larger position. "
                "Several smaller positions across different markets usually get more out of the credit than one oversized trade."
            ),
        ])
    elif content_mode == CONTENT_MODE_DFS:
        heading = f"What to Watch Before Using {brand}"
        first = (
            f"Before using {keyword}, look at the contest format and player pool for {event_label or 'the featured slate'}. "
            f"The offer works best when the qualifying entry fits the lineup or pick'em card you already wanted to build."
        )
        second = (
            f"Treat {reward_phrase} as extra entry flexibility rather than a reason to force one oversized contest. "
            "Smaller entries across a few different builds usually give the bonus more practical value."
        )
        extra_paragraphs.extend([
            (
                "Use the matchup context to decide how aggressive the first entry should be. "
                "A focused single-game slate rewards cleaner correlations, while a larger slate gives you more room to separate player combinations."
            ),
            (
                "Know what the bonus can and cannot do after it posts: whether it works as contest entry credit, "
                "when it expires, and why it is not withdrawable cash."
            ),
        ])
    else:
        return _build_sportsbook_expansion_section(
            keyword=keyword,
            brand=brand,
            event_label=event_label,
            reward_phrase=reward_phrase,
            qualifying_amount=qualifying_amount,
            min_odds=min_odds,
            bc_points=bc_points,
            bet_example_data=bet_example_data,
        )

    third = ""
    if bc_points:
        clean_points = [point for point in bc_points if point]
        if len(clean_points) >= 2:
            third = f"<p>{clean_points[0]} {clean_points[1]}</p>"
        elif clean_points:
            third = f"<p>{clean_points[0]}</p>"

    extra = ""
    if extra_paragraphs:
        extra = "\n" + "\n".join(f"<p>{paragraph}</p>" for paragraph in extra_paragraphs)
    return f"<h2>{heading}</h2>\n<p>{first}</p>\n<p>{second}</p>{third}{extra}"


def _build_sportsbook_expansion_section(
    *,
    keyword: str,
    brand: str,
    event_label: str,
    reward_phrase: str,
    qualifying_amount: str,
    min_odds: str,
    bc_points: list[str],
    bet_example_data: dict[str, Any] | None = None,
) -> str:
    """Render a data-led matchup analysis section in the expert-pick house style."""
    points = [point for point in bc_points if point]
    heading = (
        f"What the Numbers Say About {event_label}"
        if event_label and points
        else f"What to Watch Before Using {brand}"
    )

    paragraphs: list[str] = []

    # Lead with the matchup data, two sentences per paragraph.
    for start in range(0, min(len(points), 6), 2):
        paragraphs.append(" ".join(points[start:start + 2]))

    if not points:
        paragraphs.append(
            f"Before using {keyword}, pick the market for {event_label or 'the featured event'} first and then confirm it fits the offer rules. "
            "The best example is the bet you were already comfortable making."
        )

    # Tie the analysis back to the offer mechanics.
    lead_in = f"That is the backdrop for the first bet with {brand}" if points else f"For the first bet with {brand}"
    requirement_bits: list[str] = []
    if qualifying_amount:
        requirement_bits.append(f"the qualifying wager is {qualifying_amount}")
    if min_odds:
        requirement_bits.append(f"it must meet {min_odds} minimum odds")
    if requirement_bits:
        paragraphs.append(
            f"{lead_in} — {' and '.join(requirement_bits)}, "
            "so the cleanest approach is a standard market that fits those rules: moneyline, spread, or total."
        )
    elif points:
        paragraphs.append(
            f"{lead_in}. The cleanest approach is a standard market you can explain in one sentence: moneyline, spread, or total."
        )
    else:
        paragraphs.append(
            f"{lead_in}, the cleanest approach is a standard market you can explain in one sentence: moneyline, spread, or total."
        )

    paragraphs.append(
        "The bonus value comes after the qualifying action, not from changing the payout on the first wager. "
        f"That makes {reward_phrase} more useful for follow-up markets than for chasing a bigger first bet, "
        "and bonus-bet stakes usually do not return with winnings."
    )

    # Close with the pick when a concrete selection is available.
    data = dict(bet_example_data or {})
    selection = str(data.get("selection") or "").strip()
    if selection:
        odds_text = ""
        try:
            odds_text = f" at {int(float(data.get('odds'))):+d}"
        except (TypeError, ValueError):
            pass
        paragraphs.append(
            f"The play: use the qualifying bet on {selection}{odds_text}, "
            f"then keep {reward_phrase} in reserve for later eligible markets once it posts."
        )

    body = "\n".join(f"<p>{paragraph}</p>" for paragraph in paragraphs)
    return f"<h2>{heading}</h2>\n{body}"


def _extract_fact_numbers(texts: list[str]) -> set[str]:
    """Collect every numeric token present in the provided fact strings."""
    numbers: set[str] = set()
    for text in texts:
        for match in re.findall(r"\d+(?:[.,]\d+)?(?:-\d+)?", str(text or "")):
            token = match.replace(",", "")
            numbers.add(token)
            if "-" in token:
                numbers.update(part for part in token.split("-") if part)
            if "." in token:
                numbers.add(token.split(".")[0])
                # $0.62 contract prices are legitimately restated as "62 cents".
                if token.startswith("0."):
                    numbers.add(token.split(".", 1)[1])
    return numbers


def _narrative_section_is_valid(
    html: str,
    *,
    allowed_numbers: set[str],
    fact_numbers: set[str],
    play_required: bool,
    prediction_market: bool = False,
) -> bool:
    """Accept a composed narrative only when it stays inside the provided facts."""
    if not html:
        return False
    paragraph_count = len(re.findall(r"<p\b", html, flags=re.IGNORECASE))
    if paragraph_count < 2 or paragraph_count > 5:
        return False
    if re.search(r"<(?:h[1-6]|ol|ul|table|script)\b", html, flags=re.IGNORECASE):
        return False
    plain = _html_to_plain_text(html)
    word_count = len(plain.split())
    if word_count < 60 or word_count > 300:
        return False
    if "!" in plain:
        return False
    # Prompt-label echoes read broken in copy ("a qualifying wager $5 and reward $200").
    if re.search(r"\b(?:qualifying wager|reward)\s+\$\d", plain):
        return False
    banned = ("bc core", "source data", "internal note", "data feed", "our model", "our projections")
    lowered = plain.lower()
    if any(token in lowered for token in banned):
        return False
    if prediction_market and re.search(r"\b(?:bet|bets|betting|wager|wagers|sportsbook|bonus bets)\b", lowered):
        return False
    used_numbers = _extract_fact_numbers([plain])
    if not used_numbers.issubset(allowed_numbers):
        return False
    if len(used_numbers & fact_numbers) < 2:
        return False
    if play_required and "The play:" not in plain:
        return False
    if not play_required and "The play:" in plain:
        return False
    return True


async def _compose_numbers_narrative_section(
    *,
    keyword: str,
    offer: dict[str, Any],
    event_context: str = "",
    bc_core_context: dict[str, Any] | None = None,
    bet_example_data: dict[str, Any] | None = None,
    prediction_market: bool = False,
) -> str | None:
    """Compose the matchup-analysis section in the expert-pick house register.

    Facts are hard-validated: every number in the output must come from the
    provided inputs. Returns None when composition fails so the caller can
    fall back to the deterministic section.
    """
    event_label = _extract_featured_label_from_event_context(event_context)
    schedule_meta = re.compile(r"^(?:This spot falls in|The matchup is set for)\b")
    bc_points = [
        point
        for point in _select_bc_core_editorial_points(
            bc_core_context,
            section_kind="overview",
            max_points=8,
            prediction_market=prediction_market,
        )
        if point and not schedule_meta.match(point.strip())
    ][:6]
    if not event_label or len(bc_points) < 2:
        return None

    fallback_brand = _sportsbook_display_name(keyword.split()[0]) if keyword.split() else "the operator"
    brand = str(offer.get("brand") or fallback_brand).strip()
    reward_phrase = _offer_reward_phrase_visible(offer)
    if prediction_market:
        reward_phrase = reward_phrase.replace("bonus bets", "promo credits")
    qualifying_amount = _offer_qualifying_amount_text(offer)
    min_odds = str(offer.get("minimum_odds") or extract_minimum_odds(str(offer.get("terms") or "")) or "").strip()

    data = dict(bet_example_data or {})
    selection = str(data.get("selection") or "").strip()
    odds_text = ""
    market_title = str(data.get("market_title") or "").strip()
    if selection and prediction_market:
        try:
            odds_text = f" at about ${float(data.get('entry_price')):.2f} per contract"
        except (TypeError, ValueError):
            pass
    elif selection:
        try:
            odds_text = f" at {int(float(data.get('odds'))):+d}"
        except (TypeError, ValueError):
            pass

    facts_md = "\n".join(f"- {point}" for point in bc_points)
    short_reward = re.sub(r"\s+instantly\s*$", "", reward_phrase, flags=re.IGNORECASE)
    offer_sentence_bits = []
    if qualifying_amount:
        offer_sentence_bits.append(f"a {qualifying_amount} first bet")
    if short_reward:
        offer_sentence_bits.append(f"{short_reward}")
    brand_possessive = f"{brand}'" if brand.endswith("s") else f"{brand}'s"
    first_action = "first deposit" if prediction_market else "first bet"
    offer_sentence_bits = [
        bit.replace("first bet", first_action) for bit in offer_sentence_bits
    ]
    offer_example = (
        f"{brand_possessive} current offer turns {' into '.join(offer_sentence_bits)}, and this is the kind of spot to use it."
        if len(offer_sentence_bits) == 2
        else f"{brand_possessive} current offer is live for new users, and this is the kind of spot to use it."
    )
    min_odds_note = f" The qualifying bet must meet {min_odds} minimum odds." if min_odds and not prediction_market else ""
    if prediction_market:
        play_target = f"the {selection} side of {market_title}" if market_title else selection
        play_block = (
            f"THE PLAY (final paragraph on its own, must start exactly with \"The play:\"):\n- Take {play_target}{odds_text}, then keep {short_reward} for later eligible markets.\n\n"
            if selection
            else ""
        )
    else:
        play_block = (
            f"THE PLAY (final paragraph on its own, must start exactly with \"The play:\"):\n- Back {selection}{odds_text} with the qualifying bet, then keep {short_reward} for later eligible markets.\n\n"
            if selection
            else ""
        )

    close_clause = "then a clear play" if selection else "closing on the sharpest takeaway for one side"
    system_prompt = (
        (
            "You are a senior prediction-market editor for Action Network's Top Stories. "
            f"You write tight, confident, data-driven matchup analysis: stakes first, then the numbers built into an argument for one side, {close_clause}. "
            "Use prediction-market language only: market, position, contract, trade, settle. Never use bet, betting, wager, sportsbook, or bonus bets. "
            "Output clean HTML only: exactly 3 or 4 separate <p> paragraphs. No headings, no lists, no markdown, no exclamation points."
        )
        if prediction_market
        else (
            "You are a senior sports betting editor for Action Network's Top Stories. "
            f"You write tight, confident, data-driven matchup analysis: stakes first, then the numbers built into an argument for one side, {close_clause}. "
            "Output clean HTML only: exactly 3 or 4 separate <p> paragraphs. No headings, no lists, no markdown, no exclamation points."
        )
    )
    prompt = f"""Write the body paragraphs for a section headed "What the Numbers Say About {event_label}".

MATCHUP FACTS - the ONLY numbers you may use; quote each figure exactly as written and never invent or derive new ones:
{facts_md}

OFFER TIE-IN: one short sentence at the end of the second-to-last paragraph, modeled on this (adapt the wording, keep the numbers exact): "{offer_example}"{min_odds_note}

{play_block}REQUIREMENTS:
- Output 3 or 4 separate <p>...</p> paragraphs. Never one long paragraph.
- Open with the sharpest fact - form, a projection, or the market lean - and what it means for {event_label}. Never open with season, schedule, or broadcast labels.
- Build the facts into an argument for one side instead of listing them. Connect them with editorial reasoning, e.g. "that is the profile of a team that...", "which is exactly the matchup where...".
- Do not invent injuries, crowd, venue, weather, or history that is not listed. Never mention data sources, feeds, models, or anything internal.
- 120 to 220 words total. No exclamation points.
- Use the primary keyword "{keyword}" at most once, as a natural phrase such as "the {keyword} offer" - or not at all.{'' if selection else chr(10) + '- Do not include a "The play:" line or recommend a specific position; close on the strongest takeaway instead.'}"""

    allowed_numbers = _extract_fact_numbers(
        bc_points
        + [reward_phrase, qualifying_amount, min_odds, selection, odds_text, market_title, event_label, event_context]
        + [str(data.get(key) or "") for key in ("bet_amount", "potential_profit", "position_amount", "entry_price", "potential_payout", "contracts", "settlement_price")]
    )
    fact_numbers = _extract_fact_numbers(bc_points)

    for _ in range(2):
        try:
            result = await generate_completion(
                prompt=prompt,
                system_prompt=system_prompt,
                temperature=0.7,
                max_tokens=800,
            )
        except Exception:
            return None
        cleaned = _strip_source_and_prompt_leaks(str(result or "").strip())
        cleaned = _convert_availability_labels_to_prose(cleaned)
        cleaned = _decapitalize_inline_reward_mentions(cleaned)
        if _narrative_section_is_valid(
            cleaned,
            allowed_numbers=allowed_numbers,
            fact_numbers=fact_numbers,
            play_required=bool(selection),
            prediction_market=prediction_market,
        ):
            return f"<h2>What the Numbers Say About {event_label}</h2>\n{cleaned}"
    return None


def _insert_section_before_terms(html: str, section: str) -> str:
    """Insert an article section ahead of the terms block (or disclaimer, or append)."""
    insert_before = re.search(
        r"<h[1-6]\b[^>]*>[^<]*(?:Terms|Conditions|Fine Print|Rules)[^<]*</h[1-6]>",
        html,
        flags=re.IGNORECASE,
    )
    if insert_before:
        return html[: insert_before.start()] + section + "\n" + html[insert_before.start():]
    disclaimer = re.search(
        r"<p><em>.*?(?:Gambling problem|Please play responsibly|Terms apply).*?</em></p>",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if disclaimer:
        return html[: disclaimer.start()] + section + "\n" + html[disclaimer.start():]
    return f"{html}\n{section}"


_ANALYSIS_SECTION_HEADING_RE = r"<h[1-6]\b[^>]*>\s*(?:What to Watch Before Using|What the Numbers Say About)\b"


async def _ensure_matchup_analysis_section(
    html: str,
    *,
    keyword: str,
    offer: dict[str, Any],
    event_context: str = "",
    bc_core_context: dict[str, Any] | None = None,
    content_mode: str = CONTENT_MODE_SPORTSBOOK,
    bet_example_data: dict[str, Any] | None = None,
) -> str:
    """Give sportsbook and prediction-market articles with matchup data an expert-analysis section."""
    if not html or content_mode not in (CONTENT_MODE_SPORTSBOOK, CONTENT_MODE_PREDICTION_MARKET):
        return html
    if re.search(_ANALYSIS_SECTION_HEADING_RE, html, flags=re.IGNORECASE):
        return html
    section = await _compose_numbers_narrative_section(
        keyword=keyword,
        offer=offer,
        event_context=event_context,
        bc_core_context=bc_core_context,
        bet_example_data=bet_example_data,
        prediction_market=content_mode == CONTENT_MODE_PREDICTION_MARKET,
    )
    if not section:
        return html
    return _insert_section_before_terms(html, section)


async def _ensure_editorial_body_length(
    html: str,
    *,
    keyword: str,
    offer: dict[str, Any],
    event_context: str = "",
    bc_core_context: dict[str, Any] | None = None,
    content_mode: str = CONTENT_MODE_SPORTSBOOK,
    target_words: int = 500,
    bet_example_data: dict[str, Any] | None = None,
) -> str:
    """Aim for ~500 editorial body words, excluding signup steps and compliance sections."""
    if not html or target_words <= 0:
        return html
    if _body_word_count_for_editorial_target(html) >= target_words:
        return html
    if re.search(_ANALYSIS_SECTION_HEADING_RE, html, flags=re.IGNORECASE):
        return html

    section = None
    if content_mode in (CONTENT_MODE_SPORTSBOOK, CONTENT_MODE_PREDICTION_MARKET):
        section = await _compose_numbers_narrative_section(
            keyword=keyword,
            offer=offer,
            event_context=event_context,
            bc_core_context=bc_core_context,
            bet_example_data=bet_example_data,
            prediction_market=content_mode == CONTENT_MODE_PREDICTION_MARKET,
        )
    if not section:
        section = _build_length_expansion_section(
            keyword=keyword,
            offer=offer,
            event_context=event_context,
            bc_core_context=bc_core_context,
            content_mode=content_mode,
            bet_example_data=bet_example_data,
        )
    return _insert_section_before_terms(html, section)


def _secondary_keyword_count(html: str, phrase: str) -> int:
    if not html or not phrase:
        return 0
    plain = _html_to_plain_text(html)
    return len(re.findall(re.escape(phrase), plain, flags=re.IGNORECASE))


def _enforce_secondary_keyword_mentions(html: str, secondary_keywords: list[str] | None) -> str:
    """Clean forced filler and add light, fact-safe secondary coverage when missing."""
    phrases = [str(x).strip() for x in (secondary_keywords or []) if str(x).strip()]
    if not html or not phrases:
        return html

    result = html
    paragraph_pattern = re.compile(r"<p>(.*?)</p>", flags=re.IGNORECASE | re.DOTALL)
    if not paragraph_pattern.search(result):
        return result

    for phrase in phrases:
        escaped = re.escape(phrase)
        forced_patterns = [
            rf"\s*It also ties into {escaped}\.",
            rf"\s*That keeps {escaped} in the mix\.",
            rf"\s*This is also relevant for {escaped}\.",
            rf"\s*Readers comparing {escaped} should start with the same offer details\.",
            rf"\s*The same setup matters for anyone tracking {escaped}\.",
            rf"\s*This section also gives readers a cleaner path into {escaped}\.",
            rf"\s*For readers comparing {escaped}, the same offer details and availability notes still apply\.",
            rf"\s*That context also helps if you are checking {escaped} before signing up\.",
            rf"\s*Use the same offer and availability checks when reviewing {escaped}\.",
        ]
        for pattern in forced_patterns:
            result = _rewrite_html_text_nodes(
                result,
                lambda text, pattern=pattern: re.sub(pattern, "", text, flags=re.IGNORECASE),
            )

    def _safe_paragraph_spans(source: str) -> list[re.Match[str]]:
        blocked_heading = ""
        spans: list[re.Match[str]] = []
        for match in re.finditer(
            r"<h[1-6]\b[^>]*>(.*?)</h[1-6]>|<p\b[^>]*>(.*?)</p>",
            source,
            flags=re.IGNORECASE | re.DOTALL,
        ):
            heading = match.group(1)
            paragraph = match.group(2)
            if heading is not None:
                blocked_heading = _html_to_plain_text(heading).lower()
                continue
            if paragraph is None:
                continue
            if any(token in blocked_heading for token in ("terms", "conditions", "fine print", "rules", "settlement")):
                continue
            if any(token in blocked_heading for token in ("sign up", "sign-up", "signup", "claim")):
                continue
            plain = _html_to_plain_text(paragraph).strip()
            if len(plain.split()) < 12:
                continue
            if "data-id=\"switchboard_tracking\"" in paragraph.lower() or "[bam-inline-promotion" in paragraph.lower():
                continue
            spans.append(match)
        return spans

    sentence_templates = [
        "The same checks matter for {phrase}: offer amount, code status, and available states.",
        "Use {phrase} searches to confirm the selected offer instead of relying on stale terms.",
    ]

    for phrase in phrases:
        # Only hard-fill when the model missed repeated coverage. Avoid overdoing it.
        while _secondary_keyword_count(result, phrase) < 2:
            spans = _safe_paragraph_spans(result)
            if not spans:
                break
            count = _secondary_keyword_count(result, phrase)
            target = spans[min(count, len(spans) - 1)]
            paragraph = target.group(0)
            inner = target.group(2) or ""
            addition = sentence_templates[count % len(sentence_templates)].format(phrase=escape(phrase))
            if re.search(re.escape(phrase), _html_to_plain_text(inner), flags=re.IGNORECASE):
                break
            updated = f"<p>{inner.strip()} {addition}</p>"
            result = result[: target.start()] + updated + result[target.end():]

    return _normalize_visible_punctuation(result)


def _strip_formatting_from_headings(html: str) -> str:
    """Keep headings plain text even if downstream post-processing injects markup."""
    if not html:
        return html

    def _clean_heading(match: re.Match[str]) -> str:
        tag = match.group(1)
        inner = match.group(2)
        cleaned = re.sub(r"<[^>]+>", "", inner)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return f"<{tag}>{cleaned}</{tag}>"

    return re.sub(
        r"<(h[1-6])>(.*?)</\1>",
        _clean_heading,
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )


def _clean_orphaned_keyword_page_references(html: str, keyword: str) -> str:
    """Remove awkward 'page: keyword' leftovers after link trimming."""
    if not html or not keyword:
        return html
    colon_pattern = re.compile(
        rf"(page:\s*)(?:<strong>)?{re.escape(keyword)}(?:</strong>)?(\.?)",
        flags=re.IGNORECASE,
    )
    at_pattern = re.compile(
        rf"(page\s+at\s+)(?:<strong>)?{re.escape(keyword)}(?:</strong>)?(\.?)",
        flags=re.IGNORECASE,
    )
    cleaned = colon_pattern.sub("page.", html)
    return at_pattern.sub("page", cleaned)


def _unwrap_generic_offer_strong(html: str, brand: str = "") -> str:
    """Do not leave generic 'Brand offer' wording bolded in reader-facing prose."""
    if not html:
        return html
    labels = ["the offer"]
    brand_clean = str(brand or "").strip()
    if brand_clean:
        labels.append(f"{brand_clean} offer")
    result = html
    for label in labels:
        result = re.sub(
            rf"<strong>\s*({re.escape(label)})\s*</strong>",
            r"\1",
            result,
            flags=re.IGNORECASE,
        )
    return result


def _ensure_top_story_tracking_tag(content: str) -> str:
    """Append the canonical analytics event tag once to every generated article."""
    content = str(content or "")
    pattern = re.compile(
        r"<script\b[^>]*>\s*gtag\(\s*['\"]event['\"]\s*,\s*['\"]view_top_story['\"]\s*\)\s*;?\s*</script>",
        flags=re.IGNORECASE | re.DOTALL,
    )
    cleaned = pattern.sub("", content).rstrip()
    if not cleaned:
        return TOP_STORY_TRACKING_TAG
    return f"{cleaned}\n{TOP_STORY_TRACKING_TAG}"


def _normalize_matchup_vs_notation(html: str) -> str:
    """Replace visible-text matchup '@' notation with 'vs.'."""
    if not html:
        return html
    return _rewrite_html_text_nodes(
        html,
        lambda text: re.sub(r"\s@\s", " vs. ", text),
    )


def _trim_repeated_phrase_in_html(html: str, phrase: str, max_occurrences: int, replacement: str) -> str:
    """Replace repeated phrase occurrences in visible text after a max count."""
    if not html or not phrase or max_occurrences < 0:
        return html

    count = 0
    pattern = re.compile(re.escape(phrase), flags=re.IGNORECASE)

    def _transform(text: str) -> str:
        nonlocal count

        def _repl(match: re.Match[str]) -> str:
            nonlocal count
            count += 1
            if count <= max_occurrences:
                return match.group(0)
            return replacement

        return pattern.sub(_repl, text)

    return _rewrite_html_text_nodes(html, _transform)


def _trim_dangling_paragraph_endings(html: str) -> str:
    """Clean up paragraphs that end in a dangling conjunction fragment."""
    if not html:
        return html
    patterns = [
        (r",?\s*(?:and\s+)?remember\s*</p>", ".</p>"),
        (r",\s*(?:and|but|so|then)\s*</p>", ".</p>"),
        (r"\(\s*</p>", ".</p>"),
    ]
    cleaned = html
    for pattern, replacement in patterns:
        cleaned = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE)
    return cleaned


def _strip_source_and_prompt_leaks(html: str) -> str:
    """Remove internal prompt/source wording that should never appear in articles."""
    if not html:
        return html

    patterns = [
        r"\s*[^.]*\bfor this article(?:'|â€™|’)?s requested state context[^.]*\.?",
        r"\s*[^.]*\bno matched event data here[^.]*\.?",
        r"\s*[^.]*\bpre-loaded market match[^.]*\.?",
        r"\s*[^.]*\bclean event match\b[^.]*\bfeed\b[^.]*\.?",
        r"\s*[^.]*\bevent feed\b[^.]*\balign[^.]*\.?",
        r"\s*[^.]*\bevent feed\b[^.]*\.?",
        r"\s*[^.]*\bour feed\b[^.]*\.?",
        r"\s*[^.]*\bon our end\b[^.]*\.?",
        r"\s*[^.]*\binternal expertise notes?[^.]*\.?",
        r"\s*[^.]*\binternal matchup notes?[^.]*\.?",
        r"\s*[^.]*\bBC Core\b[^.]*\.?",
        r"\s*[^.]*\bexcerpt\b[^.]*\balign[^.]*\.?",
    ]
    cleaned = html
    for pattern in patterns:
        cleaned = _rewrite_html_text_nodes(
            cleaned,
            lambda text, pattern=pattern: re.sub(pattern, "", text, flags=re.IGNORECASE),
        )

    replacements = [
        (r"\bplayoff-style\b", "playoff"),
        (r"\bYou(?:'|â€™|’)?ll typically see\b", "The market board shows"),
        (r"\bcheck the live board\b", "use the selected market"),
        (r"\bcheck live now\b", "review the selected market"),
        (r"\bminimum odds\s+([+-]?\d+)\s+of greater\b", r"Minimum odds \1 or greater"),
        (r"\bEPL wager\b", "soccer wager"),
        (r"\bhas to be placed at odds of\b", "must meet odds of"),
        (r"\bmust be placed at odds of\b", "must meet odds of"),
    ]
    for pattern, replacement in replacements:
        cleaned = _rewrite_html_text_nodes(
            cleaned,
            lambda text, pattern=pattern, replacement=replacement: re.sub(
                pattern,
                replacement,
                text,
                flags=re.IGNORECASE,
            ),
        )
    cleaned = re.sub(r"<p\b[^>]*>\s*</p>", "", cleaned, flags=re.IGNORECASE)
    return _normalize_visible_punctuation(cleaned)


def _strip_market_mismatch_phrasing(html: str, market: str = "US") -> str:
    """Remove US-market phrasing from Canada-market output."""
    if not html:
        return html
    if str(market or "US").strip().upper() != "CA":
        return html

    replacements = [
        (r"\b21\+\s+and\s+U\.?S\.?\s+residents\s+where\s+permitted(?:\s*\(void where prohibited\))?", "legal-age users in the listed Canadian provinces where permitted"),
        (r"\bU\.?S\.?\s+residents\s+where\s+permitted\b", "users in the listed Canadian provinces where permitted"),
        (r"\bU\.?S\.?\s+residents\b", "users in the listed Canadian provinces"),
        (r"\bU\.?S\.?\s+users\b", "Canadian users in listed provinces"),
        (r"\bUS\s+states\b", "Canadian provinces"),
        (r"\bU\.?S\.?\s+states\b", "Canadian provinces"),
        (r"\beligible states\b", "listed provinces"),
        (r"\bEligible States\b", "Eligible Provinces"),
        (r"\bStates Available\b", "Provinces Available"),
        (r"\bvaries by state\b", "varies by province"),
        (r"\bstate availability\b", "province availability"),
        (r"\bstate-specific\b", "province-specific"),
        (r"\bnationwide\b", "available in listed provinces"),
        (r"\bMust be 21\+\b", "Legal age varies by province"),
        (r"\b21\+\.\s*", ""),
    ]
    cleaned = html
    for pattern, replacement in replacements:
        cleaned = _rewrite_html_text_nodes(
            cleaned,
            lambda text, pattern=pattern, replacement=replacement: re.sub(
                pattern,
                replacement,
                text,
                flags=re.IGNORECASE,
            ),
        )
    return _normalize_visible_punctuation(cleaned)


def _apply_generation_quality_postprocess(html: str, keyword: str, market: str = "US") -> str:
    """Final article cleanup for intro consistency, keyword placement, and repetition."""
    if not html:
        return html
    html = _soften_repetitive_intro_opener(html)
    html = _ensure_keyword_in_first_paragraph(html, keyword)
    html = _polish_intro_fallback_phrases(html)
    html = _polish_worked_example_conditionals(html)
    html = _polish_conditional_user_openers(html)
    html = _normalize_matchup_vs_notation(html)
    html = _trim_repeated_phrase_in_html(html, "see full terms", max_occurrences=2, replacement="see terms")
    html = _remove_inline_compliance_fragments(html)
    html = _strip_source_and_prompt_leaks(html)
    html = _convert_availability_labels_to_prose(html)
    html = _decapitalize_inline_reward_mentions(html)
    html = _strip_market_mismatch_phrasing(html, market)
    html = _trim_dangling_paragraph_endings(html)
    html = _normalize_visible_punctuation(html)
    return html


def _count_paragraphs(html: str) -> int:
    """Count paragraph tags in an HTML fragment."""
    if not html:
        return 0
    return len(re.findall(r"<p\b[^>]*>.*?</p>", html, flags=re.IGNORECASE | re.DOTALL))


def _protect_humanizer_fragments(html: str) -> tuple[str, dict[str, str]]:
    """Replace fragile HTML fragments with placeholders before the humanizer rewrite."""
    if not html:
        return html, {}

    protected = html
    replacements: dict[str, str] = {}
    counter = 0
    patterns = [
        re.compile(r"<a\b[^>]*>.*?</a>", flags=re.IGNORECASE | re.DOTALL),
        re.compile(r"<strong\b[^>]*>.*?</strong>", flags=re.IGNORECASE | re.DOTALL),
        re.compile(r"<em\b[^>]*>.*?</em>", flags=re.IGNORECASE | re.DOTALL),
    ]
    for pattern in patterns:
        while True:
            match = pattern.search(protected)
            if not match:
                break
            token = f"[[KEEP_{counter}]]"
            replacements[token] = match.group(0)
            protected = protected[:match.start()] + token + protected[match.end():]
            counter += 1
    return protected, replacements


def _restore_humanizer_fragments(html: str, replacements: dict[str, str]) -> str:
    """Restore protected placeholders after the humanizer rewrite."""
    restored = html
    for token, original in replacements.items():
        restored = restored.replace(token, original)
    return restored


def _extract_humanizer_markers(html: str, offer: dict[str, Any] | None = None) -> list[str]:
    """Extract hard-fact markers that must survive a prose rewrite."""
    plain = _html_to_plain_text(html)
    if not plain:
        return []

    markers: list[str] = []
    patterns = [
        r"\$\d[\d,]*(?:\.\d{1,2})?",
        r"(?<!\w)[+-]\d{2,4}(?!\w)",
        r"\b(?:18\+|19\+|21\+)\b",
        r"\b\d{1,2}:\d{2}\s*(?:AM|PM)\s*ET\b",
        r"\b(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+[A-Z][a-z]+\s+\d{1,2}(?:,\s+\d{4})?\b",
        r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2}(?:,\s+\d{4})?\b",
        r"States Available:\s*[^.]+",
        r"(?:The offer is|It(?:'|’)s) available in\s+[^.]+",
        r"(?:Offer not valid in|not valid in)\s+[^.]+",
        r"\b(?:ESPN\+|ESPN|ABC|CBS|NBC|FOX|FS1|TBS|TNT|Peacock)\b",
    ]
    for pattern in patterns:
        for match in re.findall(pattern, plain, flags=re.IGNORECASE):
            marker = re.sub(r"\s+", " ", str(match).strip())
            if marker and marker.lower() not in {m.lower() for m in markers}:
                markers.append(marker)

    bonus_code = str((offer or {}).get("bonus_code") or "").strip()
    if bonus_code and re.search(re.escape(bonus_code), plain, flags=re.IGNORECASE):
        markers.append(bonus_code)
    return markers


def _humanizer_preserves_markers(
    original_html: str,
    rewritten_html: str,
    replacements: dict[str, str],
    offer: dict[str, Any] | None = None,
) -> bool:
    """Return True when a rewritten section preserves protected fragments and hard facts."""
    if _count_paragraphs(original_html) != _count_paragraphs(rewritten_html):
        return False
    if re.search(r"<(?:h[1-6]|ol|ul|table)\b", rewritten_html, flags=re.IGNORECASE):
        return False
    for token in replacements:
        if token not in rewritten_html:
            return False

    restored = _restore_humanizer_fragments(rewritten_html, replacements)
    rewritten_plain = re.sub(r"\s+", " ", _html_to_plain_text(restored)).lower()
    for marker in _extract_humanizer_markers(original_html, offer=offer):
        normalized = re.sub(r"\s+", " ", marker).lower()
        if normalized and normalized not in rewritten_plain:
            return False
    return True


def _is_humanizer_safe_heading(title_lower: str) -> bool:
    """Return True when the section is safe for a prose-only humanizer pass."""
    if not title_lower:
        return True
    if _is_signup_heading(title_lower):
        return False
    if _is_claim_heading(title_lower, False):
        return False
    if _is_daily_promos_heading(title_lower):
        return False
    if any(x in title_lower for x in ["terms", "conditions", "fine print", "house rules", "market rules", "settlement"]):
        return False
    return True


def _segment_article_for_humanizer(html: str) -> list[dict[str, str]]:
    """Split article HTML into static and rewriteable paragraph groups."""
    if not html:
        return []

    tokens = re.findall(
        r"<!--.*?-->|<h[1-6][^>]*>.*?</h[1-6]>|<ol\b[^>]*>.*?</ol>|<ul\b[^>]*>.*?</ul>|<table\b[^>]*>.*?</table>|<p\b[^>]*>.*?</p>|[^<]+",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    parts: list[dict[str, str]] = []
    current: list[str] = []
    current_heading_lower = ""
    seen_h2 = False

    def flush() -> None:
        nonlocal current
        if not current:
            return
        block_html = "".join(current)
        is_intro = not seen_h2
        if re.search(r"<p\b", block_html, flags=re.IGNORECASE) and (is_intro or _is_humanizer_safe_heading(current_heading_lower)):
            parts.append({
                "type": "rewrite",
                "kind": "intro" if is_intro else "body",
                "heading": current_heading_lower,
                "html": block_html,
            })
        else:
            parts.append({"type": "static", "html": block_html})
        current = []

    for token in tokens:
        if re.match(r"<p\b", token, flags=re.IGNORECASE):
            current.append(token)
            continue
        if not token.strip():
            if current:
                current.append(token)
            else:
                parts.append({"type": "static", "html": token})
            continue
        flush()
        parts.append({"type": "static", "html": token})
        if re.match(r"<h[23]\b", token, flags=re.IGNORECASE):
            seen_h2 = True
            current_heading_lower = _sanitize_heading_text(re.sub(r"<[^>]+>", " ", token)).lower()
        elif re.match(r"<h1\b", token, flags=re.IGNORECASE):
            current_heading_lower = ""
        elif re.match(r"<(?:ol|ul|table)\b", token, flags=re.IGNORECASE):
            # Keep current heading for the following prose block.
            pass

    flush()
    return parts


async def _humanize_article_html(
    html: str,
    *,
    keyword: str,
    offer: dict[str, Any] | None = None,
    content_mode: str = CONTENT_MODE_SPORTSBOOK,
) -> str:
    """Polish safe prose sections so the draft reads less tool-shaped without changing facts."""
    if not html:
        return html

    segments = _segment_article_for_humanizer(html)
    rewrite_segments = [segment for segment in segments if segment.get("type") == "rewrite" and segment.get("html")]
    if not rewrite_segments:
        return html

    protected_segments: list[dict[str, Any]] = []
    for segment in rewrite_segments:
        protected_html, replacements = _protect_humanizer_fragments(segment["html"])
        protected_segments.append({
            **segment,
            "protected_html": protected_html,
            "replacements": replacements,
        })

    schema = {
        "type": "object",
        "properties": {
            "rewrites": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": len(protected_segments),
                "maxItems": len(protected_segments),
            }
        },
        "required": ["rewrites"],
        "additionalProperties": False,
    }

    mode_line = (
        "Use prediction-market wording only (market, trade, position, contract). Never use sportsbook or bonus-bet language."
        if content_mode == CONTENT_MODE_PREDICTION_MARKET
        else "Use DFS wording only (entries, contests, picks, lineup, fantasy app). Never use sportsbook or bonus-bet language."
        if content_mode == CONTENT_MODE_DFS
        else "Use sportsbook wording naturally and directly."
    )
    section_lines: list[str] = []
    for idx, segment in enumerate(protected_segments):
        heading_note = f"; heading: {segment['heading']}" if segment.get("heading") else ""
        section_lines.append(
            f"SECTION {idx + 1} ({segment['kind']}{heading_note}):\n{segment['protected_html']}"
        )
    sections_md = "\n\n".join(section_lines)

    prompt = f"""Rewrite these HTML sections so they read more like polished editorial copy and less like raw AI output.

CRITICAL:
- Preserve every placeholder token exactly as written, including [[KEEP_n]].
- Do not change or remove any numbers, odds, promo codes, dates, times, state lists, or offer mechanics.
- Keep the same number of <p> paragraphs in each section.
- Do not add headings, lists, tables, disclaimers, or new links.
- Prefer active voice, cleaner sentence rhythm, and less repetitive phrasing.
- Remove stock filler and chatbot-shaped transitions.
- Keep the exact keyword phrase intact when it already appears: {keyword}
- {mode_line}

Return JSON with a single key "rewrites" containing the rewritten HTML strings in the same order.

{sections_md}
"""

    try:
        data = await generate_completion_structured(
            prompt=prompt,
            system_prompt="You are a senior editorial polish assistant. Rewrite safely and preserve locked facts exactly.",
            schema=schema,
            name="humanized_sections",
            description="Fact-locked editorial rewrites for safe article sections",
            temperature=0.8,
            max_tokens=2200,
        )
        rewrites = data.get("rewrites", []) if isinstance(data, dict) else []
        if len(rewrites) != len(protected_segments):
            return html
    except Exception:
        return html

    rewrite_iter = iter(zip(protected_segments, rewrites))
    rendered_parts: list[str] = []
    for part in segments:
        if part.get("type") != "rewrite":
            rendered_parts.append(part.get("html", ""))
            continue
        original_segment, rewritten = next(rewrite_iter)
        candidate = str(rewritten or "").strip()
        if not candidate:
            rendered_parts.append(original_segment["html"])
            continue
        if not _humanizer_preserves_markers(
            original_segment["html"],
            candidate,
            original_segment["replacements"],
            offer=offer,
        ):
            rendered_parts.append(original_segment["html"])
            continue
        restored = _restore_humanizer_fragments(candidate, original_segment["replacements"])
        rendered_parts.append(_normalize_visible_punctuation(restored))

    return "".join(rendered_parts)


def _ensure_single_disclaimer(html: str, disclaimer: str) -> str:
    """Ensure the disclaimer appears only once at the end of the article."""
    if not disclaimer:
        return html
    pattern = rf"<p><em>{re.escape(disclaimer)}</em></p>\s*"
    cleaned = re.sub(pattern, "", html, flags=re.IGNORECASE)
    return cleaned.rstrip() + f"\n<p><em>{disclaimer}</em></p>"


def _append_required_property_links(
    html: str,
    property_key: str,
    prediction_market: bool = False,
    dfs_mode: bool = False,
) -> str:
    """Leave extra property-level resource links out of the draft by default."""
    return html


_SPORTSBOOK_DISPLAY_NAMES = {
    "draftkings": "DraftKings",
    "fanduel": "FanDuel",
    "betmgm": "BetMGM",
    "caesars": "Caesars",
    "bet365": "bet365",
    "fanatics": "Fanatics Sportsbook",
    "hardrock": "Hard Rock Bet",
    "hard_rock": "Hard Rock Bet",
    "espnbet": "ESPN BET",
}


def _sportsbook_display_name(book: str) -> str:
    key = re.sub(r"[^a-z0-9]+", "", str(book or "").lower())
    if key in _SPORTSBOOK_DISPLAY_NAMES:
        return _SPORTSBOOK_DISPLAY_NAMES[key]
    raw = str(book or "").strip()
    return raw.title() if raw else "the selected sportsbook"


def _extract_matchup_from_event_context_text(event_context: str) -> str:
    return _extract_featured_label_from_event_context(event_context, games_only=True)


def _extract_featured_label_from_event_context(event_context: str, games_only: bool = False) -> str:
    """Extract the featured game/event label from context when available."""
    if not event_context:
        return ""
    patterns = [r"Featured game:\s*(.+?)(?:\.\s+(?:Game time|Network):|$)"]
    if not games_only:
        patterns.append(r"Featured event:\s*(.+?)(?:\.\s+(?:Game time|Network):|$)")
    for pattern in patterns:
        match = re.search(pattern, event_context, flags=re.IGNORECASE)
        if match:
            label = match.group(1).strip().rstrip(".")
            label = re.sub(r"\s@\s", " vs. ", label)
            return re.sub(r"\s+", " ", label)
    return ""


def _parse_money_value(value: Any) -> float | None:
    """Parse a simple currency string like '$50' into a float."""
    if value is None:
        return None
    match = re.search(r"(\d+(?:\.\d+)?)", str(value))
    if not match:
        return None
    try:
        return float(match.group(1))
    except (TypeError, ValueError):
        return None


def _offer_reward_phrase(offer: dict[str, Any]) -> str:
    """Return a concise reward phrase like '$50 in bonus bets'."""
    offer = offer or {}
    offer_text = str(offer.get("offer_text") or offer.get("affiliate_offer") or "").strip()
    details = extract_offer_amount_details(offer_text)
    reward_amount = (
        offer.get("bonus_amount")
        or offer.get("reward_amount")
        or details.get("reward_amount")
        or extract_bonus_amount(offer_text)
    )
    reward_label = str(offer.get("reward_label") or details.get("reward_label") or "").strip().lower()
    if not reward_label:
        reward_label = "bonus bets"
    if reward_amount:
        return f"{reward_amount} in {reward_label}"
    return reward_label


def _decapitalize_marketing_words(label: str, brand: str = "") -> str:
    """Lowercase Title-Case marketing words mid-sentence, keeping branded labels intact."""
    words = str(label or "").split()
    brand_tokens = {token.lower() for token in str(brand or "").split() if token}
    if brand_tokens and any(word.lower().strip(".,") in brand_tokens for word in words):
        # Branded currency labels ("Novig Coins", "FanCash") keep their casing.
        return " ".join(words)
    out: list[str] = []
    for word in words:
        # Plain Title-Case words ("Bonus", "Bets", "Instantly") read as marketing
        # shouting mid-sentence; brands (DraftKings), acronyms (DFS), and tokens
        # with digits ($200, 24hr) keep their casing.
        out.append(word.lower() if re.fullmatch(r"[A-Z][a-z]+", word) else word)
    return " ".join(out)


def _offer_reward_phrase_visible(offer: dict[str, Any]) -> str:
    """Return a visible-copy reward phrase that reads naturally mid-sentence."""
    offer = offer or {}
    offer_text = str(offer.get("offer_text") or offer.get("affiliate_offer") or "").strip()
    details = extract_offer_amount_details(offer_text)
    reward_amount = (
        offer.get("bonus_amount")
        or offer.get("reward_amount")
        or details.get("reward_amount")
        or extract_bonus_amount(offer_text)
    )
    reward_label = _decapitalize_marketing_words(
        str(offer.get("reward_label") or details.get("reward_label") or "").strip(),
        brand=str(offer.get("brand") or ""),
    )
    if not reward_label:
        reward_label = "bonus bets"
    if reward_amount:
        return f"{reward_amount} in {reward_label}"
    return reward_label


def _offer_qualifying_amount_text(offer: dict[str, Any]) -> str:
    """Return the qualifying dollar amount when it is available from structured data or offer text."""
    offer = offer or {}
    details = extract_offer_amount_details(str(offer.get("offer_text") or offer.get("affiliate_offer") or ""))
    raw = offer.get("qualifying_amount") or details.get("qualifying_amount") or ""
    return str(raw).strip()


def _offer_mechanic_type(offer: dict[str, Any]) -> str:
    """Classify offer mechanics for compliant examples."""
    text = " ".join(
        str(offer.get(key) or "")
        for key in ("offer_text", "affiliate_offer", "terms", "name", "title")
    ).lower()
    if re.search(r"\b(first bet|bet)\s+(?:loses|loss)\b", text) or any(
        phrase in text for phrase in ("money back", "refund", "safety net", "no sweat")
    ):
        return "money_back"
    if re.search(r"\bbet\s*\$?\d", text) and re.search(r"\bget\s*\$?\d", text):
        return "bet_and_get"
    if "win or lose" in text:
        return "bet_and_get"
    if "deposit" in text and re.search(r"\bmatch(?:ed)?\b", text):
        return "deposit_match"
    return "generic"


def _offer_bonus_timing_sentence(offer: dict[str, Any], *, bet_amount: float, reward_phrase: str) -> str:
    """Explain reward timing without implying a bonus is the same as wager payout."""
    mechanic = _offer_mechanic_type(offer)
    reward_amount = _parse_money_value(offer.get("bonus_amount") or offer.get("reward_amount") or reward_phrase)
    cap_phrase = f" up to {reward_phrase}" if reward_amount else ""
    if mechanic == "money_back":
        return (
            f"If the first bet loses, the bonus is matched to that losing stake{cap_phrase} after settlement. "
            "It is not a guaranteed payout from the wager itself."
        )
    if mechanic == "bet_and_get":
        return (
            f"Once you place the eligible first wager, the offer credits {reward_phrase} under the listed terms. "
            "That bonus is separate from the result of the wager."
        )
    if mechanic == "deposit_match":
        return (
            f"The bonus is tied to the eligible deposit and offer terms, not to a guaranteed result from this wager."
        )
    return (
        f"After the qualifying wager settles, the offer credits {reward_phrase} under the listed terms. "
        "The bonus is separate from the wager payout."
    )


def _event_schedule_text(event_context: str) -> str:
    """Build a concise schedule phrase like 'tips Tuesday at 8:30 PM ET on NBC'."""
    if not event_context:
        return ""

    game_time = ""
    network = ""
    game_time_match = re.search(r"Game time:\s*([^\.]+)", event_context, flags=re.IGNORECASE)
    if game_time_match:
        game_time = re.sub(r"\s+", " ", game_time_match.group(1).strip())
    network_match = re.search(r"Network:\s*([^\.]+)", event_context, flags=re.IGNORECASE)
    if network_match:
        network = re.sub(r"\s+", " ", network_match.group(1).strip())

    if game_time and network:
        return f"tips {game_time} on {network}"
    if game_time:
        return f"is set for {game_time}"
    if network:
        return f"is on {network}"
    return ""


def _choose_variant(variation_key: str, slot: str, options: list[str], *context: str) -> str:
    """Pick a stable variant for this generation run and section slot."""
    if not options:
        return ""
    if not variation_key:
        return options[0]
    seed = "|".join([variation_key, slot, *[str(value or "") for value in context]])
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()
    return options[int(digest[:8], 16) % len(options)]


def _content_mode_label(*, prediction_market: bool = False, dfs_mode: bool = False) -> str:
    if prediction_market:
        return "prediction_market"
    if dfs_mode:
        return "dfs"
    return "sportsbook"


def _variation_brief(
    variation_key: str,
    *,
    section_kind: str,
    prediction_market: bool = False,
    dfs_mode: bool = False,
) -> str:
    """Return a compact style brief that changes between generations without changing facts."""
    mode = _content_mode_label(prediction_market=prediction_market, dfs_mode=dfs_mode)
    opener_angles = {
        "sportsbook": [
            "lead with timing and immediate offer value",
            "lead with the event window and what the offer unlocks",
            "lead with a direct value statement tied to the game",
        ],
        "dfs": [
            "lead with slate utility and extra entry volume",
            "lead with the featured game and why extra entries matter",
            "lead with the small first-step / larger bonus contrast",
        ],
        "prediction_market": [
            "lead with market flexibility after the first qualifying action",
            "lead with the event angle and what the promo credits unlock",
            "lead with the small first action / larger reward contrast",
        ],
    }
    sentence_shapes = [
        "mix one short sentence with one medium sentence in each paragraph",
        "prefer two medium sentences per paragraph with a direct final clause",
        "open each paragraph with a direct statement, then add one clarifying sentence",
    ]
    focus_map = {
        "intro": [
            "stress clarity over hype",
            "keep the second paragraph procedural and factual",
            "make the value proposition explicit in sentence one",
        ],
        "overview": [
            "focus on practical use cases instead of generic praise",
            "explain flexibility and user benefit, not hype",
            "frame the section around how the offer changes choices for the user",
        ],
        "claim": [
            "keep the math straightforward and explain outcomes plainly",
            "walk through the mechanics in plain English before the second scenario",
            "make the example feel concrete and user-facing, not abstract",
        ],
        "general": [
            "prefer plain editorial phrasing over marketing tone",
            "avoid repeating the H1 language verbatim",
            "keep the section useful and specific",
        ],
    }
    taboo = [
        "Do not use stock filler like 'front and center', 'angle is live', 'built for volume', or 'the value is simple'.",
        "Do not sound like a template. Vary sentence openings and avoid boilerplate transitions.",
    ]

    angle = _choose_variant(variation_key, f"{section_kind}_angle", opener_angles[mode], mode, section_kind)
    shape = _choose_variant(variation_key, f"{section_kind}_shape", sentence_shapes, mode, section_kind)
    focus = _choose_variant(variation_key, f"{section_kind}_focus", focus_map.get(section_kind, focus_map["general"]), mode, section_kind)
    taboo_line = _choose_variant(variation_key, f"{section_kind}_taboo", taboo, mode, section_kind)
    return "\n".join([
        f"- Variation angle: {angle}.",
        f"- Sentence movement: {shape}.",
        f"- Section focus: {focus}.",
        f"- {taboo_line}",
    ])


def _dfs_intro_hook_text(event_context: str, article_date: str = "") -> str:
    """Return a concise DFS intro hook tied to the selected event when available."""
    event_label = _extract_featured_label_from_event_context(event_context)
    schedule = _event_schedule_text(event_context)
    date_prefix = f"As of {article_date}, " if article_date else ""
    if event_label and schedule:
        return f"{date_prefix}{event_label} {schedule}"
    if event_label:
        return f"{date_prefix}{event_label} is the featured DFS spot"
    if article_date:
        return f"As of {article_date}, this DFS offer is live"
    return "This DFS offer is live"


def _prediction_market_intro_hook_text(event_context: str, article_date: str = "") -> str:
    """Return a concise prediction-market intro hook tied to the selected event when available."""
    event_label = _extract_featured_label_from_event_context(event_context)
    schedule = _event_schedule_text(event_context)
    date_prefix = f"As of {article_date}, " if article_date else ""
    if event_label and schedule:
        return f"{date_prefix}{event_label} {schedule}"
    if event_label:
        return f"{date_prefix}{event_label} is the featured market angle"
    if article_date:
        return f"As of {article_date}, this prediction-market offer is live"
    return "This prediction-market offer is live"


def _default_selection_for_event(event_label: str) -> str:
    """Build a generic but event-anchored selection for fallback examples."""
    clean = re.sub(r"\s+", " ", str(event_label or "").strip())
    if not clean:
        return "the featured market"

    matchup_parts = re.split(r"\s+(?:vs\.?|@)\s+", clean, maxsplit=1, flags=re.IGNORECASE)
    if len(matchup_parts) == 2 and matchup_parts[0].strip():
        return f"{matchup_parts[0].strip()} moneyline"

    label_lower = clean.lower()
    if any(token in label_lower for token in ("ufc", "mma", "boxing", "wbc", "fight night")):
        return "a main-card moneyline"
    if any(token in label_lower for token in ("nascar", "indycar", "formula 1", "f1")):
        return "a head-to-head matchup"
    if any(token in label_lower for token in ("golf", "masters", "pga", "open championship", "u.s. open", "ryder cup")):
        return "a round matchup"
    return f"a featured market tied to {clean}"


def _build_fallback_bet_example_data(
    offer: dict[str, Any],
    event_context: str,
) -> dict[str, Any] | None:
    """Construct a deterministic sportsbook example when the UI did not provide one."""
    event_label = _extract_featured_label_from_event_context(event_context)
    if not event_label:
        return None

    label_lower = event_label.lower()
    odds = 120 if any(token in label_lower for token in ("ufc", "mma", "boxing", "wbc", "fight night")) else -110
    qualifying_amount = _parse_money_value(_offer_qualifying_amount_text(offer)) or 50.0
    return {
        "bet_amount": qualifying_amount,
        "selection": _default_selection_for_event(event_label),
        "odds": odds,
        "sportsbook_used": str(offer.get("brand") or "").strip().lower(),
        "event_context": event_label,
    }


def _build_fallback_dfs_example_data(
    offer: dict[str, Any],
    event_context: str,
) -> dict[str, Any] | None:
    """Construct a deterministic DFS example keyed to the qualifying entry amount."""
    event_label = _extract_featured_label_from_event_context(event_context)
    if not event_label:
        return None

    qualifying_amount = _parse_money_value(
        offer.get("qualifying_amount") or extract_offer_amount_details(str(offer.get("offer_text") or "")).get("qualifying_amount")
    ) or 5.0
    reward_amount = _parse_money_value(offer.get("bonus_amount") or offer.get("reward_amount")) or 50.0
    matchup = _extract_matchup_from_event_context_text(event_context) or event_label
    return {
        "entry_amount": qualifying_amount,
        "payout_multiplier": 2.0,
        "selection": f"a pick'em contest on {matchup}",
        "event_context": event_label,
        "reward_amount": reward_amount,
    }


def _render_dfs_intro_deterministic(
    *,
    keyword: str,
    offer: dict[str, Any],
    state: str,
    event_context: str = "",
    article_date: str = "",
    variation_key: str = "",
) -> str:
    """Render a deterministic DFS intro with exact operator facts and minimal model variance."""
    brand = str(offer.get("brand") or "the DFS app").strip()
    bonus_code = str(offer.get("bonus_code") or "").strip()
    reward_phrase = _offer_reward_phrase(offer).replace("bonus bets", "bonus entries")
    qualifying_amount = _offer_qualifying_amount_text(offer) or "$5"
    states_text = _offer_states_text(offer, state, dfs_mode=True)
    excluded_states_text = _offer_excluded_states_text(offer, current_state=state, dfs_mode=True)
    age_summary = _operator_age_summary(offer, dfs_mode=True)
    availability_clause = (
        f"It's available in {states_text}."
        + (f" It isn't offered in {excluded_states_text}." if excluded_states_text else "")
        + (f" {age_summary}." if age_summary else "")
    )
    hook = _dfs_intro_hook_text(event_context, article_date=article_date)

    code_fragment = f" {bonus_code}" if bonus_code else ""
    first_para_templates = [
        f"<p>{hook}, and the {keyword}{code_fragment} gives DFS players {reward_phrase} after a {qualifying_amount} play. That gives you more contest volume without changing the size of your first entry.</p>",
        f"<p>{hook}, and the {keyword}{code_fragment} is built around a small first play and a bigger DFS reward. After {qualifying_amount} goes through, you have {reward_phrase} to work into the rest of the slate.</p>",
        f"<p>{hook}, and the {keyword}{code_fragment} keeps the first step light: one {qualifying_amount} play unlocks {reward_phrase}. That is the cleanest way to add more entries without forcing a bigger starting commitment.</p>",
    ]

    second_para_templates = []
    if bonus_code:
        second_para_templates.extend([
            f"<p>Enter {bonus_code} at signup, make your first {qualifying_amount} play, and the {brand} fantasy app adds the reward as contest credit for more entries. {availability_clause}</p>",
            f"<p>Use {bonus_code} during registration, then let the first {qualifying_amount} play qualify the offer. After that, the {brand} fantasy app posts the reward as entry credit for more contests. {availability_clause}</p>",
            f"<p>Once {bonus_code} is attached to the account, the first {qualifying_amount} play does the rest and the {brand} fantasy app adds the reward as contest credit. {availability_clause}</p>",
        ])
    else:
        second_para_templates.extend([
            f"<p>Make your first {qualifying_amount} play and the {brand} fantasy app adds the reward as contest credit for more entries. {availability_clause}</p>",
            f"<p>After the first {qualifying_amount} play qualifies, the {brand} fantasy app posts the reward as entry credit for more contests. {availability_clause}</p>",
            f"<p>The first {qualifying_amount} play unlocks the reward inside the {brand} fantasy app as contest credit for more entries. {availability_clause}</p>",
        ])

    first_para = _choose_variant(variation_key, "dfs_intro_p1", first_para_templates, keyword, brand, hook)
    second_para = _choose_variant(variation_key, "dfs_intro_p2", second_para_templates, keyword, brand, states_text)
    return _normalize_visible_punctuation(first_para + second_para)


def _is_dfs_overview_heading(title_lower: str) -> bool:
    """Return True for DFS overview/value sections that should use deterministic copy."""
    if not title_lower:
        return False
    return any(
        phrase in title_lower
        for phrase in (
            " fits ",
            "details",
            "best dfs angle",
            "worth a look",
            "where ",
            "why ",
        )
    )


def _render_dfs_overview_section_deterministic(
    *,
    section_title: str,
    keyword: str,
    offer: dict[str, Any],
    event_context: str = "",
    variation_key: str = "",
) -> str:
    """Render a deterministic DFS overview section with less model-shaped prose."""
    event_label = _extract_featured_label_from_event_context(event_context) or "this slate"
    schedule = _event_schedule_text(event_context)
    reward_phrase = _offer_reward_phrase(offer).replace("bonus bets", "bonus entries")
    qualifying_amount = _offer_qualifying_amount_text(offer) or "$5"
    bonus_code = str(offer.get("bonus_code") or "").strip()
    code_fragment = f" with code {bonus_code}" if bonus_code else ""
    schedule_fragment = f" ({schedule})" if schedule else ""

    first_para_options = [
        f"<p>For {event_label}{schedule_fragment}, the {keyword} is useful because it turns one {qualifying_amount} play into {reward_phrase} for the rest of the slate. That gives you room to test different contest builds and player combinations instead of forcing everything onto one card.</p>",
        f"<p>On {event_label}{schedule_fragment}, the {keyword} works best as a volume play. Start with {qualifying_amount}, then use {reward_phrase} to spread across more entries and more lineup paths than you could justify with cash alone.</p>",
        f"<p>{event_label}{schedule_fragment} is the kind of slate where the {keyword} helps most. One {qualifying_amount} play unlocks {reward_phrase}, which makes it easier to cover different player combinations without loading everything into one build.</p>",
    ]
    second_para_options = [
        f"<p>Think of {reward_phrase} as site credit for DFS contests, not cash you can withdraw. That makes the offer best for rotating through multiple entries, reacting to late lineup news, and keeping your out-of-pocket risk low on the first play{code_fragment}.</p>",
        f"<p>{reward_phrase} is best used as extra entry volume, not as something to force into one oversized contest. That gives you flexibility to adjust to late news, shift to a different contest type, and keep the first out-of-pocket step small{code_fragment}.</p>",
        f"<p>Because {reward_phrase} works as contest credit, the real edge is flexibility. You can test a few lineup structures, move between contest sizes, and keep your cash exposure limited on the first play{code_fragment}.</p>",
    ]
    first_para = _choose_variant(variation_key, "dfs_overview_p1", first_para_options, keyword, event_label, section_title)
    second_para = _choose_variant(variation_key, "dfs_overview_p2", second_para_options, keyword, event_label, section_title)
    return _normalize_visible_punctuation(first_para + second_para)


def _render_prediction_market_intro_deterministic(
    *,
    keyword: str,
    offer: dict[str, Any],
    state: str,
    event_context: str = "",
    article_date: str = "",
    variation_key: str = "",
) -> str:
    """Render a deterministic prediction-market intro with exact operator facts."""
    brand = str(offer.get("brand") or "the operator").strip()
    bonus_code = str(offer.get("bonus_code") or "").strip()
    reward_phrase = _offer_reward_phrase_visible(offer).replace("bonus bets", "promo credits")
    qualifying_amount = _offer_qualifying_amount_text(offer) or "$25"
    states_text = _offer_states_text(offer, state, prediction_market=True)
    excluded_states_text = _offer_excluded_states_text(offer, current_state=state, prediction_market=True)
    age_summary = _operator_age_summary(offer, prediction_market=True)
    availability_clause = (
        f"It's available in {states_text}."
        + (f" It isn't offered in {excluded_states_text}." if excluded_states_text else "")
        + (f" {age_summary}." if age_summary else "")
    )
    hook = _prediction_market_intro_hook_text(event_context, article_date=article_date)

    code_fragment = f" {bonus_code}" if bonus_code else ""
    first_para_templates = [
        f"<p>{hook}, and the {keyword}{code_fragment} adds {reward_phrase} after a {qualifying_amount} qualifying action. That gives you more room to open positions without changing the size of the first move.</p>",
        f"<p>{hook}, and the {keyword}{code_fragment} is built around a smaller first action and more market flexibility after that. Once the {qualifying_amount} qualifier is done, you have {reward_phrase} to spread across later positions.</p>",
        f"<p>{hook}, and the {keyword}{code_fragment} keeps the first action manageable: one {qualifying_amount} qualifier unlocks {reward_phrase}. That makes it easier to test more than one market angle without pressing on the opening move.</p>",
    ]
    second_para_templates = []
    if bonus_code:
        second_para_templates.extend([
            f"<p>Enter {bonus_code} at signup, complete the first {qualifying_amount} qualifying action, and the {brand} app adds the reward as promotional credit for more market positions. {availability_clause}</p>",
            f"<p>Use {bonus_code} during registration, then let the first {qualifying_amount} qualifying action unlock the offer. After that, the {brand} app posts the reward as promotional credit for more positions. {availability_clause}</p>",
            f"<p>Once {bonus_code} is attached to the account, the first {qualifying_amount} qualifying action does the rest and the {brand} app adds the reward as promotional credit. {availability_clause}</p>",
        ])
    else:
        second_para_templates.extend([
            f"<p>Complete the first {qualifying_amount} qualifying action and the {brand} app adds the reward as promotional credit for more market positions. {availability_clause}</p>",
            f"<p>After the first {qualifying_amount} qualifying action, the {brand} app posts the reward as promotional credit for more market positions. {availability_clause}</p>",
            f"<p>The first {qualifying_amount} qualifying action unlocks the reward inside the {brand} app as promotional credit for later positions. {availability_clause}</p>",
        ])
    first_para = _choose_variant(variation_key, "pm_intro_p1", first_para_templates, keyword, brand, hook)
    second_para = _choose_variant(variation_key, "pm_intro_p2", second_para_templates, keyword, brand, states_text)
    return _normalize_visible_punctuation(first_para + second_para)


def _is_prediction_market_overview_heading(title_lower: str) -> bool:
    """Return True for prediction-market overview/value sections that should use deterministic copy."""
    if not title_lower:
        return False
    return any(
        phrase in title_lower
        for phrase in (
            " fits ",
            "market angle",
            "stands out",
            "worth a look",
            "where ",
            "why ",
        )
    )


def _render_prediction_market_overview_section_deterministic(
    *,
    section_title: str,
    keyword: str,
    offer: dict[str, Any],
    event_context: str = "",
    variation_key: str = "",
) -> str:
    """Render a deterministic prediction-market overview section."""
    event_label = _extract_featured_label_from_event_context(event_context) or "this market"
    schedule = _event_schedule_text(event_context)
    reward_phrase = _offer_reward_phrase_visible(offer).replace("bonus bets", "promo credits")
    qualifying_amount = _offer_qualifying_amount_text(offer) or "$25"
    bonus_code = str(offer.get("bonus_code") or "").strip()
    code_fragment = f" with code {bonus_code}" if bonus_code else ""
    schedule_fragment = f" ({schedule})" if schedule else ""

    first_para_options = [
        f"<p>For {event_label}{schedule_fragment}, the {keyword} is useful because it turns one {qualifying_amount} qualifying action into {reward_phrase} for later positions. That gives you more flexibility to spread exposure across different outcomes instead of forcing one all-in market view.</p>",
        f"<p>On {event_label}{schedule_fragment}, the {keyword} works best as a flexibility play. Start with the {qualifying_amount} qualifier, then use {reward_phrase} to test more than one market angle without loading everything into the first position.</p>",
        f"<p>{event_label}{schedule_fragment} is the kind of market where the {keyword} helps most. One {qualifying_amount} qualifying action unlocks {reward_phrase}, which makes it easier to scale into several positions instead of taking one oversized view.</p>",
    ]
    second_para_options = [
        f"<p>Think of {reward_phrase} as promotional credit for market positions, not guaranteed cash. That makes the offer best for testing multiple trade ideas, reacting to price moves, and keeping your first out-of-pocket action smaller{code_fragment}.</p>",
        f"<p>{reward_phrase} works best when you spread it across multiple trade ideas instead of forcing one big position. That gives you room to react to line movement, hedge exposure, and keep the first action smaller{code_fragment}.</p>",
        f"<p>Because {reward_phrase} comes through as promotional credit, the real edge is flexibility. You can rotate into a second market view, size positions more carefully, and avoid pressing the opening move too hard{code_fragment}.</p>",
    ]
    first_para = _choose_variant(variation_key, "pm_overview_p1", first_para_options, keyword, event_label, section_title)
    second_para = _choose_variant(variation_key, "pm_overview_p2", second_para_options, keyword, event_label, section_title)
    return _normalize_visible_punctuation(first_para + second_para)


def _build_fallback_prediction_market_example_data(
    offer: dict[str, Any],
    event_context: str,
) -> dict[str, Any] | None:
    """Construct a deterministic prediction-market example keyed to the qualifying amount."""
    event_label = _extract_featured_label_from_event_context(event_context)
    if not event_label:
        return None

    qualifying_amount = _parse_money_value(
        offer.get("qualifying_amount") or extract_offer_amount_details(str(offer.get("offer_text") or "")).get("qualifying_amount")
    ) or 25.0
    reward_amount = _parse_money_value(offer.get("bonus_amount") or offer.get("reward_amount")) or 50.0
    return {
        "qualifying_amount": qualifying_amount,
        "position_amount": 10.0,
        "entry_price": 0.50,
        "settlement_price": 1.0,
        "selection": event_label,
        "event_context": event_label,
        "reward_amount": reward_amount,
    }


def _render_prediction_market_example_section_deterministic(
    *,
    offer: dict[str, Any],
    bet_example_data: dict[str, Any] | None,
    event_context: str = "",
    variation_key: str = "",
) -> str | None:
    """Render a deterministic prediction-market worked example from structured offer facts."""
    data = dict(bet_example_data or {})
    if not data:
        fallback_data = _build_fallback_prediction_market_example_data(offer, event_context)
        if fallback_data:
            data = fallback_data
    if not data:
        return None

    qualifying_amount = _parse_money_value(data.get("qualifying_amount"))
    if qualifying_amount is None:
        qualifying_amount = _parse_money_value(_offer_qualifying_amount_text(offer))
    if qualifying_amount is None:
        qualifying_amount = 25.0
    position_amount = _parse_money_value(data.get("position_amount") or data.get("bet_amount")) or 10.0
    entry_price = float(data.get("entry_price") or 0.50)
    settlement_price = float(data.get("settlement_price") or 1.0)
    selection = str(data.get("selection") or "").strip()
    if not selection:
        return None
    prediction_market_data = data.get("prediction_market") if isinstance(data.get("prediction_market"), dict) else {}
    market_title = str(data.get("market_title") or prediction_market_data.get("market_title") or "").strip()
    selection_phrase = selection
    if market_title and selection.lower() not in market_title.lower():
        selection_phrase = f"the {selection} side of {market_title}"
    elif market_title:
        selection_phrase = market_title

    contracts = position_amount / entry_price if entry_price > 0 else 0.0
    gross_payout = contracts * settlement_price
    profit = gross_payout - position_amount
    reward_phrase = _offer_reward_phrase_visible(offer).replace("bonus bets", "promo credits")
    bonus_code = str(offer.get("bonus_code") or "").strip()
    code_sentence = f" after entering <strong>{bonus_code}</strong> at signup" if bonus_code else ""

    first_para_options = [
        f"<p>I complete the ${qualifying_amount:.0f} qualifying action{code_sentence}. Then I open a separate ${position_amount:.0f} position on {selection_phrase}. At ${entry_price:.2f} per contract, that buys about {contracts:.0f} contracts. A ${settlement_price:.2f} settlement pays about ${gross_payout:.2f}, or roughly ${profit:.2f} in profit.</p>",
        f"<p>The ${qualifying_amount:.0f} qualifying action comes first{code_sentence}. Then I use a separate ${position_amount:.0f} position on {selection_phrase}. At ${entry_price:.2f} per contract, that buys about {contracts:.0f} contracts. A close at ${settlement_price:.2f} returns about ${gross_payout:.2f}, or roughly ${profit:.2f} in profit.</p>",
        f"<p>After the ${qualifying_amount:.0f} qualifying action{code_sentence}, I put ${position_amount:.0f} behind {selection_phrase}. At ${entry_price:.2f} per contract, that buys roughly {contracts:.0f} contracts. A ${settlement_price:.2f} settlement pays about ${gross_payout:.2f}, which means about ${profit:.2f} in profit.</p>",
    ]
    second_para_options = [
        f"<p>The opposite settlement costs the ${position_amount:.0f} position amount, but the {reward_phrase} from the offer remains. That makes the reward better for several smaller positions instead of one large market view.</p>",
        f"<p>A market move the other way puts the ${position_amount:.0f} position at risk, but the {reward_phrase} from the offer remains. Use that reward as extra flexibility across several positions, not as fuel for one oversized trade.</p>",
        f"<p>An unfavorable settlement still risks the ${position_amount:.0f} amount in that market while the {reward_phrase} remains available. The cleaner use is several smaller follow-up positions instead of one doubled-down view.</p>",
    ]
    first_para = _choose_variant(variation_key, "pm_claim_p1", first_para_options, selection_phrase, reward_phrase)
    second_para = _choose_variant(variation_key, "pm_claim_p2", second_para_options, selection_phrase, reward_phrase)
    return first_para + second_para


def _render_bet_example_section_deterministic(
    *,
    offer: dict[str, Any],
    bet_example_data: dict[str, Any] | None,
    event_context: str = "",
    variation_key: str = "",
) -> str | None:
    """Render a sportsbook worked-example section from structured UI selections."""
    data = dict(bet_example_data or {})
    if not data:
        fallback_data = _build_fallback_bet_example_data(offer, event_context)
        if fallback_data:
            data = fallback_data
    if not data:
        return None

    try:
        bet_amount = float(data.get("bet_amount"))
        odds = int(data.get("odds"))
    except (TypeError, ValueError):
        return None

    selection = str(data.get("selection") or "").strip()
    if not selection:
        return None

    profit_raw = data.get("potential_profit")
    try:
        profit = float(profit_raw)
    except (TypeError, ValueError):
        if odds > 0:
            profit = (bet_amount * odds) / 100.0
        else:
            profit = (bet_amount * 100.0) / abs(odds) if odds != 0 else 0.0
    total_return = bet_amount + profit

    book = str(data.get("sportsbook_used") or data.get("sportsbook_requested") or "").strip()
    book_label = _sportsbook_display_name(book)
    event_label = (
        _extract_featured_label_from_event_context(event_context)
        or str(data.get("event_context") or "").strip()
    )
    event_clause = ""
    if event_label and event_label.lower() not in selection.lower():
        event_clause = f" for {event_label}"
    odds_display = f"{odds:+d}"

    bonus_code = str(offer.get("bonus_code") or "").strip()
    reward_phrase = _offer_reward_phrase(offer)
    timing_sentence = _offer_bonus_timing_sentence(
        offer,
        bet_amount=bet_amount,
        reward_phrase=reward_phrase,
    )
    code_clause = f" after entering <strong>{bonus_code}</strong>" if bonus_code else ""

    first_para_options = [
        f"<p>Here's how it works with {book_label}: place the qualifying ${bet_amount:.0f} bet on {selection} at {odds_display}{event_clause}{code_clause}. "
        f"If it wins, the ticket pays about ${profit:.2f} in profit on top of the returned stake. If it loses, the ${bet_amount:.0f} stake is gone.</p>",
        f"<p>The play is simple at {book_label}: put the qualifying ${bet_amount:.0f} on {selection} at {odds_display}{event_clause}{code_clause}. "
        f"A winner adds about ${profit:.2f} in profit; a loser costs the ${bet_amount:.0f} stake.</p>",
        f"<p>Start with the qualifying ${bet_amount:.0f} bet on {selection} at {odds_display} with {book_label}{event_clause}{code_clause}. "
        f"Win, and it pays like any normal bet — about ${profit:.2f} in profit. Lose, and you are out the ${bet_amount:.0f} stake.</p>",
    ]
    first_para = _choose_variant(variation_key, "sb_claim_p1", first_para_options, selection, book_label)
    return (
        first_para
        + f"<p>{timing_sentence} Use any bonus bets on later eligible markets, and remember that bonus-bet stakes usually do not return with winnings.</p>"
    )


def _render_dfs_example_section_deterministic(
    *,
    offer: dict[str, Any],
    bet_example_data: dict[str, Any] | None,
    event_context: str = "",
    variation_key: str = "",
) -> str | None:
    """Render a DFS worked-example section from structured offer facts."""
    data = dict(bet_example_data or {})
    if not data:
        fallback_data = _build_fallback_dfs_example_data(offer, event_context)
        if fallback_data:
            data = fallback_data
    if not data:
        return None

    entry_amount = _parse_money_value(data.get("entry_amount") or data.get("bet_amount")) or 0.0
    if entry_amount <= 0:
        return None
    payout_multiplier = float(data.get("payout_multiplier") or 2.0)
    total_return = entry_amount * payout_multiplier
    event_label = _extract_featured_label_from_event_context(event_context) or str(data.get("event_context") or "").strip()
    selection = str(data.get("selection") or "").strip() or f"a pick'em contest on {event_label or 'the featured slate'}"
    bonus_code = str(offer.get("bonus_code") or "").strip()
    reward_phrase = _offer_reward_phrase(offer).replace("bonus bets", "bonus entries")
    reward_amount = _parse_money_value(data.get("reward_amount")) or _parse_money_value(offer.get("bonus_amount") or offer.get("reward_amount")) or 50.0
    split_amount = max(5.0, reward_amount / 5.0)
    code_sentence = f" and enter <strong>{bonus_code}</strong> at signup," if bonus_code else ""
    event_clause = ""
    if event_label and event_label.lower() not in selection.lower():
        event_clause = f" for {event_label}"

    first_para_options = [
        f"<p>I use a ${entry_amount:.0f} DFS entry on {selection}{event_clause}{code_sentence}. That first play qualifies the offer. A {payout_multiplier:.0f}x card returns ${total_return:.0f} total, including the original entry fee. A miss costs only the ${entry_amount:.0f} entry fee, and the app still credits {reward_phrase} once the qualifying play posts.</p>",
        f"<p>Say I start with a ${entry_amount:.0f} DFS entry on {selection}{event_clause}{code_sentence}. That opening entry triggers the offer. A {payout_multiplier:.0f}x result returns ${total_return:.0f} total, including the entry fee. A losing card keeps the loss at ${entry_amount:.0f}, then the app posts {reward_phrase} after settlement.</p>",
        f"<p>A ${entry_amount:.0f} DFS entry on {selection}{event_clause} triggers the offer{code_sentence}. A {payout_multiplier:.0f}x result turns it into ${total_return:.0f} back in total. A miss leaves me down ${entry_amount:.0f} on the entry, while the app still credits {reward_phrase} after the qualifying play posts.</p>",
    ]
    second_para_options = [
        f"<p>From there, treat {reward_phrase} as contest-only entry credit. A clean next step is splitting it into five ${split_amount:.0f} entries across different builds, so one game script does not decide the whole slate.</p>",
        f"<p>From that point, {reward_phrase} is best used as extra entry volume, not as one oversized play. Splitting it into several ${split_amount:.0f} entries gives you room to rotate different builds and avoid tying the whole slate to one game script.</p>",
        f"<p>Once the reward lands, think of {reward_phrase} as extra lineup flexibility. Breaking it into multiple ${split_amount:.0f} entries lets you test more than one build and keeps the slate from hinging on a single card.</p>",
    ]
    first_para = _choose_variant(variation_key, "dfs_claim_p1", first_para_options, selection, reward_phrase)
    second_para = _choose_variant(variation_key, "dfs_claim_p2", second_para_options, selection, reward_phrase)
    return first_para + second_para


# ============================================================================
# STRUCTURED DRAFT GENERATION (Plan-Execute System)
# ============================================================================

async def generate_draft_from_outline(
    outline: list[dict],
    keyword: str,
    title: str,
    offer: dict[str, Any],
    alt_offers: list[dict[str, Any]] | None = None,
    state: str = "ALL",
    offer_property: str = "action_network",
    event_context: str = "",
    article_date: str = "",
    bet_example: str = "",
    bet_example_data: dict[str, Any] | None = None,
    output_format: str = "html",
    variation_key: str = "",
    article_preferences: dict[str, Any] | None = None,
    bc_core_context: dict[str, Any] | None = None,
) -> str:
    """Generate full article draft from structured outline (Execute stage).

    Uses the talking points from the Plan stage to generate focused,
    non-repetitive content for each section.

    Args:
        outline: Structured outline from generate_structured_outline
        keyword: Primary keyword
        title: Article H1 title
        offer: Offer dict from BAM API
        state: Target state
        event_context: Game/event context if applicable
        bet_example: Pre-built bet example text
        output_format: "html" or "markdown"

    Returns:
        Complete article in specified format
    """
    offer = offer or {}
    brand = offer.get("brand", "")
    offer_text = offer.get("offer_text", "")
    bonus_code = offer.get("bonus_code", "")
    terms = offer.get("terms", "")
    bonus_amount = offer.get("bonus_amount") or extract_bonus_amount(offer_text)
    expiration_days = offer.get("bonus_expiration_days") or extract_bonus_expiration_days(terms)
    switchboard_url = _offer_switchboard_url(offer, state=state, property_key=offer_property)

    # Multi-offer support
    all_offers = [offer] + (alt_offers or []) if offer else (alt_offers or [])
    content_mode = _get_content_mode(
        offer=offer,
        offers=all_offers,
        keyword=keyword,
        title=title,
    )
    is_prediction_market = content_mode == CONTENT_MODE_PREDICTION_MARKET
    is_dfs_mode = content_mode == CONTENT_MODE_DFS
    keyword = _normalize_brand_keyword_text(keyword, brand)
    variation_key = variation_key or uuid4().hex
    prefs = _normalize_article_preferences(article_preferences)
    preferred_links = _dedupe_link_specs_by_url(get_links_by_urls(prefs["preferred_internal_urls"], property_key=offer_property))
    preferred_urls = [str(link.url) for link in preferred_links if getattr(link, "url", None)]

    def select_offer_for_shortcode(level: str) -> dict[str, Any] | None:
        if not all_offers:
            return None
        idx = _shortcode_index(level)
        if idx < 0 or idx >= len(all_offers):
            return None
        return all_offers[idx]

    parts = []
    parts.append(f"<h1>{title}</h1>")
    previous_content = ""
    keyword_count = 0
    target_keyword_total = 9
    seen_headings: set[str] = set()

    for section in outline:
        level = section.get("level", "h2")
        section_title = _sanitize_heading_text(section.get("title", ""))
        talking_points = section.get("talking_points", [])
        avoid = section.get("avoid", [])

        if level == "intro":
            content = await _generate_intro_section(
                keyword=keyword,
                title=title,
                offer=offer,
                all_offers=all_offers,
                state=state,
                talking_points=talking_points,
                event_context=event_context,
                article_date=article_date,
                prediction_market=is_prediction_market,
                dfs_mode=is_dfs_mode,
                variation_key=variation_key,
                article_preferences=prefs,
                bc_core_context=bc_core_context,
            )
            parts.append(content)
            previous_content += content
            keyword_count += _count_keyword(content, keyword)

        elif level.startswith("shortcode"):
            current_offer = select_offer_for_shortcode(level)
            if current_offer:
                current_switchboard = _offer_switchboard_url(
                    current_offer,
                    state=state,
                    property_key=offer_property,
                ) or switchboard_url
                block = _render_html_offer_block(current_offer, current_switchboard, property_key=offer_property)
                parts.append(block)

        elif level in ("h2", "h3"):
            normalized = _normalize_heading(section_title)
            if normalized and normalized in seen_headings:
                continue
            if normalized:
                seen_headings.add(normalized)
            content = await _generate_body_section(
                section_title=section_title,
                level=level,
                keyword=keyword,
                offer=offer,
                all_offers=all_offers,
                state=state,
                offer_property=offer_property,
                talking_points=talking_points,
                avoid=avoid,
                previous_content=previous_content,
                current_keyword_count=keyword_count,
                target_keyword_total=target_keyword_total,
                event_context=event_context,
                bet_example=bet_example,
                bet_example_data=bet_example_data,
                prediction_market=is_prediction_market,
                dfs_mode=is_dfs_mode,
                variation_key=variation_key,
                article_preferences=prefs,
                preferred_links=preferred_links,
                bc_core_context=bc_core_context,
            )
            tag = "h2" if level == "h2" else "h3"
            parts.append(f"<{tag}>{section_title}</{tag}>")
            parts.append(content)
            previous_content += f"\n{section_title}:\n{content}"
            keyword_count += _count_keyword(content, keyword)

    # Join and inject switchboard links
    html_output = "\n".join(parts)
    html_output = _strip_placeholder_hash_links(html_output)
    html_output = _append_required_property_links(
        html_output,
        property_key=offer_property,
        prediction_market=is_prediction_market,
        dfs_mode=is_dfs_mode,
    )
    html_output = _apply_generation_quality_postprocess(html_output, keyword, prefs.get("market", "US"))
    primary_evergreen_link = get_operator_evergreen_link(property_key=offer_property, brand=brand)
    primary_evergreen_url = str(primary_evergreen_link.url) if primary_evergreen_link and primary_evergreen_link.url else ""
    if prefs.get("market") == "CA" and offer_property == "goal_com" and "goal.com/en-ca/" not in primary_evergreen_url.lower():
        primary_evergreen_url = ""
        preferred_urls = [url for url in preferred_urls if "goal.com/en-ca/" in url.lower()]
    primary_internal_url = preferred_urls[0] if preferred_urls else primary_evergreen_url
    if primary_internal_url:
        html_output = _ensure_first_paragraph_keyword_internal_link(html_output, keyword, primary_internal_url)

    # Ensure single disclaimer at the end
    disclaimer_state = "CANADA" if prefs.get("market") == "CA" and str(state or "").upper() == "ALL" else state
    disclaimer = get_disclaimer_for_state(disclaimer_state)
    if is_prediction_market:
        disclaimer = _adapt_disclaimer_for_prediction_market(disclaimer)
    elif is_dfs_mode:
        disclaimer = _adapt_disclaimer_for_dfs(disclaimer)
    html_output = _ensure_single_disclaimer(html_output, disclaimer)

    html_output = _inject_switchboard_links_for_offers(
        html_output,
        offers=all_offers,
        state=state,
        property_key=offer_property,
        max_links=1,
    )
    html_output = _strip_placeholder_hash_links(html_output)
    html_output = _strip_invalid_non_switchboard_links(html_output)
    html_output = _keep_selected_non_switchboard_links(
        html_output,
        preferred_urls,
        fallback_primary_url=primary_internal_url,
    )
    html_output = _align_selected_link_anchors(
        html_output,
        preferred_links,
        [keyword, *prefs["secondary_keywords"]],
    )
    html_output = await _humanize_article_html(
        html_output,
        keyword=keyword,
        offer=offer,
        content_mode=content_mode,
    )
    html_output = _ensure_keyword_in_first_paragraph(html_output, keyword)
    html_output = _apply_content_mode_language_guardrails(html_output, content_mode)
    html_output = _normalize_brand_keyword_text(html_output, brand)
    html_output = _target_keyword_mentions(html_output, keyword)
    html_output = _enforce_primary_keyword_density(html_output, keyword)
    html_output = _enforce_secondary_keyword_mentions(html_output, prefs["secondary_keywords"])
    html_output = _clean_orphaned_keyword_page_references(html_output, keyword)
    html_output = _unwrap_generic_offer_strong(html_output, brand)
    html_output = _strip_source_and_prompt_leaks(html_output)
    html_output = _strip_unprovided_article_date(html_output, article_date)
    html_output = _strip_market_mismatch_phrasing(html_output, prefs.get("market", "US"))
    html_output = _strip_formatting_from_headings(html_output)
    html_output = await _ensure_matchup_analysis_section(
        html_output,
        keyword=keyword,
        offer=offer,
        event_context=event_context,
        bc_core_context=bc_core_context,
        content_mode=content_mode,
        bet_example_data=bet_example_data,
    )
    html_output = await _ensure_editorial_body_length(
        html_output,
        keyword=keyword,
        offer=offer,
        event_context=event_context,
        bc_core_context=bc_core_context,
        content_mode=content_mode,
        bet_example_data=bet_example_data,
    )
    html_output = _cap_primary_keyword_density(html_output, keyword)
    html_output = _title_case_headings(html_output)
    html_output = _normalize_brand_casing(
        html_output,
        brand or (keyword.split()[0] if keyword.split() else ""),
    )

    if output_format == "markdown":
        # Convert back to markdown (basic)
        return _ensure_top_story_tracking_tag(_html_to_markdown(html_output))

    return _ensure_top_story_tracking_tag(html_output)


async def _generate_intro_section(
    keyword: str,
    title: str,
    offer: dict,
    all_offers: list[dict[str, Any]] | None,
    state: str,
    talking_points: list[str],
    event_context: str = "",
    article_date: str = "",
    prediction_market: bool = False,
    dfs_mode: bool = False,
    variation_key: str = "",
    article_preferences: dict[str, Any] | None = None,
    bc_core_context: dict[str, Any] | None = None,
) -> str:
    """Generate the intro/lede section.

    The intro should:
    1. Hook with a specific game/event if available (e.g., "Seahawks vs Patriots tonight at 6:30 PM ET on NBC")
    2. State the offer clearly with date
    3. Keep code mention light and use only one natural <strong> anchor when helpful
    4. State eligibility without turning into a legal dump
    """
    brand = offer.get("brand", "")
    offer_text = offer.get("offer_text", "")
    bonus_code = offer.get("bonus_code", "")
    terms = offer.get("terms", "")
    expiration_days = offer.get("bonus_expiration_days") or extract_bonus_expiration_days(terms)
    bonus_amount = offer.get("bonus_amount") or extract_bonus_amount(offer_text)
    offer_summary = _offer_value_summary(
        offer,
        prediction_market=prediction_market,
        dfs_mode=dfs_mode,
    )
    expiration_line = _offer_expiration_prompt_line(expiration_days)
    date_str = str(article_date or "").strip()
    date_clause = f" ahead of {date_str}" if date_str else ""
    date_instruction = (
        f"ARTICLE DATE (use this exact date if a date is mentioned): {date_str}"
        if date_str
        else "ARTICLE DATE: not provided. Do not mention today's date; use only event dates/times from Event Context."
    )
    style_guide = get_style_instructions()
    has_code = bool(bonus_code.strip())
    preferred_code_term = _preferred_code_term(brand)
    code_strong = f"<strong>{bonus_code}</strong>" if has_code else ""
    link_anchor = f"{brand} offer" if brand else "the offer"
    prompt_offers = [offer] if offer else []
    has_multiple_offers = len(prompt_offers) > 1
    multi_offer_context = _build_multi_offer_prompt_context(
        prompt_offers,
        state,
        prediction_market=prediction_market,
        dfs_mode=dfs_mode,
    )
    states_text = _offer_states_text(
        offer,
        state,
        prediction_market=prediction_market,
        dfs_mode=dfs_mode,
    )
    excluded_states_text = _offer_excluded_states_text(
        offer,
        current_state=state,
        prediction_market=prediction_market,
        dfs_mode=dfs_mode,
    )
    age_summary = _operator_age_summary(
        offer,
        prediction_market=prediction_market,
        dfs_mode=dfs_mode,
    )
    prefs = _normalize_article_preferences(article_preferences)
    is_canada_market = prefs.get("market") == "CA"
    availability_label = "province" if is_canada_market else "state"
    availability_context_label = "Eligible Provinces" if is_canada_market else "Eligible States"
    excluded_context_label = "Excluded Provinces" if is_canada_market else "Excluded States"

    # Format talking points for prompt
    points_md = "\n".join(f"- {p}" for p in talking_points) if talking_points else ""
    variation_md = _variation_brief(
        variation_key,
        section_kind="intro",
        prediction_market=prediction_market,
        dfs_mode=dfs_mode,
    )
    bc_core_points = (
        _select_bc_core_editorial_points(
            bc_core_context,
            section_kind="intro",
            max_points=2,
            prediction_market=prediction_market,
            dfs_mode=dfs_mode,
        )
        if bc_core_context
        else []
    )
    bc_core_required_count = 2 if len(bc_core_points) >= 2 else 1 if bc_core_points else 0

    system_prompt = (
        """You are a PUNCHY prediction-market writer for Action Network.
Write a 2-paragraph intro (lede) that sits between the H1 and H2.

TONE: Direct, confident, conversational.
- Use prediction-market language (market, position, contract, trade).
- Do NOT use sportsbook/betting/wager language.

Output clean HTML only - use <p>, <a>, <strong> tags. No markdown. No exclamation points."""
        if prediction_market
        else """You are a PUNCHY DFS writer for Action Network.
Write a 2-paragraph intro (lede) that sits between the H1 and H2.

TONE: Direct, confident, conversational.
- Use DFS language (entries, contests, picks, lineup, fantasy app).
- Do NOT use sportsbook/betting/wager language.

Output clean HTML only - use <p>, <a>, <strong> tags. No markdown. No exclamation points."""
        if dfs_mode
        else """You are a PUNCHY sports betting writer for Action Network.
Write a 2-paragraph intro (lede) that sits between the H1 and H2.

TONE: Direct, confident, conversational. Like you're telling a friend about a deal.
- Lead with the event or the offer value, not a canned template.
- Summarize the offer naturally instead of pasting the full raw promo headline.
- Do NOT use generic openers like "If you are looking for a valuable offer..."

Output clean HTML only - use <p>, <a>, <strong> tags. No markdown. No exclamation points."""
    )

    # Build the intro hook based on context
    game_hook = ""
    if event_context:
        game_hook = f"GAME HOOK (use this naturally, not as labels):\n{_naturalize_event_context(event_context)}\n\n"

    requirements = [
        "If there is a game hook, open sentence one with the matchup/time/network context and the offer value (do not reuse the same stock opener).",
        "If no game hook, start with a direct offer statement; avoid generic openers like \"If you are looking for a valuable offer...\"",
        f"Use explicit eligible {availability_label}s from source data. Do not say nationwide.",
        "When mentioning availability, write it as natural prose, e.g. 'The offer is available in AB, BC, ...'. Never use a 'Provinces Available:' or 'States Available:' label format." if is_canada_market else "When mentioning state eligibility, write it as natural prose, e.g. 'The offer is available in AZ, CO, ...'. Never use a 'States Available:' label format.",
        "Do not paste the full raw offer string more than once. Prefer a natural summary.",
        "When referencing the offer mid-sentence, use sentence casing ('$200 in bonus bets'), never the promo headline casing ('Bonus Bets Instantly').",
        "Quote each provided stat exactly and keep each stat in its own clause; never merge two different numbers into one figure.",
        "Do not mention 21+, minimum odds, or long legal disclaimers in the intro.",
        "If expiration is mentioned, it must describe the bonus/credit expiration, not the offer itself.",
        "Do NOT include responsible gaming disclaimers here (handled at the end of the article).",
    ]
    if is_canada_market:
        requirements.extend([
            "This is a Canada-market article. Never say U.S. residents, US users, US states, eligible states, or nationwide.",
            "Use legal-age users in listed Canadian provinces where permitted; do not assert 21+ unless the source explicitly says it.",
        ])
    if prediction_market:
        requirements.append(
            "Use prediction-market terms only (market, position, contract, trade). "
            "Do not use sportsbook, betting, wager, or bonus bets."
        )
    elif dfs_mode:
        requirements.append(
            "Use DFS terms only (entries, contests, picks, lineup, fantasy app). "
            "Do not use sportsbook, betting, wager, or bonus bets."
        )
        if age_summary:
            requirements.append(f"If age guidance is mentioned, use this exact summary: '{age_summary}'.")
        if excluded_states_text:
            requirements.append(f"If you mention exclusions, use this exact excluded-state list: {excluded_states_text}.")
    if has_multiple_offers:
        requirements.append("This article includes multiple offers: mention the main offer first, and weave in one other offer only if it fits naturally.")
    if has_code:
        generic_offer_label = f"{brand} offer" if brand else "the offer"
        requirements.extend([
            f"Use the {preferred_code_term} {bonus_code} naturally once or twice in plain text.",
            f"Use <strong> only for the promo code when emphasis is needed, e.g., {code_strong}.",
            "Do NOT wrap every mention in <strong>.",
            f"Do NOT bold generic offer labels like {generic_offer_label}.",
        ])
    else:
        requirements.append("Clearly state that no promo code is required. Do NOT invent a code. Do NOT wrap this in <strong>.")
    requirements.extend([
        "Keep sentences short and plain.",
        "Avoid legal or compliance language here.",
        "Do not use links in headings or heading-like text.",
        "NO exclamation points anywhere",
        "Do NOT invent numbers not listed above.",
        "Do not default to filler like 'see full terms' unless a missing detail must be acknowledged.",
        "The intro should feel fresh on each run: keep the facts fixed, but vary phrasing and sentence openings naturally.",
    ])
    if bc_core_points:
        requirements.append(
            f"Naturally work in at least {bc_core_required_count} concrete matchup/stat/trend note{'s' if bc_core_required_count != 1 else ''} from the internal context block below. Do not mention BC Core or call it a trend sample."
        )
    if prefs["enforce_active_voice"]:
        requirements.append("Use active voice. Avoid passive phrasing like 'is offered' or 'is highlighted' when a direct verb works.")
    requirements_md = "\n".join(f"- {r}" for r in requirements)
    secondary_keywords_md = "\n".join(f"- {phrase}" for phrase in prefs["secondary_keywords"]) if prefs["secondary_keywords"] else ""
    structure_notes_md = prefs["structure_notes"]

    if has_code:
        example_output = (
            f"<p>The {preferred_code_term if brand else 'offer'} is live for [Game] tonight at [time] on [network], and {brand} is highlighting {offer_summary}{date_clause}.</p>"
            + (
                f"<p>Sign up, enter {code_strong}, complete the qualifying action, and unlock the listed promotional credit.</p>"
                if prediction_market
                else f"<p>Sign up, enter {code_strong}, make your first qualifying DFS entry, and unlock the listed bonus entries or promo credits from the app.</p>"
                if dfs_mode
                else f"<p>Sign up, enter {code_strong}, complete the qualifying wager, and unlock the listed reward tied to the offer.</p>"
            )
        )
    else:
        example_output = (
            f"<p>The {brand} offer is live for [Game] tonight at [time] on [network], and {brand} is highlighting {offer_summary}{date_clause}.</p>"
            + (
                "<p>No promo code is required; complete the qualifying action described in the offer to unlock the listed promotional credit.</p>"
                if prediction_market
                else "<p>No promo code is required; complete the qualifying DFS entry described in the offer to unlock the listed bonus entries or promo credits.</p>"
                if dfs_mode
                else "<p>No promo code is required to claim it; just sign up and place your first bet.</p>"
            )
        )

    user_prompt = f"""Write the intro paragraph for this promo article:

{date_instruction}

{game_hook}OFFER DETAILS:
- Brand: {brand}
- Offer: {offer_text}
- Offer Summary: {offer_summary}
- Bonus Code: {bonus_code or "No code required"}
- Bonus Amount: {bonus_amount or "See offer"}
- {expiration_line[2:]}
- {availability_context_label}: {states_text}
{f"- {excluded_context_label}: {excluded_states_text}" if excluded_states_text else ""}
{f"- Age Summary: {age_summary}" if age_summary and not is_canada_market else ""}

{f"MULTI-OFFER SOURCE OF TRUTH (use correct brand/code pairings):{chr(10)}{multi_offer_context}{chr(10)}" if has_multiple_offers else ""}
{f"INTERNAL MATCHUP NOTES (use at least {bc_core_required_count} naturally if available, but never cite the source):{chr(10)}" + chr(10).join(f"- {point}" for point in bc_core_points) + chr(10) if bc_core_points else ""}

KEYWORD: {keyword}
{f"SECONDARY KEYWORDS (use these naturally across the article and aim for repeated coverage, not stuffing). Never place a secondary keyword in the same sentence as the primary keyword - especially when one contains the other:{chr(10)}{secondary_keywords_md}" if secondary_keywords_md else ""}

{points_md if points_md else ""}
{f"WRITER NOTES:{chr(10)}{structure_notes_md}{chr(10)}" if structure_notes_md else ""}

STYLE GUIDE (must follow):
{style_guide}

CRITICAL REQUIREMENTS:
{requirements_md}

VARIATION BRIEF:
{variation_md}

EXAMPLE OUTPUT (match this structure):
{example_output}

Write TWO <p> tags now (HTML only, no markdown):"""

    result = await generate_completion(
        prompt=user_prompt,
        system_prompt=system_prompt,
        temperature=get_temperature_by_section("intro"),
        max_tokens=500,
    )

    # Ensure it's wrapped in <p> if not
    result = result.strip()
    if not result.startswith("<p>"):
        result = f"<p>{result}</p>"

    result = _ensure_two_paragraphs(result, brand, offer_text, has_code, code_strong, states_text)
    result = _ensure_intro_state_specificity(result, states_text)
    result = _polish_intro_section_prose(result)
    result = _remove_irrelevant_excluded_state_mentions(result, state)
    result = _remove_irrelevant_single_state_exclusion_phrases(result, state)
    result = _resolve_intro_age_conflicts(result, age_summary)
    if bc_core_points and _bc_core_marker_coverage(result, bc_core_points) < bc_core_required_count:
        retry_prompt = (
            user_prompt
            + "\n\nMANDATORY CORRECTION:\n"
            + f"The intro must use at least {bc_core_required_count} concrete matchup/stat/trend detail"
            + ("s" if bc_core_required_count != 1 else "")
            + " from the internal matchup notes.\n"
            + "Do not mention BC Core or say 'trend sample'."
        )
        result = await generate_completion(
            prompt=retry_prompt,
            system_prompt=system_prompt,
            temperature=max(0.2, min(get_temperature_by_section("intro"), 0.5)),
            max_tokens=500,
        )
        result = result.strip()
        if not result.startswith("<p>"):
            result = f"<p>{result}</p>"
        result = _ensure_two_paragraphs(result, brand, offer_text, has_code, code_strong, states_text)
        result = _ensure_intro_state_specificity(result, states_text)
        result = _polish_intro_section_prose(result)
        result = _remove_irrelevant_excluded_state_mentions(result, state)
        result = _remove_irrelevant_single_state_exclusion_phrases(result, state)
        result = _resolve_intro_age_conflicts(result, age_summary)
        if _bc_core_marker_coverage(result, bc_core_points) < bc_core_required_count:
            result = _inject_bc_core_points_into_html(result, bc_core_points[:bc_core_required_count], max_injections=bc_core_required_count)
    return result


async def _generate_body_section(
    section_title: str,
    level: str,
    keyword: str,
    offer: dict,
    all_offers: list[dict[str, Any]] | None,
    state: str,
    offer_property: str,
    talking_points: list[str],
    avoid: list[str],
    previous_content: str,
    current_keyword_count: int = 0,
    target_keyword_total: int = 9,
    event_context: str = "",
    bet_example: str = "",
    bet_example_data: dict[str, Any] | None = None,
    prediction_market: bool = False,
    dfs_mode: bool = False,
    variation_key: str = "",
    article_preferences: dict[str, Any] | None = None,
    preferred_links: list[Any] | None = None,
    bc_core_context: dict[str, Any] | None = None,
) -> str:
    """Generate a body section (H2 or H3)."""
    primary_offer = offer or {}
    prompt_offers = [primary_offer] if primary_offer else []
    has_multiple_offers = len(prompt_offers) > 1

    brand = primary_offer.get("brand", "")
    offer_text = primary_offer.get("offer_text", "")
    bonus_code = primary_offer.get("bonus_code", "")
    terms = primary_offer.get("terms", "")
    expiration_days = primary_offer.get("bonus_expiration_days") or extract_bonus_expiration_days(terms)
    min_odds = primary_offer.get("minimum_odds") or extract_minimum_odds(terms)
    wagering = primary_offer.get("wagering_requirement") or extract_wagering_requirement(terms)
    expiration_line = _offer_expiration_prompt_line(expiration_days)
    offer_summary = _offer_value_summary(
        primary_offer,
        prediction_market=prediction_market,
        dfs_mode=dfs_mode,
    )
    multi_offer_context = _build_multi_offer_prompt_context(
        prompt_offers,
        state,
        prediction_market=prediction_market,
        dfs_mode=dfs_mode,
    )
    primary_states_text = _offer_states_text(
        primary_offer,
        state,
        prediction_market=prediction_market,
        dfs_mode=dfs_mode,
    )
    prefs = _normalize_article_preferences(article_preferences)
    is_canada_market = prefs.get("market") == "CA"
    availability_label = "provinces" if is_canada_market else "states"
    availability_context_label = "Eligible Provinces" if is_canada_market else "Eligible States"
    availability_format_example = (
        'If provinces are listed, write them as prose, e.g. "The offer is available in AB, BC, ..." - never as a "Provinces Available:" label.'
        if is_canada_market
        else 'If states are listed, write them as prose, e.g. "The offer is available in AZ, CO, ..." - never as a "States Available:" label.'
    )

    style_guide = get_style_instructions()
    rag_guidance = get_rag_usage_guidance()
    has_code = bool(bonus_code.strip())
    preferred_code_term = _preferred_code_term(brand)
    code_strong = f"<strong>{bonus_code}</strong>" if has_code else ""
    link_anchor = f"{brand} offer" if brand else "the offer"

    code_requirement = (
        f"Mention the {preferred_code_term} {bonus_code} at most once if it helps the section. "
        f"Use <strong> only for the promo code when emphasis is needed, e.g., {code_strong}. Do not bold generic offer labels like {link_anchor}."
        if has_code
        else f"State clearly that no promo code is required (do not invent a code). "
        f"Do not bold generic offer labels like {link_anchor}."
    )
    code_relevance = (
        f"Mention the {preferred_code_term} {bonus_code} once when relevant; use <strong> only for {code_strong}, not for {link_anchor}."
        if has_code
        else f"Note that no promo code is required when relevant, and do not bold {link_anchor}."
    )

    if has_multiple_offers:
        entity_label = "operators" if prediction_market else "DFS apps" if dfs_mode else "sportsbooks"
        entity_label_singular = "operator" if prediction_market else "DFS app" if dfs_mode else "sportsbook"
        code_requirement = (
            "When mentioning codes, use the correct brand/code pairing for each offer. "
            f"Do not mix codes across {entity_label}."
        )
        code_relevance = (
            f"If you reference multiple offers, keep each code tied to the correct {entity_label_singular}."
        )

    step_two = (
        f"Create account and enter {code_strong}"
        if has_code
        else "Create account (no promo code required)"
    )

    if prediction_market:
        claim_intro = (
            f'- "I open a $50 position on [Market] at [price] after signing up and entering {code_strong}."'
            if has_code
            else '- "I open a $50 position on [Market] at [price] after signing up with no promo code required."'
        )
    elif dfs_mode:
        claim_intro = (
            f"- \"I enter a $50 pick'em contest on [Game/Slate] after signing up and entering {code_strong}.\""
            if has_code
            else "- \"I enter a $50 pick'em contest on [Game/Slate] after signing up with no promo code required.\""
        )
    else:
        claim_intro = (
            f'- "I place a $50 moneyline bet on [Team] at [odds] after signing up and entering {code_strong}."'
            if has_code
            else '- "I place a $50 moneyline bet on [Team] at [odds] after signing up with no promo code required."'
        )

    try:
        snippets = await query_articles(f"{section_title} {keyword}", k=3, snippet_chars=400)
        style_examples = "\n\n".join([s.get("snippet", "") for s in snippets])[:1500]
    except Exception:
        style_examples = ""

    try:
        suggested_links = await suggest_links_for_section(
            section_title,
            [keyword, brand],
            k=3,
            property_key=offer_property,
            brand=brand,
        )
        links = _dedupe_link_specs_by_url([*(preferred_links or []), *suggested_links])
        links_md = format_links_markdown(
            links,
            brand=brand,
            prediction_market=prediction_market,
            dfs_mode=dfs_mode,
        )
    except Exception:
        links_md = "(no links available)"

    points_md = "\n".join(f"- {p}" for p in talking_points) if talking_points else ""
    avoid_md = "\n".join(f"- {a}" for a in avoid) if avoid else ""
    blacklisted_phrases = _extract_common_phrases(previous_content)
    blacklisted_md = "\n".join(f"- {p}" for p in blacklisted_phrases) if blacklisted_phrases else ""
    secondary_keywords_md = "\n".join(f"- {phrase}" for phrase in prefs["secondary_keywords"]) if prefs["secondary_keywords"] else ""
    structure_notes_md = prefs["structure_notes"]

    title_lower = section_title.lower()
    is_signup = _is_signup_heading(title_lower)
    is_how_to_claim = _is_claim_heading(title_lower, is_signup)
    is_numbered_list = is_signup
    is_overview = (
        any(x in title_lower for x in ["overview", "what is", "about"])
        or (dfs_mode and _is_dfs_overview_heading(title_lower))
        or (prediction_market and _is_prediction_market_overview_heading(title_lower))
    )
    is_eligibility = any(x in title_lower for x in ["eligibility", "key details", "requirements"])
    is_daily_promos = _is_daily_promos_heading(title_lower)
    is_terms = any(x in title_lower for x in ["terms", "conditions", "fine print", "house rules", "market rules", "settlement"])

    if not is_how_to_claim:
        bet_example = ""

    if is_daily_promos:
        return _render_daily_promos_placeholder(
            prompt_offers,
            state,
            prediction_market=prediction_market,
            dfs_mode=dfs_mode,
        )

    if is_terms:
        return _render_terms_section_html(
            offers=prompt_offers,
            terms=terms,
            expiration_days=expiration_days,
            min_odds=min_odds,
            wagering=wagering,
            state=state,
            prediction_market=prediction_market,
            dfs_mode=dfs_mode,
        )

    if is_numbered_list:
        signup_url = _offer_switchboard_url(primary_offer, state=state, property_key=offer_property)
        return _build_signup_list(
            brand,
            has_code,
            code_strong,
            state=state,
            event_context=event_context,
            signup_url=signup_url,
            qualifying_amount=_offer_qualifying_amount_text(primary_offer),
            minimum_odds=str(primary_offer.get("minimum_odds") or extract_minimum_odds(terms) or "").strip(),
            reward_phrase=_offer_reward_phrase_visible(primary_offer),
            prediction_market=prediction_market,
            dfs_mode=dfs_mode,
            variation_key=variation_key,
            market=prefs.get("market", "US"),
            offer_mechanic=_offer_mechanic_type(primary_offer),
        )

    section_kind = "claim" if is_how_to_claim else "overview" if is_overview else "general"
    variation_md = _variation_brief(
        variation_key,
        section_kind=section_kind,
        prediction_market=prediction_market,
        dfs_mode=dfs_mode,
    )
    bc_core_points = (
        _select_bc_core_editorial_points(
            bc_core_context,
            section_kind=section_kind,
            max_points=3,
            prediction_market=prediction_market,
            dfs_mode=dfs_mode,
        )
        if bc_core_context and not is_terms and not is_numbered_list and not is_daily_promos
        else []
    )
    bc_core_required_count = 2 if section_kind != "claim" and len(bc_core_points) >= 2 else 1 if bc_core_points else 0

    reference_mechanics = ""
    exact_claim_lines: list[str] = []
    if is_how_to_claim:
        deterministic_claim = None
        if prediction_market and (bet_example_data or event_context):
            deterministic_claim = _render_prediction_market_example_section_deterministic(
                offer=primary_offer,
                bet_example_data=bet_example_data,
                event_context=event_context,
                variation_key=variation_key,
            )
        elif dfs_mode and (bet_example_data or event_context):
            deterministic_claim = _render_dfs_example_section_deterministic(
                offer=primary_offer,
                bet_example_data=bet_example_data,
                event_context=event_context,
                variation_key=variation_key,
            )
        elif not prediction_market and not dfs_mode and (bet_example_data or event_context):
            deterministic_claim = _render_bet_example_section_deterministic(
                offer=primary_offer,
                bet_example_data=bet_example_data,
                event_context=event_context,
                variation_key=variation_key,
            )
        if deterministic_claim:
            if not prediction_market and not dfs_mode:
                return deterministic_claim
            reference_mechanics = _html_to_plain_text(deterministic_claim)
        exact_qualifying_amount = str(
            primary_offer.get("qualifying_amount")
            or extract_offer_amount_details(offer_text).get("qualifying_amount")
            or ""
        ).strip()
        if prediction_market and exact_qualifying_amount:
            exact_claim_lines.append(
                f"- Use this exact first qualifying action amount in the example: {exact_qualifying_amount}. Do not upscale it."
            )
        elif dfs_mode and exact_qualifying_amount:
            exact_claim_lines.append(
                f"- Use this exact first DFS entry amount in the example: {exact_qualifying_amount}. Do not upscale it."
            )
        elif bet_example_data:
            bet_amount = bet_example_data.get("bet_amount")
            selection = str(bet_example_data.get("selection") or "").strip()
            odds = bet_example_data.get("odds")
            if bet_amount:
                try:
                    exact_claim_lines.append(f"- Use this exact first-bet amount in the example: ${float(bet_amount):.0f}.")
                except Exception:
                    exact_claim_lines.append(f"- Use this exact first-bet amount in the example: {bet_amount}.")
            if selection:
                exact_claim_lines.append(f"- Use this exact selection in the first example: {selection}.")
            if odds is not None and str(odds).strip() != "":
                odds_display = f"+{int(odds)}" if float(odds) > 0 else str(int(odds))
                exact_claim_lines.append(f"- Use these exact odds in the first example: {odds_display}.")

    system_prompt = (
        """You are a PUNCHY prediction-market editor for Action Network's Top Stories.

TONE: Direct, confident, conversational.
- Explain market mechanics in plain language.
- Use prediction-market wording (trade, position, contract, market).
- Avoid sportsbook/betting/wager terms.

Output well-structured HTML paragraphs. Be compliant but NOT boring.
NO markdown syntax. NO exclamation points. NO corporate-speak.
Follow the STYLE GUIDE provided in the prompt."""
        if prediction_market
        else """You are a PUNCHY DFS editor for Action Network's Top Stories.

TONE: Direct, confident, conversational.
- Explain contest mechanics in plain language.
- Use DFS wording (entries, picks, lineup, contest, fantasy app).
- Avoid sportsbook/betting/wager terms.

Output well-structured HTML paragraphs. Be compliant but NOT boring.
NO markdown syntax. NO exclamation points. NO corporate-speak.
Follow the STYLE GUIDE provided in the prompt."""
        if dfs_mode
        else """You are a PUNCHY sports betting editor for Action Network's Top Stories.

TONE: Direct, confident, conversational. Like explaining to a friend.
- "Here's how it works: place $5 on the Bills moneyline, and whether it hits or not..."
- NOT "The offer provides new users with an opportunity to..."

Output well-structured HTML paragraphs. Be compliant but NOT boring.
NO markdown syntax. NO exclamation points. NO corporate-speak.
Follow the STYLE GUIDE provided in the prompt."""
    )

    if is_how_to_claim:
        if prediction_market:
            section_objective = f"""SECTION OBJECTIVE: Provide a WORKED EXAMPLE with actual dollar amounts.

CRITICAL: This section must include a first-person market example with math:
{claim_intro}
- "A Yes settlement at $1.00 after a $0.40 entry profits $0.60 per contract."
- "The opposite settlement risks the position amount."
- Then show how promo credits apply on a separate eligible market.

Use the worked example provided if available, or create one using the event context."""
        elif dfs_mode:
            section_objective = f"""SECTION OBJECTIVE: Provide a WORKED EXAMPLE with actual dollar amounts.

CRITICAL: This section must include a first-person DFS entry example with math:
{claim_intro}
- \"A 2x result on my $50 entry returns $100 total, including the entry fee.\"
- \"A losing entry costs the entry fee, then the exact bonus entries/credits listed in the offer post after settlement (do not guess).\"
- Then show how bonus entries or promo credits can be used on a separate eligible contest.

Use the worked example provided if available, or create one using the event context."""
        else:
            section_objective = f"""SECTION OBJECTIVE: Provide a WORKED EXAMPLE with actual dollar amounts.

CRITICAL: This section must include a first-person bet example with selected offer mechanics:
{claim_intro}
- "A win pays normally at the selected odds."
- "A loss costs the first stake, and the selected offer determines whether bonus bets post after placement or after settlement."
- Do not start worked-example sentences with "If"; use direct constructions like "A win...", "A loss...", and "A later bonus bet...".
- Explain the bonus timing in plain language. Do not show formulas, multiplication, or payout tables.

Use the bet example provided if available, or create one using the event context.
If structured bet example data is provided, the first paragraph MUST use that exact amount, selection, and odds."""
    elif is_overview:
        audience_label = (
            "users who prefer prediction markets"
            if prediction_market
            else "users who prefer DFS pick'em or fantasy contests"
            if dfs_mode
            else "bettors who want low-commitment entry"
        )
        section_objective = f"""SECTION OBJECTIVE: Explain why this offer matters and what makes it valuable.

Focus on:
- WHO this offer is good for ({audience_label}, etc.)
- WHEN to use it (timing - {'promo credits' if prediction_market else 'bonus entries or promo credits' if dfs_mode else 'bonus bets'} expire in X days, packed schedule, etc.)
- A concise value summary: {offer_summary}
- {code_requirement}

Do NOT include step-by-step instructions (that's in How to Claim)."""
    elif is_eligibility:
        section_objective = f"""SECTION OBJECTIVE: Briefly cover requirements without repeating the intro.

Focus on:
- {'Legal-age/province requirements from source data' if is_canada_market else '21+ and new customer requirement'}
- Exact eligible {availability_label} from source data
- {availability_format_example}
- {'Promo-credit expiration and market-specific eligibility notes when provided' if prediction_market else 'Bonus-entry expiration and contest eligibility notes when provided' if dfs_mode else 'Minimum odds and expiration when provided'}

{code_requirement}
Keep it SHORT - avoid repeating full offer mechanics."""
    else:
        section_objective = f"""SECTION OBJECTIVE: Write helpful content under this heading.

{code_relevance}
Do NOT repeat information from previous sections."""

    if prediction_market:
        event_label = "EVENT CONTEXT (use for worked examples):"
    elif dfs_mode:
        event_label = "EVENT CONTEXT (use for DFS entry examples):"
    else:
        event_label = "EVENT CONTEXT (use for bet examples):"
    if prediction_market:
        language_guardrail = (
            "- Use prediction-market language only (trade, market, position, contract)."
            " Do not use sportsbook, betting, wager, or bonus-bet wording."
        )
    elif dfs_mode:
        language_guardrail = (
            "- Use DFS language only (entries, contests, picks, lineup, fantasy app)."
            " Do not use sportsbook, betting, wager, or bonus-bet wording."
        )
    else:
        language_guardrail = ""
    format_guardrails = [
        "- Default to 2 short HTML paragraphs unless the section objective clearly calls for a list or table.",
    ]
    if prefs["include_bullets"]:
        format_guardrails.append("- You may use a compact <ul><li> list if it helps clarity. Keep it short and editorial, not templated.")
    if prefs["include_table"]:
        format_guardrails.append("- You may use one compact HTML <table> for odds, schedule, or key details if it clearly improves the section.")
    if prefs["enforce_active_voice"]:
        format_guardrails.append("- Prefer active voice and direct verbs. Avoid passive phrasing when a direct construction works.")
    format_guardrails_md = "\n".join(format_guardrails)

    user_prompt = f"""Write the content for this section:

SECTION TITLE: {section_title}

{section_objective}

=== SOURCE OF TRUTH - DO NOT DEVIATE ===
These are exact offer details. Do NOT invent or modify numbers.
{multi_offer_context}
RULE: If a detail is not provided, omit it instead of guessing. Use "Full operator terms apply" only when a fallback is necessary.
=== END SOURCE OF TRUTH ===

{f"MULTI-OFFER RULES:{chr(10)}- This article includes {len(prompt_offers)} offers.{chr(10)}- Mention more than one offer only when the section clearly calls for comparison or options.{chr(10)}- Keep brand/code pairings correct for every mention.{chr(10)}" if has_multiple_offers else ""}

{"WORKED EXAMPLE DATA (use this for worked examples):" + chr(10) + bet_example + chr(10) if bet_example else ""}
{event_label + chr(10) + event_context + chr(10) if event_context else ""}
{f"EXACT MECHANICS REFERENCE (facts only; rewrite from scratch and do not mirror the sentence structure):{chr(10)}{reference_mechanics}{chr(10)}" if reference_mechanics else ""}
{f"EXACT CLAIM FACTS (mandatory for this section):{chr(10)}{chr(10).join(exact_claim_lines)}{chr(10)}" if exact_claim_lines else ""}
{f"INTERNAL EXPERTISE NOTES (use at least {bc_core_required_count} naturally if relevant, but never cite the source):{chr(10)}" + chr(10).join(f"- {point}" for point in bc_core_points) + chr(10) if bc_core_points else ""}

OFFER CONTEXT:
- Brand: {brand}
- Offer: {offer_text}
- Offer Summary: {offer_summary}
- Bonus Code: {bonus_code or "No code required"}
- {availability_context_label}: {primary_states_text}
- {expiration_line[2:]}

{"TALKING POINTS:" + chr(10) + points_md + chr(10) if points_md else ""}
{"DO NOT COVER (handled elsewhere):" + chr(10) + avoid_md + chr(10) if avoid_md else ""}
{f"SECONDARY KEYWORDS (use these naturally across the article and aim for repeated coverage, not stuffing). Never place a secondary keyword in the same sentence as the primary keyword - especially when one contains the other:{chr(10)}{secondary_keywords_md}{chr(10)}" if secondary_keywords_md else ""}
{f"WRITER NOTES:{chr(10)}{structure_notes_md}{chr(10)}" if structure_notes_md else ""}

OPTIONAL INTERNAL LINK SUPPORT:
- Use at most ONE internal link in this section, and only if it clearly helps the reader.
- Never link the heading.
- Never invent a URL or use href="#".
- Prefer the writer-selected links first when they fit the section.
- If the suggested links do not fit the section, use none.
{links_md}

STYLE GUIDE (must follow):
{style_guide}

RAG GUIDANCE (style only, never facts):
{rag_guidance}

STYLE EXAMPLES (match tone only):
{style_examples or "(none)"}

VARIATION BRIEF:
{variation_md}

KEYWORD USAGE:
Primary keyword: "{keyword}"
Current usage: {current_keyword_count}/{target_keyword_total}
- {"SHOULD" if current_keyword_count < target_keyword_total else "MAY"} include the exact phrase "{keyword}" if it fits naturally.
- Do not force the exact keyword more than once in this section.
- Prefer brand references/pronouns after the first exact mention in this section.
- Target ~5-9 exact keyword uses across the full article, not every section.

PREVIOUSLY WRITTEN (do NOT repeat this content):
{previous_content[-1500:] if previous_content else "(first section)"}

{"PHRASES TO AVOID (overused):" + chr(10) + blacklisted_md if blacklisted_md else ""}
{language_guardrail}
{format_guardrails_md}

SECTION-SPECIFIC GUARDRAILS:
- Do not repeat the H1 wording or simply restate the heading.
- Do not call the offer nationwide.
- {"This is a Canada-market article. Use province/provinces language and never say U.S. residents, US users, US states, eligible states, or nationwide." if is_canada_market else "This is a US-market article. Use state/states language for availability."}
- Do not paste the full raw offer string unless the section is explicitly about terms.
- Outside Terms/Eligibility, avoid repeating 21+, minimum odds, or expiration details unless essential.
- Keep any worked example tied to the exact event context or worked-example data provided above.
- For worked-example sections, use the exact mechanics and numbers from the reference blocks above, but write the prose in fresh language.
- For worked-example sections, the exact claim facts block is mandatory. Do not change those numbers or swap in a different first amount.
- If internal expertise notes are present, work at least {bc_core_required_count or 1} of them into the body naturally. Use distinct facts when more than one is available. Never mention BC Core or call anything a trend sample.
- The article should feel new on each run. Keep the structure tight, but vary the phrasing and sentence openings naturally.

DO NOT add responsible gaming disclaimers in this section (handled at the end).

FORMAT: 2 short <p> paragraphs (3 only if a worked example truly needs it)

Write the section now (HTML only, no heading, no markdown):"""

    section_temperature = get_temperature_by_section(level)
    result = await generate_completion(
        prompt=user_prompt,
        system_prompt=system_prompt,
        temperature=section_temperature,
        max_tokens=800,
    )

    sportsbook_claim_requires_exact_retry = (
        is_how_to_claim
        and not prediction_market
        and not dfs_mode
        and bool(bet_example_data)
    )
    if sportsbook_claim_requires_exact_retry and not _sportsbook_claim_matches_input(result, bet_example_data):
        correction_lines = exact_claim_lines or [
            "- Use the exact first-bet amount, selection, and odds from the input data in the first paragraph.",
        ]
        retry_prompt = (
            user_prompt
            + "\n\nMANDATORY CORRECTION:\n"
            + "Your previous draft drifted off the required sportsbook example input.\n"
            + "Rewrite the section now.\n"
            + "The first paragraph must use the exact first-bet facts below, with no substitutions:\n"
            + "\n".join(correction_lines)
            + "\nDo not swap in a different bet size, a different market, or different odds."
        )
        result = await generate_completion(
            prompt=retry_prompt,
            system_prompt=system_prompt,
            temperature=max(0.2, min(section_temperature, 0.5)),
            max_tokens=800,
        )
        if not _sportsbook_claim_matches_input(result, bet_example_data):
            fallback_claim = _render_bet_example_section_deterministic(
                offer=primary_offer,
                bet_example_data=bet_example_data,
                event_context=event_context,
                variation_key=variation_key,
            )
            if fallback_claim:
                return fallback_claim

    result = result.strip()
    if not result.startswith("<p>"):
        result = f"<p>{result}</p>"
    if not is_eligibility:
        result = _polish_body_section_prose(result)
    if bc_core_points and _bc_core_marker_coverage(result, bc_core_points) < bc_core_required_count:
        retry_prompt = (
            user_prompt
            + "\n\nMANDATORY CORRECTION:\n"
            + f"The section must use at least {bc_core_required_count} concrete stat, trend, injury, weather, or schedule detail"
            + ("s" if bc_core_required_count != 1 else "")
            + " from the internal expertise notes.\n"
            + "Do not mention BC Core or use the phrase 'trend sample'."
        )
        result = await generate_completion(
            prompt=retry_prompt,
            system_prompt=system_prompt,
            temperature=max(0.2, min(section_temperature, 0.5)),
            max_tokens=800,
        )
        result = result.strip()
        if not result.startswith("<p>"):
            result = f"<p>{result}</p>"
        if not is_eligibility:
            result = _polish_body_section_prose(result)
        if _bc_core_marker_coverage(result, bc_core_points) < bc_core_required_count:
            result = _inject_bc_core_points_into_html(result, bc_core_points[:bc_core_required_count], max_injections=bc_core_required_count)
    return result

def _render_html_offer_block(offer: dict, switchboard_url: str, property_key: str = "action_network") -> str:
    """Render offer as HTML CTA block."""
    shortcode = str(offer.get("shortcode") or "").strip()
    if not _is_property_correct_bam_shortcode(shortcode, property_key):
        shortcode = _build_property_correct_bam_shortcode(offer, property_key)
    if shortcode:
        return shortcode
    if switchboard_url:
        brand = escape(str(offer.get("brand") or "Claim Offer").strip() or "Claim Offer")
        return f'<p><a data-id="switchboard_tracking" href="{escape(switchboard_url, quote=True)}" rel="nofollow">Claim {brand} offer</a></p>'
    return shortcode


def _html_to_markdown(html: str) -> str:
    """Basic HTML to markdown conversion."""
    # Simple replacements
    md = html
    md = re.sub(r"<h1>(.*?)</h1>", r"# \1", md)
    md = re.sub(r"<h2>(.*?)</h2>", r"## \1", md)
    md = re.sub(r"<h3>(.*?)</h3>", r"### \1", md)
    md = re.sub(r"<p>(.*?)</p>", r"\1\n", md, flags=re.DOTALL)
    md = re.sub(r"<strong>(.*?)</strong>", r"**\1**", md)
    md = re.sub(r"<em>(.*?)</em>", r"*\1*", md)
    md = re.sub(r'<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>', r"[\2](\1)", md)
    md = re.sub(r"<ol>", "", md)
    md = re.sub(r"</ol>", "", md)
    md = re.sub(r"<li>(.*?)</li>", r"1. \1\n", md, flags=re.DOTALL)
    md = re.sub(r"<[^>]+>", "", md)  # Remove remaining tags
    return md.strip()


# ============================================================================
# STREAMING VERSION
# ============================================================================

async def generate_draft_from_outline_streaming(
    outline: list[dict],
    keyword: str,
    title: str,
    offer: dict[str, Any],
    alt_offers: list[dict[str, Any]] | None = None,
    state: str = "ALL",
    offer_property: str = "action_network",
    event_context: str = "",
    article_date: str = "",
    bet_example: str = "",
    bet_example_data: dict[str, Any] | None = None,
    output_format: str = "html",
    variation_key: str = "",
    article_preferences: dict[str, Any] | None = None,
    bc_core_context: dict[str, Any] | None = None,
) -> AsyncGenerator[dict, None]:
    """Generate draft with streaming updates.

    Yields dicts: {type: 'status'|'content'|'done', ...}
    """
    offer = offer or {}
    brand = offer.get("brand", "")
    keyword = _normalize_brand_keyword_text(keyword, brand)
    switchboard_url = _offer_switchboard_url(offer, state=state, property_key=offer_property)

    all_offers = [offer] + (alt_offers or []) if offer else (alt_offers or [])
    content_mode = _get_content_mode(
        offer=offer,
        offers=all_offers,
        keyword=keyword,
        title=title,
    )
    is_prediction_market = content_mode == CONTENT_MODE_PREDICTION_MARKET
    is_dfs_mode = content_mode == CONTENT_MODE_DFS
    variation_key = variation_key or uuid4().hex
    prefs = _normalize_article_preferences(article_preferences)
    preferred_links = _dedupe_link_specs_by_url(get_links_by_urls(prefs["preferred_internal_urls"], property_key=offer_property))
    preferred_urls = [str(link.url) for link in preferred_links if getattr(link, "url", None)]

    def select_offer_for_shortcode(level: str) -> dict[str, Any] | None:
        if not all_offers:
            return None
        idx = _shortcode_index(level)
        if idx < 0 or idx >= len(all_offers):
            return None
        return all_offers[idx]

    parts = []
    previous_content = ""
    total_sections = len(outline)
    keyword_count = 0
    target_keyword_total = 9
    seen_headings: set[str] = set()

    title_html = f"<h1>{title}</h1>"
    parts.append(title_html)

    yield {"type": "status", "message": f"Generating {total_sections} sections..."}
    yield {"type": "content", "section": "title", "content": f"{title_html}\n"}

    for i, section in enumerate(outline):
        level = section.get("level", "h2")
        section_title = _sanitize_heading_text(section.get("title", ""))
        talking_points = section.get("talking_points", [])
        avoid = section.get("avoid", [])

        yield {"type": "status", "message": f"Section {i+1}/{total_sections}: {section_title or level}"}

        if level == "intro":
            content = await _generate_intro_section(
                keyword=keyword,
                title=title,
                offer=offer,
                all_offers=all_offers,
                state=state,
                talking_points=talking_points,
                event_context=event_context,
                article_date=article_date,
                prediction_market=is_prediction_market,
                dfs_mode=is_dfs_mode,
                variation_key=variation_key,
                article_preferences=prefs,
                bc_core_context=bc_core_context,
            )
            parts.append(content)
            previous_content += content
            keyword_count += _count_keyword(content, keyword)
            yield {"type": "content", "section": "intro", "content": content}

        elif level.startswith("shortcode"):
            current_offer = select_offer_for_shortcode(level)
            if current_offer:
                current_switchboard = _offer_switchboard_url(
                    current_offer,
                    state=state,
                    property_key=offer_property,
                ) or switchboard_url
                block = _render_html_offer_block(current_offer, current_switchboard, property_key=offer_property)
                parts.append(block)
                yield {"type": "content", "section": "shortcode", "content": block}

        elif level in ("h2", "h3"):
            normalized = _normalize_heading(section_title)
            if normalized and normalized in seen_headings:
                continue
            if normalized:
                seen_headings.add(normalized)
            content = await _generate_body_section(
                section_title=section_title,
                level=level,
                keyword=keyword,
                offer=offer,
                all_offers=all_offers,
                state=state,
                offer_property=offer_property,
                talking_points=talking_points,
                avoid=avoid,
                previous_content=previous_content,
                current_keyword_count=keyword_count,
                target_keyword_total=target_keyword_total,
                event_context=event_context,
                bet_example=bet_example,
                bet_example_data=bet_example_data,
                prediction_market=is_prediction_market,
                dfs_mode=is_dfs_mode,
                variation_key=variation_key,
                article_preferences=prefs,
                preferred_links=preferred_links,
                bc_core_context=bc_core_context,
            )
            tag = "h2" if level == "h2" else "h3"
            heading = f"<{tag}>{section_title}</{tag}>"
            parts.append(heading)
            parts.append(content)
            previous_content += f"\n{section_title}:\n{content}"
            keyword_count += _count_keyword(content, keyword)
            yield {"type": "content", "section": section_title, "content": heading + "\n" + content}

    # Join and inject links
    html_output = "\n".join(parts)
    html_output = _strip_placeholder_hash_links(html_output)
    html_output = _append_required_property_links(
        html_output,
        property_key=offer_property,
        prediction_market=is_prediction_market,
        dfs_mode=is_dfs_mode,
    )
    html_output = _apply_generation_quality_postprocess(html_output, keyword, prefs.get("market", "US"))
    primary_evergreen_link = get_operator_evergreen_link(property_key=offer_property, brand=brand)
    primary_evergreen_url = str(primary_evergreen_link.url) if primary_evergreen_link and primary_evergreen_link.url else ""
    if prefs.get("market") == "CA" and offer_property == "goal_com" and "goal.com/en-ca/" not in primary_evergreen_url.lower():
        primary_evergreen_url = ""
        preferred_urls = [url for url in preferred_urls if "goal.com/en-ca/" in url.lower()]
    primary_internal_url = preferred_urls[0] if preferred_urls else primary_evergreen_url
    if primary_internal_url:
        html_output = _ensure_first_paragraph_keyword_internal_link(html_output, keyword, primary_internal_url)
    disclaimer_state = "CANADA" if prefs.get("market") == "CA" and str(state or "").upper() == "ALL" else state
    disclaimer = get_disclaimer_for_state(disclaimer_state)
    if is_prediction_market:
        disclaimer = _adapt_disclaimer_for_prediction_market(disclaimer)
    elif is_dfs_mode:
        disclaimer = _adapt_disclaimer_for_dfs(disclaimer)
    html_output = _ensure_single_disclaimer(html_output, disclaimer)
    yield {"type": "content", "section": "footer", "content": f"<p><em>{disclaimer}</em></p>"}
    all_offers = [offer] + (alt_offers or []) if offer else (alt_offers or [])
    html_output = _inject_switchboard_links_for_offers(
        html_output,
        offers=all_offers,
        state=state,
        property_key=offer_property,
        max_links=1,
    )
    html_output = _strip_placeholder_hash_links(html_output)
    html_output = _strip_invalid_non_switchboard_links(html_output)
    html_output = _keep_selected_non_switchboard_links(
        html_output,
        preferred_urls,
        fallback_primary_url=primary_internal_url,
    )
    html_output = _align_selected_link_anchors(
        html_output,
        preferred_links,
        [keyword, *prefs["secondary_keywords"]],
    )
    html_output = await _humanize_article_html(
        html_output,
        keyword=keyword,
        offer=offer,
        content_mode=content_mode,
    )
    html_output = _ensure_keyword_in_first_paragraph(html_output, keyword)
    html_output = _apply_content_mode_language_guardrails(html_output, content_mode)
    html_output = _normalize_brand_keyword_text(html_output, brand)
    html_output = _target_keyword_mentions(html_output, keyword)
    html_output = _enforce_primary_keyword_density(html_output, keyword)
    html_output = _enforce_secondary_keyword_mentions(html_output, prefs["secondary_keywords"])
    html_output = _clean_orphaned_keyword_page_references(html_output, keyword)
    html_output = _unwrap_generic_offer_strong(html_output, brand)
    html_output = _strip_source_and_prompt_leaks(html_output)
    html_output = _strip_unprovided_article_date(html_output, article_date)
    html_output = _strip_market_mismatch_phrasing(html_output, prefs.get("market", "US"))
    html_output = _strip_formatting_from_headings(html_output)
    html_output = await _ensure_matchup_analysis_section(
        html_output,
        keyword=keyword,
        offer=offer,
        event_context=event_context,
        bc_core_context=bc_core_context,
        content_mode=content_mode,
        bet_example_data=bet_example_data,
    )
    html_output = await _ensure_editorial_body_length(
        html_output,
        keyword=keyword,
        offer=offer,
        event_context=event_context,
        bc_core_context=bc_core_context,
        content_mode=content_mode,
        bet_example_data=bet_example_data,
    )
    html_output = _cap_primary_keyword_density(html_output, keyword)
    html_output = _title_case_headings(html_output)
    html_output = _normalize_brand_casing(
        html_output,
        brand or (keyword.split()[0] if keyword.split() else ""),
    )

    if output_format == "markdown":
        html_output = _html_to_markdown(html_output)

    html_output = _ensure_top_story_tracking_tag(html_output)
    yield {"type": "done", "draft": html_output, "word_count": len(html_output.split())}


# ============================================================================
# LEGACY TOKEN-BASED DRAFT (for backward compatibility)
# ============================================================================

def parse_token(token: str) -> dict:
    """Parse a token into its components (legacy)."""
    token = token.strip()

    if token.upper() == "[INTRO]":
        return {"type": "intro", "title": "Introduction"}

    shortcode_match = re.match(r"\[(SHORTCODE(?:_[A-Z0-9]+)?)\]", token, re.IGNORECASE)
    if shortcode_match:
        label = shortcode_match.group(1).lower()
        return {"type": label, "title": "Promo Module"}

    h2_match = re.match(r"\[H2:\s*(.+)\]", token, re.IGNORECASE)
    if h2_match:
        return {"type": "h2", "title": h2_match.group(1).strip()}

    h3_match = re.match(r"\[H3:\s*(.+)\]", token, re.IGNORECASE)
    if h3_match:
        return {"type": "h3", "title": h3_match.group(1).strip()}

    return {"type": "unknown", "title": token}


def _hydrate_outline_guidance(outline: list[dict], keyword: str) -> list[dict]:
    """Add baseline talking points for legacy token outlines."""
    hydrated: list[dict] = []
    for section in outline:
        level = str(section.get("level", "h2"))
        title = str(section.get("title", ""))
        points = list(section.get("talking_points") or [])
        avoid = list(section.get("avoid") or [])

        if level in ("h2", "h3") and not points:
            title_lower = title.lower()
            is_signup = _is_signup_heading(title_lower)
            is_claim = _is_claim_heading(title_lower, is_signup)
            is_terms = any(x in title_lower for x in ["terms", "conditions", "fine print"])
            is_eligibility = any(x in title_lower for x in ["eligibility", "key details", "requirements"])
            is_overview = any(x in title_lower for x in ["overview", "what is", "about"])
            is_daily_promos = _is_daily_promos_heading(title_lower)

            if is_signup:
                points = [
                    "Step-by-step registration flow",
                    "Where to enter promo code (or note none is required)",
                    "How first deposit and qualifying bet work",
                ]
                avoid.extend(["Long legal disclaimers", "Repeating full offer description"])
            elif is_claim:
                points = [
                    "First-person worked bet example",
                    "Win scenario payout math",
                    "Loss scenario and what bonus is received",
                ]
                avoid.extend(["Generic feature descriptions"])
            elif is_terms:
                points = [
                    "Only include verified terms from source data",
                    "If details are missing, direct reader to full terms",
                ]
                avoid.extend(["Inventing legal restrictions"])
            elif is_eligibility:
                points = [
                    "21+ and new customer requirement",
                    "Eligible states and key restrictions",
                    "Bonus expiration and minimum odds if available",
                ]
                avoid.extend(["Restating full offer mechanics"])
            elif is_daily_promos:
                points = [
                    "List today's rotating promos and promo codes",
                    "Include state availability for each listed promo",
                    "Mark this section for daily editorial refresh before publish",
                ]
                avoid.extend(["Outdated promo details from previous days"])
            elif is_overview:
                points = [
                    f"Why the {keyword} offer matters now",
                    "Who benefits most from this offer",
                    "Value and timing in plain language",
                ]
                avoid.extend(["Step-by-step sign-up details"])
            else:
                points = [
                    f"Address the section angle for {keyword}",
                    "Include one concrete and verifiable offer detail",
                ]

        hydrated.append({
            "level": level,
            "title": title,
            "talking_points": points,
            "avoid": avoid,
        })

    return hydrated


async def generate_draft(
    outline_tokens: list[str],
    keyword: str,
    title: str,
    offer: dict[str, Any] | None = None,
    alt_offers: list[dict[str, Any]] | None = None,
    state: str = "ALL",
    style_profile: str = "Top Stories – Informative",
    game_context: str = "",
    bet_example: str = "",
) -> str:
    """Generate full article draft from outline tokens (legacy).

    Converts tokens to structured outline and uses new system.
    """
    # Convert tokens to structured outline
    outline = []
    for token in outline_tokens:
        parsed = parse_token(token)
        outline.append({
            "level": parsed["type"],
            "title": parsed["title"] if parsed["type"] not in ("intro",) and not str(parsed["type"]).startswith("shortcode") else "",
            "talking_points": [],
            "avoid": [],
        })
    outline = _hydrate_outline_guidance(outline, keyword)

    return await generate_draft_from_outline(
        outline=outline,
        keyword=keyword,
        title=title,
        offer=offer or {},
        alt_offers=alt_offers,
        state=state,
        event_context=game_context,
        bet_example=bet_example,
        output_format="html",
    )


async def generate_draft_streaming(
    outline_tokens: list[str],
    keyword: str,
    title: str,
    offer: dict[str, Any] | None = None,
    alt_offers: list[dict[str, Any]] | None = None,
    state: str = "ALL",
    style_profile: str = "Top Stories – Informative",
) -> AsyncGenerator[dict, None]:
    """Generate draft with streaming updates (legacy)."""
    # Convert tokens to structured outline
    outline = []
    for token in outline_tokens:
        parsed = parse_token(token)
        outline.append({
            "level": parsed["type"],
            "title": parsed["title"] if parsed["type"] not in ("intro",) and not str(parsed["type"]).startswith("shortcode") else "",
            "talking_points": [],
            "avoid": [],
        })
    outline = _hydrate_outline_guidance(outline, keyword)

    async for update in generate_draft_from_outline_streaming(
        outline=outline,
        keyword=keyword,
        title=title,
        offer=offer or {},
        alt_offers=alt_offers,
        state=state,
        output_format="markdown",
    ):
        yield update
