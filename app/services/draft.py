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
from app.services.internal_links import suggest_links_for_section, format_links_markdown
from app.services.compliance import get_disclaimer_for_state
from app.services.bam_offers import render_bam_offer_block
from app.services.content_guidelines import get_style_instructions, get_temperature_by_section
from app.services.style import get_rag_usage_guidance
from app.services.switchboard_links import inject_switchboard_links, build_switchboard_url
from app.services.offer_parsing import (
    extract_bonus_amount,
    extract_bonus_expiration_days,
    extract_minimum_odds,
    extract_wagering_requirement,
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


def _inject_switchboard_links_for_offers(
    html_output: str,
    offers: list[dict[str, Any]],
    state: str,
    max_links: int = 12,
) -> str:
    """Inject switchboard links for each offer (brand + bonus code)."""
    if not html_output or not offers:
        return html_output

    for offer in offers:
        brand = offer.get("brand", "")
        bonus_code = offer.get("bonus_code", "")
        switchboard_url = offer.get("switchboard_link", "")
        if not switchboard_url and offer.get("affiliate_id") and offer.get("campaign_id"):
            switchboard_url = build_switchboard_url(
                offer["affiliate_id"],
                offer["campaign_id"],
                state_code=state if state != "ALL" else "",
            )
        if not (brand and switchboard_url):
            continue
        html_output = inject_switchboard_links(
            html_output,
            brand=brand,
            bonus_code=bonus_code,
            switchboard_url=switchboard_url,
            max_links=max_links,
        )
    return html_output


def _build_signup_list(brand: str, has_code: bool, code_strong: str) -> str:
    """Build a deterministic 5-step signup list as HTML."""
    brand_label = brand or "the sportsbook"
    signup_link = f'<a href="#">{brand_label} sign-up guide</a>'
    bonus_link = '<a href="#">how bonus bets work</a>'

    step_two = (
        f"Create your account and enter {code_strong}."
        if has_code
        else "Create your account (no promo code required)."
    )

    steps = [
        f"Confirm you’re eligible (21+ in your state) and open {signup_link}.",
        step_two,
        "Complete verification and log in.",
        "Make your first deposit.",
        f"Place a qualifying bet and review {bonus_link} for payout details.",
    ]

    items = "\n".join(f"<li>{step}</li>" for step in steps)
    return f"<ol>\n{items}\n</ol>"


def _steps_to_html(steps: list[str]) -> str:
    items = "\n".join(f"<li>{step}</li>" for step in steps)
    return f"<ol>\n{items}\n</ol>"


def _offer_expiration_prompt_line(expiration_days: int | None) -> str:
    """Build a safe expiration prompt line for source-of-truth sections."""
    if expiration_days is None:
        return "- Expiration: Not provided (if needed, say \"see full terms\"; do not guess)"
    return f"- Expiration: {expiration_days} days (if mentioned, use exactly {expiration_days})"


def _format_offer_for_prompt(offer: dict[str, Any], state: str) -> str:
    """Format one offer as a compact source-of-truth row for prompts."""
    brand = str(offer.get("brand") or "[not provided]")
    offer_text = str(offer.get("offer_text") or offer.get("affiliate_offer") or "[not provided]")
    code = str(offer.get("bonus_code") or "No code required")
    terms = str(offer.get("terms") or "")
    expiration_days = offer.get("bonus_expiration_days")
    if expiration_days is None:
        expiration_days = extract_bonus_expiration_days(terms)
    min_odds = offer.get("minimum_odds") or extract_minimum_odds(terms)
    wagering = offer.get("wagering_requirement") or extract_wagering_requirement(terms)
    bonus_amount = offer.get("bonus_amount") or extract_bonus_amount(offer_text)
    states = offer.get("states_list") or offer.get("states") or state or "ALL"
    if isinstance(states, list):
        states_text = ", ".join(str(s) for s in states[:12]) if states else "ALL"
    else:
        states_text = str(states)
    expiration_text = (
        f"{expiration_days} days"
        if expiration_days is not None
        else "Not provided (use \"see full terms\")"
    )
    return (
        f"- Brand: {brand}\n"
        f"  Offer: {offer_text}\n"
        f"  Bonus Amount: {bonus_amount or '[not provided]'}\n"
        f"  Bonus Code: {code}\n"
        f"  Available in: {states_text}\n"
        f"  Expiration: {expiration_text}\n"
        f"  Minimum Odds: {min_odds if min_odds else '[see terms - do not guess]'}\n"
        f"  Wagering: {wagering if wagering else '[see terms - do not guess]'}"
    )


def _build_multi_offer_prompt_context(offers: list[dict[str, Any]], state: str) -> str:
    """Build source-of-truth prompt context for one or more offers."""
    normalized = [o for o in offers if o]
    if not normalized:
        return ""
    rows = [_format_offer_for_prompt(offer, state) for offer in normalized[:3]]
    return "\n".join(rows)


def _render_terms_section_html(
    *,
    terms: str,
    expiration_days: int | None,
    min_odds: str,
    wagering: str,
) -> str:
    """Render a deterministic terms section to avoid legal hallucinations."""
    if terms:
        cleaned = terms.replace("\\n", "\n")
        paras = [p.strip() for p in cleaned.splitlines() if p.strip()]
        if paras:
            return "\n".join(f"<p>{p}</p>" for p in paras)

    points: list[str] = []
    if expiration_days is not None:
        points.append(f"Bonus bets expire in {expiration_days} days.")
    if min_odds:
        points.append(f"Minimum odds requirement: {min_odds}.")
    if wagering:
        points.append(f"Wagering requirement: {wagering}.")
    points.append("See full terms at the operator site for complete eligibility and restrictions.")
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

    user_prompt = f"""Write a 5-step sign-up list for this promo.
Output JSON with a single key: "steps" (array of 5 strings).
Each step should be 1-2 sentences and plain language.

Brand: {brand}
Keyword: {keyword}
State: {state}
{code_line}

Include at least one internal link using HTML <a href=\"#\">anchor text</a>.
Available internal links (use 1-2):
{links_md}

Do NOT include responsible gaming disclaimers here.

STYLE GUIDE:
{style_guide}
"""

    try:
        data = await generate_completion_structured(
            prompt=user_prompt,
            system_prompt="You are a concise sports betting editor. Output only valid JSON.",
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


def _ensure_two_paragraphs(html: str, brand: str, offer_text: str, has_code: bool, code_strong: str, state: str) -> str:
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
        details = []
        if brand and offer_text:
            details.append(f"{brand} is offering {offer_text}.")
        if has_code:
            details.append(f"Enter the {code_strong} when you register.")
        else:
            details.append("No promo code is required.")
        if state and state != "ALL":
            details.append(f"Available in {state}.")
        second = " ".join(details) or "See full terms for eligibility and timing."

    return f"<p>{first}</p>\n<p>{second}</p>"


def _ensure_single_disclaimer(html: str, disclaimer: str) -> str:
    """Ensure the disclaimer appears only once at the end of the article."""
    if not disclaimer:
        return html
    pattern = rf"<p><em>{re.escape(disclaimer)}</em></p>\s*"
    cleaned = re.sub(pattern, "", html, flags=re.IGNORECASE)
    return cleaned.rstrip() + f"\n<p><em>{disclaimer}</em></p>"


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
    event_context: str = "",
    bet_example: str = "",
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
    switchboard_url = offer.get("switchboard_link", "")

    # Multi-offer support
    all_offers = [offer] + (alt_offers or []) if offer else (alt_offers or [])

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

    # Build switchboard URL if not provided
    if not switchboard_url and offer.get("affiliate_id") and offer.get("campaign_id"):
        switchboard_url = build_switchboard_url(
            offer["affiliate_id"],
            offer["campaign_id"],
            state_code=state if state != "ALL" else "",
        )

    parts = []
    parts.append(f"<h1>{title}</h1>")
    previous_content = ""
    keyword_count = 0
    target_keyword_total = 9
    seen_headings: set[str] = set()

    for section in outline:
        level = section.get("level", "h2")
        section_title = section.get("title", "")
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
            )
            parts.append(content)
            previous_content += content
            keyword_count += _count_keyword(content, keyword)

        elif level.startswith("shortcode"):
            current_offer = select_offer_for_shortcode(level)
            if current_offer:
                current_switchboard = current_offer.get("switchboard_link", "") or switchboard_url
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
                talking_points=talking_points,
                avoid=avoid,
                previous_content=previous_content,
                current_keyword_count=keyword_count,
                target_keyword_total=target_keyword_total,
                event_context=event_context,
                bet_example=bet_example,
            )
            tag = "h2" if level == "h2" else "h3"
            parts.append(f"<{tag}>{section_title}</{tag}>")
            parts.append(content)
            previous_content += f"\n{section_title}:\n{content}"
            keyword_count += _count_keyword(content, keyword)

    # Join and inject switchboard links
    html_output = "\n".join(parts)

    # Ensure single disclaimer at the end
    disclaimer = get_disclaimer_for_state(state)
    html_output = _ensure_single_disclaimer(html_output, disclaimer)

    html_output = _inject_switchboard_links_for_offers(
        html_output,
        offers=all_offers,
        state=state,
        max_links=2,
    )

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
) -> str:
    """Generate the intro/lede section.

    The intro should:
    1. Hook with a specific game/event if available (e.g., "Seahawks vs Patriots tonight at 6:30 PM ET on NBC")
    2. State the offer clearly with date
    3. Mention the promo code twice in plain text, but only wrap ONE natural anchor phrase in <strong> (used for switchboard links)
    4. State eligibility (21+, new users, states)
    """
    brand = offer.get("brand", "")
    offer_text = offer.get("offer_text", "")
    bonus_code = offer.get("bonus_code", "")
    terms = offer.get("terms", "")
    expiration_days = offer.get("bonus_expiration_days") or extract_bonus_expiration_days(terms)
    bonus_amount = offer.get("bonus_amount") or extract_bonus_amount(offer_text)
    expiration_line = _offer_expiration_prompt_line(expiration_days)
    date_str = today_long()
    style_guide = get_style_instructions()
    has_code = bool(bonus_code.strip())
    code_strong = f"<strong>{brand} bonus code {bonus_code}</strong>"
    link_anchor = f"<strong>{brand} promo code</strong>"
    prompt_offers = [o for o in (all_offers or []) if o] or [offer]
    has_multiple_offers = len(prompt_offers) > 1
    multi_offer_context = _build_multi_offer_prompt_context(prompt_offers, state)

    # Format talking points for prompt
    points_md = "\n".join(f"- {p}" for p in talking_points) if talking_points else ""

    system_prompt = """You are a PUNCHY sports betting writer for Action Network.
Write a 2-paragraph intro (lede) that sits between the H1 and H2.

TONE: Direct, confident, conversational. Like you're telling a friend about a deal.
- "Put the bet365 promo code to work for Seahawks vs Patriots tonight..."
- NOT "If you are looking for a valuable offer, consider bet365..."

Output clean HTML only - use <p>, <a>, <strong> tags. No markdown. No exclamation points."""

    # Build the intro hook based on context
    game_hook = ""
    if event_context:
        game_hook = f"GAME HOOK (use this to open):\n{event_context}\n\n"

    requirements = [
        f'If there is a game hook, START with it: "Put the {brand} Promo Code to work for [Game] tonight at [time] on [network], because..."',
        "If no game hook, start with a direct offer statement; avoid generic openers like \"If you are looking for a valuable offer...\"",
        "Do NOT include responsible gaming disclaimers here (handled at the end of the article).",
    ]
    if has_multiple_offers:
        requirements.append("This article includes multiple offers: mention at least two distinct offers naturally in the lede.")
    if has_code:
        requirements.extend([
            f"Mention the promo code value {bonus_code} twice in plain text.",
            f"Include ONE natural link anchor wrapped in <strong>, e.g., {link_anchor} or <strong>{brand} offer</strong>.",
            "Do NOT wrap every mention in <strong>.",
        ])
    else:
        requirements.append("Clearly state that no promo code is required. Do NOT invent a code. Do NOT wrap this in <strong>.")
    requirements.extend([
        "Keep sentences short and plain.",
        "Avoid legal or compliance language here.",
        "NO exclamation points anywhere",
        "Do NOT invent numbers not listed above. If unsure, say \"see full terms.\"",
    ])
    requirements_md = "\n".join(f"- {r}" for r in requirements)

    example_output = (
        f"<p>Put the {link_anchor} to work for [Game] tonight at [time] on [network], because {brand} is offering {offer_text} ahead of {date_str}.</p>"
        f"<p>Sign up, enter the promo code {bonus_code}, place your $5 bet, and you will get $200 in bonus bets whether your pick wins or loses.</p>"
    ) if has_code else (
        f"<p>Put the {brand} offer to work for [Game] tonight at [time] on [network], because {brand} is offering {offer_text} ahead of {date_str}.</p>"
        f"<p>No promo code is required to claim it; just sign up and place your first bet.</p>"
    )

    user_prompt = f"""Write the intro paragraph for this promo article:

DATE (include this): {date_str}

{game_hook}OFFER DETAILS:
- Brand: {brand}
- Offer: {offer_text}
- Bonus Code: {bonus_code or "No code required"}
- Bonus Amount: {bonus_amount or "See offer"}
- {expiration_line[2:]}
- State: {state}

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

    return _ensure_two_paragraphs(result, brand, offer_text, has_code, code_strong, state)


async def _generate_body_section(
    section_title: str,
    level: str,
    keyword: str,
    offer: dict,
    all_offers: list[dict[str, Any]] | None,
    state: str,
    talking_points: list[str],
    avoid: list[str],
    previous_content: str,
    current_keyword_count: int = 0,
    target_keyword_total: int = 9,
    event_context: str = "",
    bet_example: str = "",
) -> str:
    """Generate a body section (H2 or H3).

    Key improvements over generic prompts:
    1. Section-specific objectives (Overview vs How to Claim vs Eligibility)
    2. Worked bet examples with actual math in "How to Claim" sections
    3. Promo code mentions with <strong> tags for switchboard link injection
    4. Internal links that ADD context, not just link to other review pages
    """
    prompt_offers = [o for o in (all_offers or []) if o] or ([offer] if offer else [])
    primary_offer = offer or (prompt_offers[0] if prompt_offers else {})
    has_multiple_offers = len(prompt_offers) > 1
    brand = primary_offer.get("brand", "")
    offer_text = primary_offer.get("offer_text", "")
    bonus_code = primary_offer.get("bonus_code", "")
    terms = primary_offer.get("terms", "")
    bonus_amount = primary_offer.get("bonus_amount") or extract_bonus_amount(offer_text)
    expiration_days = primary_offer.get("bonus_expiration_days") or extract_bonus_expiration_days(terms)
    min_odds = primary_offer.get("minimum_odds") or extract_minimum_odds(terms)
    wagering = primary_offer.get("wagering_requirement") or extract_wagering_requirement(terms)
    expiration_line = _offer_expiration_prompt_line(expiration_days)
    multi_offer_context = _build_multi_offer_prompt_context(prompt_offers, state)
    style_guide = get_style_instructions()
    rag_guidance = get_rag_usage_guidance()
    has_code = bool(bonus_code.strip())
    code_strong = f"<strong>{brand} Promo Code {bonus_code}</strong>"
    link_anchor = f"<strong>{brand} promo code</strong>"
    code_requirement = (
        f"Mention the promo code value {bonus_code} at least once in plain text. "
        f"Include ONE natural <strong> anchor for linking, e.g., {link_anchor} or <strong>{brand} offer</strong>."
        if has_code
        else f"State clearly that no promo code is required (do not invent a code). "
             f"Include ONE natural <strong> anchor for linking, e.g., <strong>{brand} offer</strong>."
    )
    code_relevance = (
        f"If relevant, mention the promo code value {bonus_code} in plain text and include ONE <strong> anchor like {link_anchor}."
        if has_code
        else f"If relevant, note no promo code is required and include ONE <strong> anchor like <strong>{brand} offer</strong>."
    )
    if has_multiple_offers:
        code_requirement = (
            "When mentioning promo codes, use the correct brand/code pairing for each offer. "
            "Do not mix codes across sportsbooks."
        )
        code_relevance = (
            "If you reference multiple offers, keep each bonus code tied to the correct sportsbook."
        )
    step_two = (
        f"Create account and enter {code_strong}"
        if has_code
        else "Create account (no promo code required)"
    )
    claim_intro = (
        f'- "If I place a $50 moneyline bet on [Team] at [odds], I start by signing up and entering the {code_strong}."'
        if has_code
        else '- "If I place a $50 moneyline bet on [Team] at [odds], I start by signing up (no promo code required)."'
    )

    # Get RAG snippets for style (house articles for tone matching)
    try:
        snippets = await query_articles(f"{section_title} {keyword}", k=3, snippet_chars=400)
        style_examples = "\n\n".join([s.get("snippet", "") for s in snippets])[:1500]
    except Exception:
        style_examples = ""

    # Get internal links - these should be contextual guides, not other review pages
    try:
        links = await suggest_links_for_section(section_title, [keyword, brand], k=3)
        links_md = format_links_markdown(links, brand=brand)
    except Exception:
        links_md = "(no links available)"

    # Format talking points
    points_md = "\n".join(f"- {p}" for p in talking_points) if talking_points else ""

    # Format avoid list
    avoid_md = "\n".join(f"- {a}" for a in avoid) if avoid else ""

    # Anti-repetition phrases
    blacklisted_phrases = _extract_common_phrases(previous_content)
    blacklisted_md = "\n".join(f"- {p}" for p in blacklisted_phrases) if blacklisted_phrases else ""

    # Determine section type and specific objective
    title_lower = section_title.lower()
    is_signup = _is_signup_heading(title_lower)
    is_how_to_claim = _is_claim_heading(title_lower, is_signup)
    is_numbered_list = is_signup
    is_overview = any(x in title_lower for x in ["overview", "what is", "about"])
    is_eligibility = any(x in title_lower for x in ["eligibility", "key details", "requirements"])
    is_terms = any(x in title_lower for x in ["terms", "conditions", "fine print"])
    if not is_how_to_claim:
        bet_example = ""

    if is_terms:
        return _render_terms_section_html(
            terms=terms,
            expiration_days=expiration_days,
            min_odds=min_odds,
            wagering=wagering,
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
        )
        if steps:
            return _steps_to_html(steps)
        return _build_signup_list(brand, has_code, code_strong)

    system_prompt = """You are a PUNCHY sports betting editor for Action Network's Top Stories.

