"""Draft generation service (Execute stage).

Expands structured outlines with talking points into full article draft.
Outputs HTML format for direct publishing.
"""

import re
import markdown
from datetime import datetime
from typing import AsyncGenerator, Any
from zoneinfo import ZoneInfo

from app.services.llm import generate_completion, generate_completion_structured
from app.services.rag import query_articles
from app.services.internal_links import (
    format_links_markdown,
    get_operator_evergreen_link,
    suggest_links_for_section,
)
from app.services.compliance import get_disclaimer_for_state
from app.services.bam_offers import PROPERTIES, render_bam_offer_block
from app.services.content_guidelines import get_style_instructions, get_temperature_by_section
from app.services.style import get_rag_usage_guidance
from app.services.switchboard_links import inject_switchboard_links, build_switchboard_url
from app.services.operator_profile import (
    CONTENT_MODE_DFS,
    CONTENT_MODE_PREDICTION_MARKET,
    CONTENT_MODE_SPORTSBOOK,
    get_content_mode_offer,
)
from app.services.offer_parsing import (
    extract_bonus_amount,
    extract_bonus_expiration_days,
    extract_minimum_odds,
    extract_offer_amount_details,
    extract_states_from_terms,
    extract_wagering_requirement,
    parse_states,
)


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
    return bool(re.search(
        r"\b(sign ?up|sign-up|signup|register|registration|create an? account|open an? account|"
        r"get started|how to sign|how to register|how to join)\b",
        title_lower,
    ))


def _is_claim_heading(title_lower: str, is_signup: bool) -> bool:
    """Return True if the section title indicates a claim/usage example."""
    if is_signup:
        return False
    if not title_lower:
        return False
    return bool(re.search(
        r"\b(how to claim|claim|worked example|bet example|example|how to use)\b",
        title_lower,
    ))


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

    # Only rewrite visible text nodes so href/src attributes and URLs remain intact.
    tokens = re.findall(r"<[^>]+>|[^<]+", html, flags=re.DOTALL)
    out: list[str] = []
    for token in tokens:
        out.append(token if token.startswith("<") else replacer(token))
    return "".join(out)


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
    out = re.sub(r"gambling problem\?", "Need help?", disclaimer, flags=re.IGNORECASE)
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
        prop = PROPERTIES.get(prop_key, PROPERTIES["action_network"])
        return build_switchboard_url(
            affiliate_id,
            campaign_id,
            state_code=state if state != "ALL" else "",
            property_id=prop.get("property_id", "1"),
            switchboard_domain=prop.get("switchboard_domain", "switchboard.actionnetwork.com"),
        )

    return str(offer.get("switchboard_link") or "").strip()


