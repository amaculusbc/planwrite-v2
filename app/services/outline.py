"""Outline generation service (Plan stage).

Generates structured outlines with unique talking points per section.
Writers can review and modify the outline before draft generation (Execute stage).
"""

import hashlib
import json
import re
from datetime import datetime
from typing import Any, AsyncGenerator
from zoneinfo import ZoneInfo

from app.services.llm import (
    generate_completion,
    generate_completion_streaming,
    generate_completion_structured,
)
from app.services.rag import query_articles
from app.services.content_guidelines import get_style_instructions, get_temperature_by_section
from app.services.operator_profile import (
    CONTENT_MODE_DFS,
    CONTENT_MODE_PREDICTION_MARKET,
    get_content_mode_context,
)


OUTLINE_SECTION_SCHEMA = {
    "type": "object",
    "properties": {
        "level": {
            "type": "string",
            "enum": ["intro", "shortcode", "h2", "h3"],
        },
        "title": {"type": "string"},
        "talking_points": {
            "type": "array",
            "items": {"type": "string"},
        },
        "avoid": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["level", "title", "talking_points", "avoid"],
    "additionalProperties": False,
}


OUTLINE_SCHEMA = {
    "type": "object",
    "properties": {
        "outline": {
            "type": "array",
            "items": OUTLINE_SECTION_SCHEMA,
            "minItems": 3,
        }
    },
    "required": ["outline"],
    "additionalProperties": False,
}


def today_long(tz: str = "US/Eastern") -> str:
    """Get today's date in long format."""
    try:
        now = datetime.now(ZoneInfo(tz))
    except Exception:
        now = datetime.now()
    return f"{now.strftime('%A')}, {now.strftime('%B')} {now.day}, {now.year}"


def _extract_matchup_from_event_context(event_context: str) -> str:
    """Extract matchup text from event context string when available."""
    if not event_context:
        return ""

    featured = re.search(r"Featured game:\s*([^\.]+)", event_context, flags=re.IGNORECASE)
    if featured:
        raw = featured.group(1).strip()
    else:
        featured_event = re.search(r"Featured event:\s*([^\.]+)", event_context, flags=re.IGNORECASE)
        if featured_event:
            return re.sub(r"\s+", " ", featured_event.group(1).strip()).strip()
        direct = re.search(
            r"([A-Za-z0-9 .'\-]+)\s+(?:vs\.?|@)\s+([A-Za-z0-9 .'\-]+)",
            event_context,
            flags=re.IGNORECASE,
        )
        if not direct:
            return ""
        raw = f"{direct.group(1).strip()} vs. {direct.group(2).strip()}"

    if "@" in raw:
        parts = [p.strip() for p in raw.split("@", 1)]
        if len(parts) == 2 and parts[0] and parts[1]:
            raw = f"{parts[0]} vs. {parts[1]}"

    return re.sub(r"\s+", " ", raw).strip()


def _headline_topic(
    keyword: str,
    brand: str,
    is_prediction_market: bool = False,
    is_dfs: bool = False,
) -> str:
    """Build normalized topic phrase for headings."""
    if keyword and keyword.strip():
        return keyword.strip()
    if brand and brand.strip():
        brand_clean = brand.strip()
        if brand_clean.lower() == "bet365":
            return f"{brand_clean} bonus code"
        return f"{brand_clean} promo code"
    if is_prediction_market:
        return "prediction market promo code"
    if is_dfs:
        return "DFS promo code"
    return "sportsbook promo code"


_MULTI_WORD_TEAM_ENDINGS = {
    "red sox",
    "white sox",
    "blue jays",
    "trail blazers",
    "tar heels",
    "fighting irish",
    "golden eagles",
    "mean green",
    "hilltoppers",
    "mountain hawks",
    "ragin cajuns",
    "sun devils",
}


def _short_team_label(name: str) -> str:
    """Condense full team names for editorial headings."""
    clean = re.sub(r"\s+", " ", str(name or "").strip())
    if not clean:
        return ""
    parts = clean.split(" ")
    if len(parts) == 1:
        return clean
    last_two = " ".join(parts[-2:]).lower()
    if last_two in _MULTI_WORD_TEAM_ENDINGS:
        return " ".join(parts[-2:])
    return parts[-1]


def _compact_matchup_label(matchup: str) -> str:
    """Shorten team matchups while preserving non-team events."""
    raw = re.sub(r"\s+", " ", str(matchup or "").strip())
    if not raw:
        return ""
    if not re.search(r"\s+(?:vs\.?|@)\s+", raw, flags=re.IGNORECASE):
        return raw
    parts = re.split(r"\s+(?:vs\.?|@)\s+", raw, maxsplit=1, flags=re.IGNORECASE)
    if len(parts) != 2:
        return raw
    left, right = [part.strip() for part in parts]
    if not left or not right:
        return raw
    short_left = _short_team_label(left)
    short_right = _short_team_label(right)
    if short_left and short_right:
        return f"{short_left} vs. {short_right}"
    return raw


def _is_claim_title(title_lower: str) -> bool:
    """Return True if the heading already functions as the claim/example section."""
    if not title_lower:
        return False
    return bool(re.search(
        r"(^using\b.*\b(code|offer|bonus)\b)|\b(how to claim|claim|how to use|worked example|bet example|quick example|example)\b",
        title_lower,
    ))


def _is_signup_title(title_lower: str) -> bool:
    """Return True if the heading already functions as the sign-up section."""
    if not title_lower:
        return False
    return bool(re.search(
        r"\b(sign ?up|sign-up|signup|register|registration|create an? account|open an? account|"
        r"get started|how to sign|how to register|how to join)\b",
        title_lower,
    ))


def _is_terms_title(title_lower: str) -> bool:
    """Return True if the heading already functions as the terms/fine print section."""
    if not title_lower:
        return False
    return bool(re.search(r"\b(terms|conditions|fine print|house rules)\b", title_lower))


def _classify_h2_section(title_lower: str) -> str:
    """Classify editorial H2s so duplicate semantic sections can be removed."""
    if _is_daily_promos_title(title_lower):
        return "daily_promos"
    if _is_signup_title(title_lower):
        return "signup"
    if _is_terms_title(title_lower):
        return "terms"
    if _is_claim_title(title_lower):
        return "claim"
    return "other"


def _contextual_section_titles(
    keyword: str,
    brand: str,
    event_context: str = "",
    is_prediction_market: bool = False,
    is_dfs: bool = False,
) -> dict[str, str]:
    """Build contextual section titles (game-specific when context is available)."""
    topic = _headline_topic(keyword, brand, is_prediction_market=is_prediction_market, is_dfs=is_dfs)
    matchup = _extract_matchup_from_event_context(event_context)
    title_matchup = _compact_matchup_label(matchup)
    seed = "|".join([
        topic.lower(),
        brand.lower(),
        title_matchup.lower(),
        "pm" if is_prediction_market else "dfs" if is_dfs else "sportsbook",
    ])

    def choose(slot: str, options: list[str]) -> str:
        if not options:
            return ""
        digest = hashlib.sha1(f"{seed}|{slot}".encode("utf-8")).hexdigest()
        return options[int(digest[:8], 16) % len(options)]

    if title_matchup:
        if is_prediction_market:
            overview_options = [
                f"Why {topic} fits {title_matchup}",
                f"Best Market Angle for {title_matchup}",
                f"What Stands Out for {title_matchup}",
                f"Where {topic} fits on {title_matchup}",
            ]
            claim_options = [
                f"How to Use the Offer for {title_matchup}",
                f"How to Use the Offer on {title_matchup}",
                f"How to Use the Offer Around {title_matchup}",
            ]
        elif is_dfs:
            overview_options = [
                f"Why {topic} works for {title_matchup}",
                f"Best DFS Angle for {title_matchup}",
                f"What to Know About {title_matchup}",
                f"Where {topic} fits on this slate",
            ]
            claim_options = [
                f"How to Use the Offer for {title_matchup}",
                f"How to Use the Offer on this slate",
                f"How to Use the Offer before {title_matchup}",
            ]
        else:
            overview_options = [
                f"Why {topic} is worth a look for {title_matchup}",
                f"Best Angle for {title_matchup}",
                f"What Stands Out for {title_matchup}",
                f"How {topic} fits {title_matchup}",
            ]
            claim_options = [
                f"Worked Example for {title_matchup}",
                f"How the Bonus Bets Play Out for {title_matchup}",
                f"Example: Turning the Offer Into Extra Bets for {title_matchup}",
                f"What the Welcome Offer Looks Like on {title_matchup}",
            ]
        signup_options = [
            f"How to Sign Up Before {title_matchup}",
            f"Sign-Up Steps Before {title_matchup}",
            f"How to Get Started Before {title_matchup}",
        ]
    else:
        if is_prediction_market:
            overview_options = [
                f"Why {topic} is worth a look today",
                f"{topic}: Best Market Angle Today",
                f"What to Know About {topic} right now",
                f"Where {topic} fits today",
            ]
            claim_options = [
                "How to Use the Offer for Any Market",
                "How to Use the Offer Today",
                "How to Use the Offer Across Top Markets",
            ]
        elif is_dfs:
            overview_options = [
                f"Why {topic} is worth a look today",
                f"{topic}: Best DFS Angle Today",
                f"What to Know About {topic} right now",
                f"Where {topic} fits today",
            ]
            claim_options = [
                "How to Use the Offer for Any Slate",
                "How to Use the Offer Today",
                "How to Use the Offer Across Top Slates",
            ]
        else:
            overview_options = [
                f"Why {topic} is worth a look today",
                f"{topic}: Best Angle Right Now",
                f"What to Know About {topic} today",
                f"Where {topic} fits today",
            ]
            claim_options = [
                "Worked Example: Offer in Action",
                "How the Bonus Bets Play Out Today",
                "Example: Turning the Offer Into Extra Bets",
                "What the Welcome Offer Looks Like Today",
            ]
        signup_options = [
            f"How to Sign Up for {topic}",
            f"Sign-Up Steps for {topic}",
            f"How to Get Started with {topic}",
        ]

    daily_promos_options = [
        "Daily Promos Placeholder",
        "Daily Promos Today",
        "Today's Promo Placeholder",
        "Promo Update Placeholder",
    ]
    if is_prediction_market:
        terms_options = [
            "Market Terms & Settlement",
            "Market Rules & Settlement",
            "Offer Terms & Market Rules",
        ]
    elif is_dfs:
        terms_options = [
            "Contest Terms & Rules",
            "DFS Terms & Rules",
            "Contest Rules & Fine Print",
        ]
    else:
        terms_options = [
            "Terms & Conditions",
            "Offer Terms & Conditions",
            "Fine Print & Offer Terms",
        ]

    return {
        "overview": choose("overview", overview_options),
        "claim": choose("claim", claim_options),
        "signup": choose("signup", signup_options),
        "daily_promos": choose("daily_promos", daily_promos_options),
        "terms": choose("terms", terms_options),
    }


def _is_daily_promos_title(title_lower: str) -> bool:
    """Return True for any daily-promo placeholder title variant."""
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


def _apply_editorial_section_rules(
    outline: list[dict],
    keyword: str,
    brand: str,
    event_context: str = "",
    is_prediction_market: bool = False,
    is_dfs: bool = False,
) -> list[dict]:
    """Apply house section rules: remove redundant key-details, enforce daily promos."""
    if not outline:
        return outline

    titles = _contextual_section_titles(
        keyword,
        brand,
        event_context,
        is_prediction_market=is_prediction_market,
        is_dfs=is_dfs,
    )
    cleaned: list[dict] = []
    has_daily_promos = False
    has_claim = False
    has_signup = False
    has_terms = False
    first_h2_idx = -1

    for section in outline:
        level = str(section.get("level", ""))
        title = str(section.get("title", ""))
        title_lower = title.lower()

        if level == "h2" and ("key details" in title_lower or "eligibility" in title_lower):
            # Redundant section: these details are covered in intro/claim/terms.
            continue

        normalized = dict(section)
        if level == "h2":
            if first_h2_idx == -1:
                first_h2_idx = len(cleaned)
                normalized["title"] = titles["overview"]
                cleaned.append(normalized)
                continue

            section_kind = _classify_h2_section(title_lower)
            if section_kind == "claim":
                if has_claim:
                    continue
                normalized["title"] = titles["claim"]
                has_claim = True
            elif section_kind == "daily_promos":
                if has_daily_promos:
                    continue
                normalized["title"] = titles["daily_promos"]
                has_daily_promos = True
            elif section_kind == "signup":
                if has_signup:
                    continue
                normalized["title"] = titles["signup"]
                has_signup = True
            elif section_kind == "terms":
                if has_terms:
                    continue
                normalized["title"] = titles["terms"]
                has_terms = True

        cleaned.append(normalized)

    if not has_claim:
        cleaned.append({
            "level": "h2",
            "title": titles["claim"],
            "talking_points": [
                "Worked example with contract settlement outcomes"
                if is_prediction_market
                else "Worked example with contest entry outcomes"
                if is_dfs
                else "Worked example with win/loss outcomes",
                "How promo credits apply to eligible market positions"
                if is_prediction_market
                else "How bonus entries or contest credits are applied"
                if is_dfs
                else "How bonus credits are applied",
            ],
            "avoid": ["Rewriting legal terms"],
        })

    if not has_daily_promos:
        daily_section = {
            "level": "h2",
            "title": titles["daily_promos"],
            "talking_points": [
                "Placeholder only for today's rotating promos",
                "List operator, code, and eligible states"
                if is_prediction_market
                else "List DFS app, code, and eligible states"
                if is_dfs
                else "List sportsbook, code, and eligible states",
                "Update this section daily before publishing",
            ],
            "avoid": ["Using stale promos from prior days"],
        }
        insert_idx = next(
            (
                idx for idx, sec in enumerate(cleaned)
                if str(sec.get("level", "")) == "h2"
                and (
                    _is_signup_title(str(sec.get("title", "")).lower())
                    or _is_terms_title(str(sec.get("title", "")).lower())
                )
            ),
            len(cleaned),
        )
        cleaned.insert(insert_idx, daily_section)

    if not has_signup:
        cleaned.append({
            "level": "h2",
            "title": titles["signup"],
            "talking_points": [
                "Five-step registration flow",
                "Where to enter promo code",
                "How to place first qualifying market position"
                if is_prediction_market
                else "How to enter the first qualifying contest"
                if is_dfs
                else "How to place first qualifying bet",
            ],
            "avoid": ["Deep legal terms"],
        })

    if not has_terms:
        cleaned.append({
            "level": "h2",
            "title": titles["terms"],
            "talking_points": [
                "Reference official operator terms"
                if not is_prediction_market and not is_dfs
                else "Reference official market terms"
                if is_prediction_market
                else "Reference official contest rules and app terms",
                "State restrictions and settlement timelines"
                if is_prediction_market
                else "State restrictions and contest/entry expiry windows"
                if is_dfs
                else "State restrictions and expiry windows",
            ],
            "avoid": ["Repeating claim walkthrough"],
        })

    return cleaned


# ============================================================================
# STRUCTURED OUTLINE (New Plan-Execute System)
# ============================================================================

async def generate_structured_outline(
    keyword: str,
    title: str,
    offer: dict[str, Any],
    event_context: str = "",
    bet_example: str = "",
    competitor_context: str = "",
) -> list[dict]:
    """Generate a structured outline with unique talking points per section.

    This is the PLAN stage - it creates an editable outline that writers
    can review and modify before draft generation.

    Args:
        keyword: Primary keyword (e.g., "bet365 promo code")
        title: Article title (H1)
        offer: Offer dict from BAM API
        event_context: Game/event context if applicable
        bet_example: Pre-built bet example text
        competitor_context: Scraped competitor content

    Returns:
        List of section dicts with:
        - level: "intro", "shortcode", "h2", "h3"
        - title: Section heading (empty for intro/shortcode)
        - talking_points: List of unique points to cover
        - avoid: List of things NOT to include (covered elsewhere)
    """
    brand = offer.get("brand", "")
    offer_text = offer.get("offer_text", "")
    bonus_code = offer.get("bonus_code", "")
    terms = offer.get("terms", "")
    content_mode = get_content_mode_context(keyword, title, brand, offer_text)
    is_prediction_market = content_mode == CONTENT_MODE_PREDICTION_MARKET
    is_dfs = content_mode == CONTENT_MODE_DFS
    style_guide = get_style_instructions()
    section_titles = _contextual_section_titles(
        keyword,
        brand,
        event_context,
        is_prediction_market=is_prediction_market,
        is_dfs=is_dfs,
    )

    # Get RAG snippets for style reference
    try:
        hits = await query_articles(keyword, k=5, snippet_chars=600)
        rag_context = "\n\n".join([h.get("snippet", "") for h in hits if h.get("snippet")])[:3000]
    except Exception:
        rag_context = ""

    publication_label = (
        "prediction market"
        if is_prediction_market
        else "daily fantasy"
        if is_dfs
        else "sports betting"
    )
    language_rule = (
        "Use prediction-market language (trade, market, position, contract) and avoid sportsbook/bet/wager terms"
        if is_prediction_market
        else "Use DFS language (entries, contests, picks, fantasy app) and avoid sportsbook/bet/wager terms"
        if is_dfs
        else "Use natural sportsbook language with clear, factual mechanics"
    )

    system_prompt = f"""You are a senior content strategist for a {publication_label} publication.
Your job is to create a DETAILED CONTENT PLAN for a promo code article.

CRITICAL: Each section must have UNIQUE talking points. Never repeat information across sections.
The outline you create will be reviewed by human writers who may modify it.
Do not mirror the H1 with boilerplate H2s. Avoid section titles that are just the keyword plus the matchup.

Output a structured outline in this exact JSON format:
{{
  "outline": [
    {{"level": "intro", "title": "", "talking_points": ["point 1", "point 2"], "avoid": []}},
    {{"level": "shortcode", "title": "", "talking_points": [], "avoid": []}},
    {{"level": "h2", "title": "Section Title", "talking_points": ["unique point 1", "unique point 2"], "avoid": ["thing covered elsewhere"]}}
  ]
}}

RULES:
- INTRO: 2-3 talking points about the hook, date, and offer value
- SHORTCODE: Place after intro, between major sections, and before sign-up
- H2 sections: Each needs 2-4 UNIQUE talking points
- H3 subsections: Only when genuinely helpful, 1-2 talking points
- "avoid" lists what other sections cover (to prevent repetition)
- Maximum 5 H2 sections total
- The first H2 should mention the topic naturally, but should not repeat the H1 wording
- Do not create a standalone "Key Details" or "Eligibility" section
- {language_rule}"""

    claim_point = (
        f"Use this worked example: {bet_example}"
        if bet_example
        else "Create a hypothetical worked example using a $50-100 market position"
        if is_prediction_market
        else "Create a hypothetical worked example using a $50-100 DFS contest entry"
        if is_dfs
        else "Create hypothetical bet example with $50-100 wager"
    )
    signup_step_five = (
        "5. Place first qualifying market position"
        if is_prediction_market
        else "5. Enter first qualifying contest"
        if is_dfs
        else "5. Place bet"
    )
    terms_point = (
        "Reference official terms, eligibility, and settlement notes"
        if is_prediction_market
        else "Reference official app terms, contest rules, and eligibility notes"
        if is_dfs
        else "Full T&C reference, responsible gaming, state helpline"
    )
    overview_point = (
        "Why it's valuable, market timing angle, who benefits (NOT claiming steps)"
        if is_prediction_market
        else "Why it's valuable for DFS players, contest flexibility, who benefits (NOT sign-up steps)"
        if is_dfs
        else "Why it's valuable, timing advantage, who benefits (NOT claiming steps)"
    )
    intro_point = (
        "Should mention date, offer value, and explicit eligible states (not generic \"nationwide\")"
        if is_prediction_market
        else "Should mention date, offer value, promo code if required, and explicit eligible states (not generic \"nationwide\")"
        if is_dfs
        else "Should mention date, offer value, whether a code is needed, and explicit eligible states (not generic \"nationwide\")"
    )
    daily_promos_point = (
        "Use placeholder bullets only for editor updates (operator, code, offer, states)"
        if is_prediction_market
        else "Use placeholder bullets only for editor updates (DFS app, code, offer, states)"
        if is_dfs
        else "Use placeholder bullets only for editor updates (book, code, offer, states)"
    )
    competitor_research_goal = (
        "COMPETITOR RESEARCH GOAL:\n"
        "- Use the research below to avoid cloning competitor headings or flow.\n"
        "- Pick at least one angle or heading style that is materially different from competitors.\n"
        "- Do not reuse competitor wording verbatim.\n"
        if competitor_context
        else ""
    )

    user_prompt = f"""Create a detailed content plan for this article:

KEYWORD: {keyword}
TITLE: {title}
DATE: {today_long()}

OFFER DETAILS:
- Brand: {brand}
- Offer: {offer_text}
- Bonus Code: {bonus_code}
- Terms excerpt: {terms[:500] if terms else "See operator site"}

{"EVENT CONTEXT: " + event_context if event_context else ""}
{"BET EXAMPLE AVAILABLE: " + bet_example[:200] + "..." if bet_example else ""}

COMPETITOR RESEARCH:
{competitor_context[:2000] if competitor_context else "(none provided)"}

STYLE GUIDE (follow for tone/structure):
{style_guide}

STYLE EXAMPLES (match this tone):
{rag_context or "(none available)"}

{competitor_research_goal}

REQUIRED STRUCTURE:
1. [INTRO] - Hook with date, offer value, promo code mention
2. [SHORTCODE] - Promo card
3. [H2: {section_titles["overview"]}] - Why this offer matters (NOT how to claim)
4. [SHORTCODE]
5. [H2: {section_titles["claim"]}] - Worked example with calculations
6. [SHORTCODE]
7. [H2: {section_titles["daily_promos"]}] - Placeholder section only for daily promo updates
8. [H2: {section_titles["signup"]}] - Step-by-step numbered list
9. [H2: {section_titles["terms"]}] - Fine print summary

TALKING POINTS GUIDANCE:
- INTRO: {intro_point}
- OVERVIEW: {overview_point}
- HOW TO CLAIM: {claim_point}
- DAILY PROMOS: {daily_promos_point}
- SIGN UP: Numbered steps (1. Go to site 2. Register 3. Enter code 4. Deposit {signup_step_five})
- TERMS: {terms_point}

HEADING RULES:
- Every H2 must feel editorially distinct from the H1
- Avoid generic headings like "Key Details & Eligibility"
- Avoid headings that are just "{keyword}" plus the event label
- Vary the framing across articles so the outline does not look templated

Output ONLY the JSON object, no other text:"""

    try:
        data = await generate_completion_structured(
            prompt=user_prompt,
            system_prompt=system_prompt,
            schema=OUTLINE_SCHEMA,
            name="article_outline",
            description="Structured outline for a promo article",
            temperature=get_temperature_by_section("outline"),
            max_tokens=2000,
        )
        outline = data.get("outline", []) if isinstance(data, dict) else []
        outline = _ensure_shortcodes(outline)
        outline = _apply_editorial_section_rules(
            outline,
            keyword=keyword,
            brand=brand,
            event_context=event_context,
            is_prediction_market=is_prediction_market,
            is_dfs=is_dfs,
        )
        return outline
    except Exception as e:
        print(f"Failed to generate structured outline: {e}")

    # Fallback to default structure
    return _get_default_outline(
        keyword,
        brand,
        event_context,
        bet_example,
        is_prediction_market=is_prediction_market,
        is_dfs=is_dfs,
    )


def _ensure_shortcodes(outline: list[dict]) -> list[dict]:
    """Ensure shortcodes are present: after intro, then every 2 H2s.

    This fixes LLM outputs that forget to include [SHORTCODE] tokens.
    """
    if not outline:
        return outline

    result = []
    h2_count_since_shortcode = 0
    has_intro = False

    for i, section in enumerate(outline):
        level = section.get("level", "")

        if level == "intro":
            result.append(section)
            has_intro = True
            # Always add shortcode after intro
            if i + 1 < len(outline) and outline[i + 1].get("level") != "shortcode":
                result.append({"level": "shortcode", "title": "", "talking_points": [], "avoid": []})
                h2_count_since_shortcode = 0
        elif level == "shortcode":
            result.append(section)
            h2_count_since_shortcode = 0
        elif level == "h2":
            # Add shortcode before this H2 if we've had 2+ H2s since last shortcode
            if h2_count_since_shortcode >= 2:
                result.append({"level": "shortcode", "title": "", "talking_points": [], "avoid": []})
                h2_count_since_shortcode = 0
            result.append(section)
            h2_count_since_shortcode += 1
        else:
            # h3 or other
            result.append(section)

    # Ensure we have intro at start if missing
    if not has_intro and result and result[0].get("level") != "intro":
        result.insert(0, {
            "level": "intro",
            "title": "",
            "talking_points": ["Hook with date and offer value", "Mention the code only if one is required"],
            "avoid": [],
        })
        result.insert(1, {"level": "shortcode", "title": "", "talking_points": [], "avoid": []})

    return result


def _get_default_outline(
    keyword: str,
    brand: str,
    event_context: str = "",
    bet_example: str = "",
    is_prediction_market: bool = False,
    is_dfs: bool = False,
) -> list[dict]:
    """Return default outline structure if AI generation fails."""
    titles = _contextual_section_titles(
        keyword,
        brand,
        event_context,
        is_prediction_market=is_prediction_market,
        is_dfs=is_dfs,
    )
    default_example_point = (
        "Worked example with a $50 market position"
        if is_prediction_market
        else "Worked example with a $50 DFS contest entry"
        if is_dfs
        else "Worked example with $50 bet"
    )
    return [
        {
            "level": "intro",
            "title": "",
            "talking_points": [
                f"Hook with today's date and {brand} offer value",
                "Mention the code naturally if one is required",
                "State explicit eligible states without calling the offer nationwide",
            ],
            "avoid": [],
        },
        {
            "level": "shortcode",
            "title": "",
            "talking_points": [],
            "avoid": [],
        },
        {
            "level": "h2",
            "title": titles["overview"],
            "talking_points": [
                "Why this offer is valuable for bettors"
                if not is_prediction_market and not is_dfs
                else "Why this offer is valuable for prediction-market users"
                if is_prediction_market
                else "Why this offer is valuable for DFS players",
                "Timing advantage (sign up now)",
                "What makes it stand out from other promos",
            ],
            "avoid": ["Step-by-step claiming instructions", "Full terms details", "Repeating the H1 wording"],
        },
        {
            "level": "shortcode",
            "title": "",
            "talking_points": [],
            "avoid": [],
        },
        {
            "level": "h2",
            "title": titles["claim"],
            "talking_points": [
                bet_example if bet_example else default_example_point,
                "Show win scenario with profit calculation"
                if not is_prediction_market and not is_dfs
                else "Show settlement scenario with payout calculation"
                if is_prediction_market
                else "Show contest outcome example and payout logic",
                "Show loss scenario with bonus bet receipt"
                if not is_prediction_market and not is_dfs
                else "Show loss scenario and how promo credits can be used"
                if is_prediction_market
                else "Show non-cash outcome and how bonus entries/credits apply",
            ],
            "avoid": ["Restating what the offer is", "Eligibility requirements"],
        },
        {
            "level": "shortcode",
            "title": "",
            "talking_points": [],
            "avoid": [],
        },
        {
            "level": "h2",
            "title": titles["daily_promos"],
            "talking_points": [
                "Placeholder only for today's rotating promos (editor updates daily)",
                "List sportsbook, offer, code, and state availability"
                if not is_prediction_market and not is_dfs
                else "List operator, offer, code, and state availability"
                if is_prediction_market
                else "List DFS app, offer, code, and state availability",
                "Do not prefill this section with live promo copy",
            ],
            "avoid": ["Using stale promos from previous days"],
        },
        {
            "level": "h2",
            "title": titles["signup"],
            "talking_points": [
                "Step 1: Visit site/app",
                "Step 2: Click Join/Register",
                "Step 3: Enter promo code",
                "Step 4: Complete verification",
                "Step 5: Make deposit and place first bet"
                if not is_prediction_market and not is_dfs
                else "Step 5: Fund account and place first market position"
                if is_prediction_market
                else "Step 5: Fund account and enter first contest",
            ],
            "avoid": ["Offer details", "Terms explanation"],
        },
        {
            "level": "h2",
            "title": titles["terms"],
            "talking_points": [
                "Reference to full terms on operator site",
                "Key restrictions summary",
                "Responsible gaming reminder with helpline"
                if not is_prediction_market and not is_dfs
                else "Eligibility and settlement notes"
                if is_prediction_market
                else "Eligibility, contest rules, and expiration notes",
            ],
            "avoid": ["Repeating eligibility copy from above", "Claiming steps"],
        },
    ]


def outline_to_text(outline: list[dict]) -> str:
    """Convert structured outline to editable text format.

    Format:
    [INTRO]
    > Hook with date and offer value
    > Mention promo code twice

    [SHORTCODE]

    [H2: Section Title]
    > Talking point 1
    > Talking point 2
    ! Avoid: thing covered elsewhere

    Args:
        outline: Structured outline from generate_structured_outline

    Returns:
        Editable text representation
    """
    lines = []

    for section in outline:
        level = section.get("level", "h2")
        title = section.get("title", "")
        talking_points = section.get("talking_points", [])
        avoid = section.get("avoid", [])

        # Section header
        if level == "intro":
            lines.append("[INTRO]")
        elif str(level).startswith("shortcode"):
            token = str(level).upper()
            if token == "SHORTCODE":
                lines.append("[SHORTCODE]")
            else:
                lines.append(f"[{token}]")
        elif level in ("h2", "h3"):
            lines.append(f"[{level.upper()}: {title}]")

        # Talking points
        for point in talking_points:
            lines.append(f"> {point}")

        # Avoid list
        if avoid:
            lines.append(f"! Avoid: {', '.join(avoid)}")

        lines.append("")  # Blank line between sections

    return "\n".join(lines).strip()


def text_to_outline(text: str) -> list[dict]:
    """Parse editable text format back to structured outline.

    Args:
        text: Text from outline_to_text (possibly edited by user)

    Returns:
        Structured outline list
    """
    outline = []
    current_section = None

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue

        # Check for section headers
        intro_match = re.match(r"^\[INTRO\]$", line, re.IGNORECASE)
        shortcode_match = re.match(r"^\[(SHORTCODE(?:_[A-Z0-9]+)?)\]$", line, re.IGNORECASE)
        h2_match = re.match(r"^\[H2:\s*(.+)\]$", line, re.IGNORECASE)
        h3_match = re.match(r"^\[H3:\s*(.+)\]$", line, re.IGNORECASE)

        if intro_match:
            if current_section:
                outline.append(current_section)
            current_section = {
                "level": "intro",
                "title": "",
                "talking_points": [],
                "avoid": [],
            }
        elif shortcode_match:
            if current_section:
                outline.append(current_section)
            shortcode_level = shortcode_match.group(1).lower()
            current_section = {
                "level": shortcode_level,
                "title": "",
                "talking_points": [],
                "avoid": [],
            }
        elif h2_match:
            if current_section:
                outline.append(current_section)
            current_section = {
                "level": "h2",
                "title": h2_match.group(1).strip(),
                "talking_points": [],
                "avoid": [],
            }
        elif h3_match:
            if current_section:
                outline.append(current_section)
            current_section = {
                "level": "h3",
                "title": h3_match.group(1).strip(),
                "talking_points": [],
                "avoid": [],
            }
        elif line.startswith(">") and current_section:
            # Talking point
            point = line[1:].strip()
            if point:
                current_section["talking_points"].append(point)
        elif line.startswith("!") and current_section:
            # Avoid directive
            avoid_match = re.match(r"^!\s*Avoid:\s*(.+)$", line, re.IGNORECASE)
            if avoid_match:
                avoid_items = [a.strip() for a in avoid_match.group(1).split(",")]
                current_section["avoid"].extend(avoid_items)

    # Don't forget last section
    if current_section:
        outline.append(current_section)

    return outline


def validate_outline(outline: list[dict], keyword: str) -> list[str]:
    """Validate outline structure and content.

    Args:
        outline: Structured outline
        keyword: Primary keyword

    Returns:
        List of warning/error messages (empty if valid)
    """
    warnings = []

    # Count section types
    h2_count = sum(1 for s in outline if s.get("level") == "h2")
    shortcode_count = sum(1 for s in outline if str(s.get("level", "")).startswith("shortcode"))
    has_intro = any(s.get("level") == "intro" for s in outline)

    # Structure checks
    if not has_intro:
        warnings.append("Missing [INTRO] section")

    if h2_count < 3:
        warnings.append(f"Only {h2_count} H2 sections (recommend 4-5)")
    elif h2_count > 6:
        warnings.append(f"Too many H2 sections ({h2_count}) - recommend max 5")

    if shortcode_count < 2:
        warnings.append("Consider adding more [SHORTCODE] placements for CTAs")

    # Keyword in headings check
    keyword_lower = keyword.lower()
    h2_titles = [s.get("title", "").lower() for s in outline if s.get("level") == "h2"]

    if h2_titles and keyword_lower not in h2_titles[0]:
        warnings.append(f"First H2 should contain keyword '{keyword}'")

    keyword_in_h2s = sum(1 for t in h2_titles if keyword_lower in t)
    if keyword_in_h2s < 2:
        warnings.append(f"Keyword '{keyword}' only in {keyword_in_h2s} H2 titles (recommend 3+)")

    # Talking points check
    for section in outline:
        if section.get("level") in ("h2", "h3"):
            points = section.get("talking_points", [])
            if len(points) < 2:
                warnings.append(f"Section '{section.get('title', 'Untitled')}' has too few talking points")

    return warnings


# ============================================================================
# LEGACY TOKEN-BASED OUTLINE (for backward compatibility)
# ============================================================================

DEFAULT_TOKENS = [
    "[INTRO]",
    "[SHORTCODE]",
    "[H2: Promo Code Overview]",
    "[SHORTCODE]",
    "[H2: How to Claim the Promo Code]",
    "[H2: Daily Promos Today]",
    "[H2: How to Sign Up]",
    "[H2: Terms & Conditions]",
]


def _default_tokens_multi(num_offers: int = 1, keyword: str = "Offer") -> list[str]:
    """Build a lean default token set with multi-offer shortcodes."""
    main_shortcode = "[SHORTCODE_MAIN]" if num_offers > 1 else "[SHORTCODE]"
    tokens = [
        "[INTRO]",
        main_shortcode,
        f"[H2: {keyword} Overview]",
        main_shortcode,
    ]
    if num_offers > 1:
        tokens.append("[SHORTCODE_1]")
    if num_offers > 2:
        tokens.append("[SHORTCODE_2]")
    tokens.extend([
        f"[H2: How to Claim the {keyword}]",
        main_shortcode,
        "[H2: Daily Promos Today]",
        f"[H2: How to Sign Up for {keyword}]",
        "[H2: Terms & Conditions]",
    ])
    return tokens


def parse_outline_tokens(text: str, default_shortcode_token: str = "[SHORTCODE]") -> list[str]:
    """Parse outline text into token list (legacy format).

    Tokens are lines matching: [INTRO], [SHORTCODE], [H2: Title], [H3: Title]
    Always ensures [INTRO] and [SHORTCODE] are present at the start.
    """
    tokens = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue

        # Match bracket tokens
        if re.match(r"^\[(?:INTRO|SHORTCODE(?:_[A-Z0-9]+)?|H[23]:\s*.+)\]$", line, re.IGNORECASE):
            tokens.append(line)
        # Also accept lines that look like tokens without brackets
        elif re.match(r"^(INTRO|SHORTCODE(?:_[A-Z0-9]+)?)$", line, re.IGNORECASE):
            tokens.append(f"[{line.upper()}]")
        elif re.match(r"^H[23]:\s*.+$", line, re.IGNORECASE):
            tokens.append(f"[{line}]")

    if not tokens:
        if default_shortcode_token and default_shortcode_token != "[SHORTCODE]":
            return _default_tokens_multi(num_offers=2, keyword="Offer")
        return DEFAULT_TOKENS

    # Ensure [INTRO] and [SHORTCODE] are at the start
    has_intro = any(t.upper() == "[INTRO]" for t in tokens)
    has_shortcode = any(t.upper().startswith("[SHORTCODE") for t in tokens)

    if not has_intro:
        tokens.insert(0, "[INTRO]")
    if not has_shortcode:
        # Insert after INTRO
        intro_idx = next((i for i, t in enumerate(tokens) if t.upper() == "[INTRO]"), -1)
        tokens.insert(intro_idx + 1, default_shortcode_token)

    return _reposition_alt_shortcodes(tokens)


def _reposition_alt_shortcodes(tokens: list[str]) -> list[str]:
    """Place alt shortcodes immediately after the first shortcode token."""
    alt_tokens = [t for t in tokens if t.upper() in ("[SHORTCODE_1]", "[SHORTCODE_2]")]
    if not alt_tokens:
        return tokens

    tokens = [t for t in tokens if t.upper() not in ("[SHORTCODE_1]", "[SHORTCODE_2]")]

    insert_idx = next((i for i, t in enumerate(tokens) if t.upper().startswith("[SHORTCODE")), -1)
    if insert_idx == -1:
        intro_idx = next((i for i, t in enumerate(tokens) if t.upper() == "[INTRO]"), -1)
        insert_idx = intro_idx

    for offset, tok in enumerate(alt_tokens):
        tokens.insert(insert_idx + 1 + offset, tok)

    return tokens


def tokens_to_text(tokens: list[str]) -> str:
    """Convert token list to editable text format (legacy)."""
    return "\n".join(tokens)


def structured_to_tokens(outline: list[dict]) -> list[str]:
    """Convert structured outline to legacy token format.

    Args:
        outline: Structured outline list

    Returns:
        List of token strings
    """
    tokens = []
    for section in outline:
        level = section.get("level", "h2")
        title = section.get("title", "")

        if level == "intro":
            tokens.append("[INTRO]")
        elif str(level).startswith("shortcode"):
            tokens.append(f"[{str(level).upper()}]")
        elif level in ("h2", "h3"):
            tokens.append(f"[{level.upper()}: {title}]")

    return tokens


async def generate_outline(
    keyword: str,
    title: str,
    offer_text: str = "",
    brand: str = "",
    state: str = "ALL",
    competitor_context: str = "",
    style_profile: str = "Top Stories – Informative",
    game_context: str = "",
    num_offers: int = 1,
) -> list[str]:
    """Generate article outline using RAG and LLM (legacy format).

    Returns list of outline tokens.
    """
    # Get RAG context
    rag_snippets = await query_articles(keyword, k=6, snippet_chars=800)
    rag_context = "\n\n".join([
        f"[{s['source']}]: {s['snippet']}"
        for s in rag_snippets
    ]) or "(No relevant articles found)"
    content_mode = get_content_mode_context(keyword, title, brand, offer_text)
    is_prediction_market = content_mode == CONTENT_MODE_PREDICTION_MARKET
    is_dfs = content_mode == CONTENT_MODE_DFS
    section_titles = _contextual_section_titles(
        keyword,
        brand,
        game_context,
        is_prediction_market=is_prediction_market,
        is_dfs=is_dfs,
    )

    # Build prompt - keep it tight to avoid bloated outlines
    language_rule = (
        "Use prediction-market language and avoid sportsbook/betting terms"
        if is_prediction_market
        else "Use DFS language and avoid sportsbook/betting terms"
        if is_dfs
        else "Use clear sportsbook language"
    )
    system_prompt = f"""You are an SEO content planner for short, timely Top Stories promo articles.
Output a lean outline using bracket tokens. One item per line.

Format:
[INTRO]
[SHORTCODE] or [SHORTCODE_MAIN]/[SHORTCODE_1]/[SHORTCODE_2]
[H2: ...]
[H3: ...]  (only when needed under the preceding H2)

CRITICAL RULES:
- ALWAYS start with [INTRO] then [SHORTCODE]
- Use 3-4 H2 sections MAX (these are short articles, 600-800 words)
- Use H3 sparingly - only 1-2 per H2 if needed
- Insert [SHORTCODE_MAIN] 2-3 times total throughout (after intro, mid-article)
- Keep headings SHORT (under 8 words)
- NO "Benefits" or "Features" sections - focus on the offer
- Output ONLY tokens, no explanations
- {language_rule}"""

    user_prompt = f"""Create an outline for:

KEYWORD: {keyword}
TITLE: {title}
BRAND: {brand or "(none)"}
OFFER: {offer_text or "(none)"}
{f"GAME: {game_context}" if game_context else ""}

REQUIRED STRUCTURE (follow this pattern):
[INTRO]
[SHORTCODE_MAIN]
[H2: {section_titles['overview']}]
[H2: {section_titles['claim']}]
[H3: Example: (offer summary)]
[SHORTCODE_MAIN]
[H2: {section_titles['daily_promos']}]
[H2: {section_titles['signup']}]
[SHORTCODE_MAIN]
[H2: {section_titles['terms']}]

If multiple offers are selected, also include:
- [SHORTCODE_1] for the first alternative offer
- [SHORTCODE_2] for the second alternative offer
You have {num_offers} total offer(s).

Adjust headings to match the keyword. Output tokens now:"""

    # Generate
    response = await generate_completion(
        prompt=user_prompt,
        system_prompt=system_prompt,
        temperature=0.3,
        max_tokens=1000,
    )

    # Parse tokens
    shortcode_token = "[SHORTCODE_MAIN]" if num_offers > 1 else "[SHORTCODE]"
    tokens = parse_outline_tokens(response, default_shortcode_token=shortcode_token)
    return tokens if tokens else _default_tokens_multi(num_offers=num_offers, keyword=keyword or "Offer")


async def generate_outline_streaming(
    keyword: str,
    title: str,
    offer_text: str = "",
    brand: str = "",
    state: str = "ALL",
    competitor_context: str = "",
    style_profile: str = "Top Stories – Informative",
    num_offers: int = 1,
) -> AsyncGenerator[dict, None]:
    """Generate outline with streaming updates (legacy format).

    Yields dicts: {type: 'status'|'token'|'done', ...}
    """
    yield {"type": "status", "message": "Querying article database..."}

    # Get RAG context
    rag_snippets = await query_articles(keyword, k=6, snippet_chars=800)
    rag_context = "\n\n".join([
        f"[{s['source']}]: {s['snippet']}"
        for s in rag_snippets
    ]) or "(No relevant articles found)"
    content_mode = get_content_mode_context(keyword, title, brand, offer_text)
    is_prediction_market = content_mode == CONTENT_MODE_PREDICTION_MARKET
    is_dfs = content_mode == CONTENT_MODE_DFS
    section_titles = _contextual_section_titles(
        keyword,
        brand,
        is_prediction_market=is_prediction_market,
        is_dfs=is_dfs,
    )

    yield {"type": "status", "message": f"Found {len(rag_snippets)} relevant articles"}

    # Build prompt (same as non-streaming)
    publication_label = (
        "prediction-market"
        if is_prediction_market
        else "DFS"
        if is_dfs
        else "sports betting"
    )
    language_rule = (
        "Avoid sportsbook/betting terminology for this operator"
        if is_prediction_market or is_dfs
        else "Use natural sportsbook terminology"
    )
    system_prompt = f"""You are an SEO content planner specializing in {publication_label} promotional content.
Your task is to create a structured article outline using bracket tokens.

Output format (one token per line):
[INTRO] - Opening paragraph hook
[SHORTCODE] - Promo module placement (or [SHORTCODE_MAIN]/[SHORTCODE_1]/[SHORTCODE_2])
[H2: Section Title] - Main sections
[H3: Subsection Title] - Subsections under H2s

Rules:
- Start with [INTRO] then [SHORTCODE]
- Use 4-5 H2 sections
- Use H3 subsections sparingly (0-3 per H2)
- Keep titles concise, contextual, and keyword-relevant
- Include a Daily Promos section placeholder
- Output ONLY the tokens, no explanations
- {language_rule}"""

    user_prompt = f"""Create an article outline for:

KEYWORD: {keyword}
TITLE: {title}
BRAND: {brand or "(none)"}
OFFER: {offer_text or "(none)"}
STATE: {state}
STYLE: {style_profile}

REQUIRED STRUCTURE (follow this pattern):
[INTRO]
[SHORTCODE_MAIN]
[H2: {section_titles['overview']}]
[H2: {section_titles['claim']}]
[SHORTCODE_MAIN]
[H2: {section_titles['daily_promos']}]
[H2: {section_titles['signup']}]
[H2: {section_titles['terms']}]

STRUCTURE EXAMPLES (use for outline format inspiration, NOT content):
These show how we typically structure similar articles.
{rag_context}

{f"COMPETITOR CONTEXT:{chr(10)}{competitor_context}" if competitor_context else ""}

You have {num_offers} total offer(s). If more than 1, include [SHORTCODE_1] and [SHORTCODE_2] tokens.

Generate the outline tokens now:"""

    yield {"type": "status", "message": "Generating outline..."}

    # Stream the response
    buffer = ""
    tokens_found = []

    async for chunk in generate_completion_streaming(
        prompt=user_prompt,
        system_prompt=system_prompt,
        temperature=0.3,
        max_tokens=1000,
    ):
        buffer += chunk

        # Check for complete lines
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            line = line.strip()

            if line and re.match(r"^\[(?:INTRO|SHORTCODE(?:_[A-Z0-9]+)?|H[23]:\s*.+)\]$", line, re.IGNORECASE):
                tokens_found.append(line)
                yield {"type": "token", "content": line}

    # Check remaining buffer
    if buffer.strip():
        line = buffer.strip()
        if re.match(r"^\[(?:INTRO|SHORTCODE(?:_[A-Z0-9]+)?|H[23]:\s*.+)\]$", line, re.IGNORECASE):
            tokens_found.append(line)
            yield {"type": "token", "content": line}

    # Use defaults if nothing found
    final_tokens = tokens_found if tokens_found else _default_tokens_multi(num_offers=num_offers, keyword=keyword or "Offer")
    final_tokens = _reposition_alt_shortcodes(final_tokens)
    yield {"type": "done", "outline": final_tokens}