TONE: Direct, confident, conversational. Like explaining to a friend.
- "Here's how it works: place $5 on the Bills moneyline, and whether it hits or not..."
- NOT "The offer provides new users with an opportunity to..."

Output well-structured HTML paragraphs. Be compliant but NOT boring.
NO markdown syntax. NO exclamation points. NO corporate-speak.
Follow the STYLE GUIDE provided in the prompt."""

    # Build section-specific instructions
    if is_how_to_claim:
        bet_example = bet_example or ""
        section_objective = f"""SECTION OBJECTIVE: Provide a WORKED EXAMPLE with actual dollar amounts.

CRITICAL: This section must include a first-person bet example with math:
{claim_intro}
- "If my bet wins at +120, I profit $60 and get back my $50 stake, so I cash out $110 total."
- "If it loses, I am down $50 on the bet, but I still receive [bonus amount] in bonus bets."
- Then show how to use the bonus bets: "If I put $200 in bonus bets on [another pick] at -110 and it wins, the payout is profit-only: $200 × (100/110) = $181.82"

Use the bet example provided if available, or create one using the event context."""
    elif is_overview:
        section_objective = f"""SECTION OBJECTIVE: Explain why this offer matters and what makes it valuable.

Focus on:
- WHO this offer is good for (bettors who want low-commitment entry, etc.)
- WHEN to use it (timing - bonus bets expire in X days, packed schedule, etc.)
- {code_requirement}