def _build_signup_list(
    brand: str,
    has_code: bool,
    code_strong: str,
    prediction_market: bool = False,
    dfs_mode: bool = False,
) -> str:
    """Build a deterministic 5-step signup list as HTML."""
    brand_label = brand or ("the operator" if prediction_market else "the DFS app" if dfs_mode else "the sportsbook")
    signup_guide_ref = f"{brand_label} sign-up guide"
    mechanics_ref = (
        "how market contracts settle"
        if prediction_market
        else "how pick'em entries work"
        if dfs_mode
        else "how bonus bets work"
    )

    step_two = (
        f"Create your account and enter {code_strong}."
        if has_code
        else "Create your account (no promo code required)."
    )

    steps = [
        f"Confirm you're eligible in your state and open the {signup_guide_ref}.",
        step_two,
        "Complete verification and log in.",
        "Fund your account.",
        (
            f"Place a qualifying market position and review {mechanics_ref} for settlement details."
            if prediction_market
            else f"Place a qualifying fantasy entry and review {mechanics_ref} for contest rules."
            if dfs_mode
            else f"Place a qualifying bet and review {mechanics_ref} for payout details."
        ),
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


def _count_non_switchboard_links(html: str) -> int:
    """Count non-switchboard links in HTML."""
    if not html:
        return 0
    links = re.findall(r'<a\b([^>]*)href\s*=\s*(["\'])(https?://[^"\']+)\2([^>]*)>', html, flags=re.IGNORECASE)
    count = 0
    for before_attrs, _, _, after_attrs in links:
        attrs_text = f"{before_attrs} {after_attrs}".lower()
        if "switchboard_tracking" in attrs_text:
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
        if "switchboard_tracking" in attrs_text:
            return match.group(0)
        url_key = url.strip().lower()
        if not url_key:
            return match.group(0)
        if url_key in seen_urls:
            return inner
        seen_urls.add(url_key)
        return match.group(0)

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
        after_attrs = match.group(4) or ""
        attrs_text = f"{before_attrs} {after_attrs}".lower()
        if "switchboard_tracking" in attrs_text:
            return match.group(0)
        if kept >= max_links:
            return match.group(5) or ""
        kept += 1
        return match.group(0)

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
    states_text = _offer_states_text(offer, state)
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


def _offer_states_text(offer: dict[str, Any], fallback_state: str = "ALL") -> str:
    """Build a human-readable state list for prompts."""
    states = _normalize_states(offer.get("states_list") or offer.get("states"))
    if not states:
        states = extract_states_from_terms(str(offer.get("terms") or ""))
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
            "<li><strong>[Operator 1]:</strong> [Promo details]. Code: [CODE]. States Available: [state list].</li>",
            "<li><strong>[Operator 2]:</strong> [Promo details]. Code: [CODE]. States Available: [state list].</li>",
            "<li><strong>[Operator 3]:</strong> [Promo details]. Code: [CODE]. States Available: [state list].</li>",
        ]
    elif dfs_mode:
        items = [
            "<li><strong>[DFS App 1]:</strong> [Promo details]. Code: [CODE]. States Available: [state list].</li>",
            "<li><strong>[DFS App 2]:</strong> [Promo details]. Code: [CODE]. States Available: [state list].</li>",
            "<li><strong>[DFS App 3]:</strong> [Promo details]. Code: [CODE]. States Available: [state list].</li>",
        ]
    else:
        items = [
            "<li><strong>[Sportsbook 1]:</strong> [Promo details]. Code: [CODE]. States Available: [state list].</li>",
            "<li><strong>[Sportsbook 2]:</strong> [Promo details]. Code: [CODE]. States Available: [state list].</li>",
            "<li><strong>[Sportsbook 3]:</strong> [Promo details]. Code: [CODE]. States Available: [state list].</li>",
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
    prediction_market: bool = False,
    dfs_mode: bool = False,
) -> str:
    """Render a deterministic terms section to avoid legal hallucinations."""
    normalized_offers = [offer for offer in (offers or []) if offer]
    if len(normalized_offers) > 1:
        items: list[str] = []
        for offer in normalized_offers[:3]:
            offer_terms = str(offer.get("terms") or "")
            offer_expiration = offer.get("bonus_expiration_days") or extract_bonus_expiration_days(offer_terms)
            offer_min_odds = str(offer.get("minimum_odds") or extract_minimum_odds(offer_terms) or "").strip()
            offer_wagering = str(offer.get("wagering_requirement") or extract_wagering_requirement(offer_terms) or "").strip()
            brand = str(offer.get("brand") or "Offer").strip()
            code = str(offer.get("bonus_code") or "").strip()
            states_text = _offer_states_text(offer, "ALL")
            parts = [f"<strong>{brand}</strong>."]
            if code:
                parts.append(f"Code: {code}.")
            else:
                parts.append("No promo code required.")
            parts.append(f"States Available: {states_text}.")
            if offer_expiration is not None:
                parts.append(
                    f"Promotional credits expire in {offer_expiration} days."
                    if prediction_market
                    else f"Bonus entries expire in {offer_expiration} days."
                    if dfs_mode
                    else f"Bonus bets expire in {offer_expiration} days."
                )
            if not prediction_market and not dfs_mode:
                if offer_min_odds:
                    parts.append(f"Minimum odds requirement: {offer_min_odds}.")
                if offer_wagering:
                    parts.append(f"Wagering requirement: {offer_wagering}.")
            parts.append("Full operator terms apply.")
            items.append(f"<li>{' '.join(parts)}</li>")
        return "<ul>\n" + "\n".join(items) + "\n</ul>"

    if terms:
        cleaned = terms.replace("\\n", "\n")
        paras = [p.strip() for p in cleaned.splitlines() if p.strip()]
        if paras:
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
        "Full operator terms apply for complete eligibility and restrictions."
        if not prediction_market and not dfs_mode
        else "Full operator terms apply for complete eligibility and contest rules."
        if dfs_mode
        else "Full operator terms apply for complete eligibility, market rules, and settlement details."
    )
    return f"<p>{' '.join(points)}</p>"

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
        if len(steps) == 5:
            return steps
    except Exception:
        pass
    return None

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
            details.append(f"States Available: {states_text}.")
        second = " ".join(details) or "Full operator terms apply."

    return f"<p>{first}</p>\n<p>{second}</p>"


def _ensure_intro_state_specificity(html: str, states_text: str) -> str:
    """Ensure intro copy uses explicit states when state list is known."""
    if not html or not states_text:
        return html

    normalized_states = states_text.strip()
    if not normalized_states or normalized_states.lower().startswith("all eligible states"):
        return html

    html = re.sub(r"\bavailable nationwide\b", f"available in {normalized_states}", html, flags=re.IGNORECASE)
    html = re.sub(r"\bnationwide states\b", normalized_states, html, flags=re.IGNORECASE)

    state_tokens = [s.strip().upper() for s in normalized_states.split(",") if s.strip()]
    plain = re.sub(r"<[^>]+>", " ", html).upper()
    has_states_available_phrase = "STATES AVAILABLE:" in plain
    has_explicit_state = any(re.search(rf"\b{re.escape(token)}\b", plain) for token in state_tokens)
    if has_explicit_state and has_states_available_phrase:
        return html

    addition = f" States Available: {normalized_states}."
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
    first_para = match.group(0)
    first_inner = match.group(1)
    plain = re.sub(r"<[^>]+>", " ", first_inner)
    if re.search(re.escape(keyword), plain, flags=re.IGNORECASE):
        return html
    prefix = f"The {keyword} is live today. "
    updated_para = f"<p>{prefix}{first_inner.strip()}</p>"
    return html.replace(first_para, updated_para, 1)


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


def _apply_generation_quality_postprocess(html: str, keyword: str) -> str:
    """Final article cleanup for intro consistency, keyword placement, and repetition."""
    if not html:
        return html
    html = _soften_repetitive_intro_opener(html)
    html = _ensure_keyword_in_first_paragraph(html, keyword)
    html = _normalize_matchup_vs_notation(html)
    html = _trim_repeated_phrase_in_html(html, "see full terms", max_occurrences=2, replacement="see terms")
    return html


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
    if not event_context:
        return ""
    match = re.search(r"Featured game:\s*([^\.]+)", event_context, flags=re.IGNORECASE)
    if not match:
        return ""
    matchup = match.group(1).strip()
    return re.sub(r"\s@\s", " vs. ", matchup)