Do NOT include step-by-step instructions (that's in How to Claim)."""
    elif is_eligibility:
        section_objective = f"""SECTION OBJECTIVE: Cover essential eligibility and requirements BRIEFLY.

Focus on:
- 21+ and new customer requirement
- Eligible states
- Minimum odds requirement (e.g., -500 or longer)
- Bonus bet expiration (e.g., 7 days)

{code_requirement}
Keep it SHORT - do NOT restate the full offer (already covered)."""
    elif is_numbered_list:
        section_objective = f"""SECTION OBJECTIVE: Step-by-step registration instructions.

Output as a numbered <ol> list with exactly 5 steps:
1. Confirm eligibility (21+, state, open app/website)
2. {step_two}
3. Complete registration and verify identity
4. Make deposit
5. Place first bet and receive bonus

Each step should be 1-2 sentences. Include relevant internal links."""
    else:
        section_objective = f"""SECTION OBJECTIVE: Write helpful content under this heading.

{code_relevance}
Do NOT repeat information from previous sections."""

    user_prompt = f"""Write the content for this section:

SECTION TITLE: {section_title}

{section_objective}

=== SOURCE OF TRUTH - DO NOT DEVIATE ===
These are exact offer details. Do NOT invent or modify numbers.
{multi_offer_context}
RULE: If a detail is not provided, say "see full terms" instead of guessing.
=== END SOURCE OF TRUTH ===

{f"MULTI-OFFER RULES:{chr(10)}- This article includes {len(prompt_offers)} offers.{chr(10)}- Mention at least two distinct offers in overview or key-details style sections.{chr(10)}- Keep brand/code pairings correct for every mention.{chr(10)}" if has_multiple_offers else ""}

{"BET EXAMPLE DATA (use this for the worked example):" + chr(10) + bet_example + chr(10) if bet_example else ""}
{"EVENT CONTEXT (use for bet examples):" + chr(10) + event_context + chr(10) if event_context else ""}

OFFER CONTEXT:
- Brand: {brand}
- Offer: {offer_text}
- Bonus Code: {bonus_code or "No code required"}
- State: {state}
- {expiration_line[2:]}

{"TALKING POINTS:" + chr(10) + points_md + chr(10) if points_md else ""}
{"DO NOT COVER (handled elsewhere):" + chr(10) + avoid_md + chr(10) if avoid_md else ""}

INTERNAL LINKS TO WEAVE IN (MUST use at least 2; use placeholder anchors like [bet365 sign-up guide]):
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
- {"MUST" if current_keyword_count < target_keyword_total else "SHOULD"} include the exact phrase "{keyword}" at least once in this section.
- Use it naturally; avoid repetition.

PREVIOUSLY WRITTEN (do NOT repeat this content):
{previous_content[-1500:] if previous_content else "(first section)"}

{"PHRASES TO AVOID (overused):" + chr(10) + blacklisted_md if blacklisted_md else ""}

DO NOT add responsible gaming disclaimers in this section (handled at the end).

FORMAT: {"Numbered <ol> list with 5 <li> items" if is_numbered_list else "2-3 <p> paragraphs"}

Write the section now (HTML only, no heading, no markdown):"""

    result = await generate_completion(
        prompt=user_prompt,
        system_prompt=system_prompt,
        temperature=get_temperature_by_section(level),
        max_tokens=800,
    )

    result = result.strip()

    if is_numbered_list:
        li_count = len(re.findall(r"<li\b", result, flags=re.IGNORECASE))
        if li_count < 5:
            result = _build_signup_list(brand, has_code, code_strong)

    return result


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
    event_context: str = "",
    bet_example: str = "",
    output_format: str = "html",
) -> AsyncGenerator[dict, None]:
    """Generate draft with streaming updates.

    Yields dicts: {type: 'status'|'content'|'done', ...}
    """
    brand = offer.get("brand", "")
    bonus_code = offer.get("bonus_code", "")
    switchboard_url = offer.get("switchboard_link", "")

    all_offers = [offer] + (alt_offers or []) if offer else (alt_offers or [])

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

    if not switchboard_url and offer.get("affiliate_id") and offer.get("campaign_id"):
        switchboard_url = build_switchboard_url(
            offer["affiliate_id"],
            offer["campaign_id"],
            state_code=state if state != "ALL" else "",
        )

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
        section_title = section.get("title", "")
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
            )
            parts.append(content)
            previous_content += content
            keyword_count += _count_keyword(content, keyword)
            yield {"type": "content", "section": "intro", "content": content}

        elif level.startswith("shortcode"):
            current_offer = select_offer_for_shortcode(level)
            if current_offer:
                current_switchboard = current_offer.get("switchboard_link", "") or switchboard_url
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
                talking_points=talking_points,
                avoid=avoid,
                previous_content=previous_content,
                current_keyword_count=keyword_count,
                target_keyword_total=target_keyword_total,
                event_context=event_context,
                bet_example=bet_example,
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
    disclaimer = get_disclaimer_for_state(state)
    html_output = _ensure_single_disclaimer(html_output, disclaimer)
    yield {"type": "content", "section": "footer", "content": f"<p><em>{disclaimer}</em></p>"}
    all_offers = [offer] + (alt_offers or []) if offer else (alt_offers or [])
    html_output = _inject_switchboard_links_for_offers(
        html_output,
        offers=all_offers,
        state=state,
        max_links=2,
    )

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