def _render_bet_example_section_deterministic(
    *,
    offer: dict[str, Any],
    bet_example_data: dict[str, Any] | None,
    event_context: str = "",
) -> str | None:
    """Render a sportsbook worked-example section from structured UI selections."""
    data = dict(bet_example_data or {})
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
    event_label = _extract_matchup_from_event_context_text(event_context) or str(data.get("event_context") or "").strip()
    event_clause = f" for {event_label}" if event_label else ""
    odds_display = f"{odds:+d}"

    bonus_code = str(offer.get("bonus_code") or "").strip()
    offer_text = str(offer.get("offer_text") or offer.get("affiliate_offer") or "").strip()
    brand = str(offer.get("brand") or "").strip()

    if bonus_code:
        code_clause = f"with code <strong>{bonus_code}</strong>"
    else:
        code_clause = "with no promo code required"

    offer_clause = (
        f"{brand} is offering {offer_text}"
        if brand and offer_text
        else offer_text
        if offer_text
        else "follow the exact offer terms for the promo details"
    )

    return (
        f"<p>Here is a worked example using {book_label}. If I place a ${bet_amount:.0f} bet on {selection} at {odds_display}{event_clause}, "
        f"I profit ${profit:.2f} if it wins and get back ${total_return:.2f} total (including stake).</p>"
        f"<p>If the bet loses, I am down ${bet_amount:.0f} on the wager. Then {code_clause}, {offer_clause}. Keep the bonus mechanics tied to the exact offer terms listed in this article.</p>"
    )


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
    bet_example: str = "",
    bet_example_data: dict[str, Any] | None = None,
    output_format: str = "html",
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

    def select_offer_for_shortcode(level: str) -> dict[str, Any] | None:
        if not all_offers:
            return None
        if level in ("shortcode", "shortcode_main"):
            return all_offers[0]
        if level.startswith("shortcode_"):
            suffix = level.split("_", 1)[1]
            if suffix.isdigit():
                idx = int(suffix)
                if idx < len(all_offers):
                    return all_offers[idx]
            # Unknown suffix falls back to main
            return all_offers[0]
        return all_offers[0]

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
                prediction_market=is_prediction_market,
                dfs_mode=is_dfs_mode,
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
                block = _render_html_offer_block(current_offer, current_switchboard)
                parts.append(block)
            else:
                parts.append("<!-- Promo module placeholder -->")

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
    html_output = _apply_generation_quality_postprocess(html_output, keyword)
    primary_evergreen_link = get_operator_evergreen_link(property_key=offer_property, brand=brand)
    if primary_evergreen_link and primary_evergreen_link.url:
        html_output = _link_first_keyword_internal(html_output, keyword, primary_evergreen_link.url)

    # Ensure single disclaimer at the end
    disclaimer = get_disclaimer_for_state(state)
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
    html_output = _dedupe_non_switchboard_links_by_url(html_output)
    html_output = _limit_non_switchboard_links(html_output, max_links=1)
    html_output = _apply_content_mode_language_guardrails(html_output, content_mode)
    html_output = _normalize_brand_keyword_text(html_output, brand)

    if output_format == "markdown":
        # Convert back to markdown (basic)
        return _html_to_markdown(html_output)

    return html_output


async def _generate_intro_section(
    keyword: str,
    title: str,
    offer: dict,
    all_offers: list[dict[str, Any]] | None,
    state: str,
    talking_points: list[str],
    event_context: str = "",
    prediction_market: bool = False,
    dfs_mode: bool = False,
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
    date_str = today_long()
    style_guide = get_style_instructions()
    has_code = bool(bonus_code.strip())
    preferred_code_term = _preferred_code_term(brand)
    code_strong = f"<strong>{bonus_code}</strong>" if has_code else ""
    link_anchor = f"<strong>{brand} offer</strong>" if brand else "<strong>the offer</strong>"
    prompt_offers = [o for o in (all_offers or []) if o] or [offer]
    has_multiple_offers = len(prompt_offers) > 1
    multi_offer_context = _build_multi_offer_prompt_context(
        prompt_offers,
        state,
        prediction_market=prediction_market,
        dfs_mode=dfs_mode,
    )
    states_text = _offer_states_text(offer, state)

    # Format talking points for prompt
    points_md = "\n".join(f"- {p}" for p in talking_points) if talking_points else ""

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
        game_hook = f"GAME HOOK (use this to open):\n{event_context}\n\n"

    requirements = [
        "If there is a game hook, open sentence one with the matchup/time/network context and the offer value (do not reuse the same stock opener).",
        "If no game hook, start with a direct offer statement; avoid generic openers like \"If you are looking for a valuable offer...\"",
        "Use explicit eligible states from source data. Do not say 'nationwide states'.",
        "When listing state eligibility, use this exact label format: 'States Available: AZ, CO, ...'.",
        "Do not paste the full raw offer string more than once. Prefer a natural summary.",
        "Do not mention 21+, minimum odds, or long legal disclaimers in the intro.",
        "If expiration is mentioned, it must describe the bonus/credit expiration, not the offer itself.",
        "Do NOT include responsible gaming disclaimers here (handled at the end of the article).",
    ]
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
    if has_multiple_offers:
        requirements.append("This article includes multiple offers: mention the main offer first, and weave in one other offer only if it fits naturally.")
    if has_code:
        requirements.extend([
            f"Use the {preferred_code_term} {bonus_code} naturally once or twice in plain text.",
            f"Include at most ONE natural <strong> anchor, e.g., {link_anchor} or {code_strong}.",
            "Do NOT wrap every mention in <strong>.",
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
    ])
    requirements_md = "\n".join(f"- {r}" for r in requirements)

    if has_code:
        example_output = (
            f"<p>The {preferred_code_term if brand else 'offer'} is live for [Game] tonight at [time] on [network], and {brand} is highlighting {offer_summary} ahead of {date_str}.</p>"
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
            f"<p>The {brand} offer is live for [Game] tonight at [time] on [network], and {brand} is highlighting {offer_summary} ahead of {date_str}.</p>"
            + (
                "<p>No promo code is required; complete the qualifying action described in the offer to unlock the listed promotional credit.</p>"
                if prediction_market
                else "<p>No promo code is required; complete the qualifying DFS entry described in the offer to unlock the listed bonus entries or promo credits.</p>"
                if dfs_mode
                else "<p>No promo code is required to claim it; just sign up and place your first bet.</p>"
            )
        )

    user_prompt = f"""Write the intro paragraph for this promo article:

DATE (include this): {date_str}

{game_hook}OFFER DETAILS:
- Brand: {brand}
- Offer: {offer_text}
- Offer Summary: {offer_summary}
- Bonus Code: {bonus_code or "No code required"}
- Bonus Amount: {bonus_amount or "See offer"}
- {expiration_line[2:]}
- Eligible States: {states_text}

{f"MULTI-OFFER SOURCE OF TRUTH (use correct brand/code pairings):{chr(10)}{multi_offer_context}{chr(10)}" if has_multiple_offers else ""}

KEYWORD: {keyword}

{points_md if points_md else ""}

STYLE GUIDE (must follow):
{style_guide}

CRITICAL REQUIREMENTS:
{requirements_md}

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
    return _ensure_intro_state_specificity(result, states_text)


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
) -> str:
    """Generate a body section (H2 or H3)."""
    prompt_offers = [o for o in (all_offers or []) if o] or ([offer] if offer else [])
    primary_offer = offer or (prompt_offers[0] if prompt_offers else {})
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
    primary_states_text = _offer_states_text(primary_offer, state)

    style_guide = get_style_instructions()
    rag_guidance = get_rag_usage_guidance()
    has_code = bool(bonus_code.strip())
    preferred_code_term = _preferred_code_term(brand)
    code_strong = f"<strong>{bonus_code}</strong>" if has_code else ""
    link_anchor = f"<strong>{brand} offer</strong>" if brand else "<strong>the offer</strong>"

    code_requirement = (
        f"Mention the {preferred_code_term} {bonus_code} at most once if it helps the section. "
        f"Include at most ONE natural <strong> anchor for linking, e.g., {link_anchor} or {code_strong}."
        if has_code
        else f"State clearly that no promo code is required (do not invent a code). "
        f"Include at most ONE natural <strong> anchor for linking, e.g., {link_anchor}."
    )
    code_relevance = (
        f"If relevant, mention the {preferred_code_term} {bonus_code} once in plain text and optionally include one <strong> anchor like {link_anchor}."
        if has_code
        else f"If relevant, note that no promo code is required and optionally include one <strong> anchor like {link_anchor}."
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
            f'- "If I open a $50 position on [Market] at [price], I start by signing up and entering {code_strong}."'
            if has_code
            else '- "If I open a $50 position on [Market] at [price], I start by signing up (no promo code required)."'
        )
    elif dfs_mode:
        claim_intro = (
            f"- \"If I enter a $50 pick'em contest on [Game/Slate], I start by signing up and entering {code_strong}.\""
            if has_code
            else "- \"If I enter a $50 pick'em contest on [Game/Slate], I start by signing up (no promo code required).\""
        )
    else:
        claim_intro = (
            f'- "If I place a $50 moneyline bet on [Team] at [odds], I start by signing up and entering {code_strong}."'
            if has_code
            else '- "If I place a $50 moneyline bet on [Team] at [odds], I start by signing up (no promo code required)."'
        )

    try:
        snippets = await query_articles(f"{section_title} {keyword}", k=3, snippet_chars=400)
        style_examples = "\n\n".join([s.get("snippet", "") for s in snippets])[:1500]
    except Exception:
        style_examples = ""

    try:
        links = await suggest_links_for_section(
            section_title,
            [keyword, brand],
            k=3,
            property_key=offer_property,
            brand=brand,
        )
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

    title_lower = section_title.lower()
    is_signup = _is_signup_heading(title_lower)
    is_how_to_claim = _is_claim_heading(title_lower, is_signup)
    is_numbered_list = is_signup
    is_overview = any(x in title_lower for x in ["overview", "what is", "about"])
    is_eligibility = any(x in title_lower for x in ["eligibility", "key details", "requirements"])
    is_daily_promos = "daily promo" in title_lower or "promos today" in title_lower
    is_terms = any(x in title_lower for x in ["terms", "conditions", "fine print"])

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
            prediction_market=prediction_market,
            dfs_mode=dfs_mode,
        )

    if is_numbered_list:
        steps = await _generate_signup_steps_structured(
            brand=brand,
            keyword=keyword,
            state=state,
            has_code=has_code,
            code_strong=code_strong,
            style_guide=style_guide,
            links_md=links_md,
            prediction_market=prediction_market,
            dfs_mode=dfs_mode,
        )
        if steps:
            return _steps_to_html(steps)
        return _build_signup_list(
            brand,
            has_code,
            code_strong,
            prediction_market=prediction_market,
            dfs_mode=dfs_mode,
        )

    if is_how_to_claim and not prediction_market and not dfs_mode and bet_example_data:
        deterministic_claim = _render_bet_example_section_deterministic(
            offer=primary_offer,
            bet_example_data=bet_example_data,
            event_context=event_context,
        )
        if deterministic_claim:
            return deterministic_claim

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
- "If my position settles Yes at $1.00 after entering at $0.40, profit is $0.60 per contract."
- "If it settles the other way, I can lose the position amount."
- Then show how promo credits can be applied on a separate eligible market.

Use the worked example provided if available, or create one using the event context."""
        elif dfs_mode:
            section_objective = f"""SECTION OBJECTIVE: Provide a WORKED EXAMPLE with actual dollar amounts.

CRITICAL: This section must include a first-person DFS entry example with math:
{claim_intro}
- \"If my $50 entry returns 2x on the contest payout structure, I receive $100 back total (including stake).\"
- \"If it does not cash, I lose the entry fee, then explain the exact bonus entries/credits listed in the offer (do not guess).\"
- Then show how bonus entries or promo credits can be used on a separate eligible contest.

Use the worked example provided if available, or create one using the event context."""
        else:
            section_objective = f"""SECTION OBJECTIVE: Provide a WORKED EXAMPLE with actual dollar amounts.

CRITICAL: This section must include a first-person bet example with math:
{claim_intro}
- "If my bet wins at +120, I profit $60 and get back my $50 stake, so I cash out $110 total."
- "If it loses, I am down $50 on the bet, but I still receive [bonus amount] in bonus bets."
- Then show how to use the bonus bets: "If I put $200 in bonus bets on [another pick] at -110 and it wins, the payout is profit-only: $200 × (100/110) = $181.82"

Use the bet example provided if available, or create one using the event context."""
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
- 21+ and new customer requirement
- Exact eligible states from source data
- If states are listed, render as: "States Available: AZ, CO, ..."
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

OFFER CONTEXT:
- Brand: {brand}
- Offer: {offer_text}
- Offer Summary: {offer_summary}
- Bonus Code: {bonus_code or "No code required"}
- Eligible States: {primary_states_text}
- {expiration_line[2:]}

{"TALKING POINTS:" + chr(10) + points_md + chr(10) if points_md else ""}
{"DO NOT COVER (handled elsewhere):" + chr(10) + avoid_md + chr(10) if avoid_md else ""}

OPTIONAL INTERNAL LINK SUPPORT:
- Use at most ONE internal link in this section, and only if it clearly helps the reader.
- Never link the heading.
- Never invent a URL or use href="#".
- If the suggested links do not fit the section, use none.
{links_md}

STYLE GUIDE (must follow):
{style_guide}

RAG GUIDANCE (style only, never facts):
{rag_guidance}

STYLE EXAMPLES (match tone only):
{style_examples or "(none)"}

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

SECTION-SPECIFIC GUARDRAILS:
- Do not repeat the H1 wording or simply restate the heading.
- Do not call the offer nationwide.
- Do not paste the full raw offer string unless the section is explicitly about terms.
- Outside Terms/Eligibility, avoid repeating 21+, minimum odds, or expiration details unless essential.
- Keep any worked example tied to the exact event context or worked-example data provided above.

DO NOT add responsible gaming disclaimers in this section (handled at the end).

FORMAT: 2 short <p> paragraphs (3 only if a worked example truly needs it)

Write the section now (HTML only, no heading, no markdown):"""

    result = await generate_completion(
        prompt=user_prompt,
        system_prompt=system_prompt,
        temperature=get_temperature_by_section(level),
        max_tokens=800,
    )

    return result.strip()

def _render_html_offer_block(offer: dict, switchboard_url: str) -> str:
    """Render offer as HTML CTA block."""
    shortcode = offer.get("shortcode") or ""
    if not shortcode:
        return "<!-- Promo module placeholder -->"
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
    bet_example: str = "",
    bet_example_data: dict[str, Any] | None = None,
    output_format: str = "html",
) -> AsyncGenerator[dict, None]:
    """Generate draft with streaming updates.

    Yields dicts: {type: 'status'|'content'|'done', ...}
    """
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

    def select_offer_for_shortcode(level: str) -> dict[str, Any] | None:
        if not all_offers:
            return None
        if level in ("shortcode", "shortcode_main"):
            return all_offers[0]
        if level.startswith("shortcode_"):
            suffix = level.split("_", 1)[1]
            if suffix.isdigit():
                idx = int(suffix)
                if idx < len(all_offers):
                    return all_offers[idx]
            return all_offers[0]
        return all_offers[0]

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
                prediction_market=is_prediction_market,
                dfs_mode=is_dfs_mode,
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
                block = _render_html_offer_block(current_offer, current_switchboard)
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
    html_output = _apply_generation_quality_postprocess(html_output, keyword)
    primary_evergreen_link = get_operator_evergreen_link(property_key=offer_property, brand=brand)
    if primary_evergreen_link and primary_evergreen_link.url:
        html_output = _link_first_keyword_internal(html_output, keyword, primary_evergreen_link.url)
    disclaimer = get_disclaimer_for_state(state)
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
    html_output = _dedupe_non_switchboard_links_by_url(html_output)
    html_output = _limit_non_switchboard_links(html_output, max_links=1)
    html_output = _apply_content_mode_language_guardrails(html_output, content_mode)
    html_output = _normalize_brand_keyword_text(html_output, brand)

    if output_format == "markdown":
        html_output = _html_to_markdown(html_output)

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
            is_daily_promos = "daily promo" in title_lower or "promos today" in title_lower

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
