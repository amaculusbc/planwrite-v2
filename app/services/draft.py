"""Draft generation service.

Expands outline tokens into full article draft.
"""

import re
from datetime import datetime
from typing import AsyncGenerator, Any

from app.services.llm import generate_completion, generate_completion_streaming
from app.services.rag import query_articles
from app.services.internal_links import suggest_links_for_section, format_links_markdown
from app.services.compliance import get_disclaimer_for_state
from app.services.bam_offers import render_bam_offer_block
from app.services.style import format_constraints_for_prompt, get_rag_usage_guidance


def today_long() -> str:
    """Get today's date in long format."""
    return datetime.now().strftime("%A, %B %d, %Y")


def parse_token(token: str) -> dict:
    """Parse a token into its components.

    Returns: {type: 'intro'|'shortcode'|'h2'|'h3', title: str}
    """
    token = token.strip()

    if token.upper() == "[INTRO]":
        return {"type": "intro", "title": "Introduction"}

    if token.upper() == "[SHORTCODE]":
        return {"type": "shortcode", "title": "Promo Module"}

    h2_match = re.match(r"\[H2:\s*(.+)\]", token, re.IGNORECASE)
    if h2_match:
        return {"type": "h2", "title": h2_match.group(1).strip()}

    h3_match = re.match(r"\[H3:\s*(.+)\]", token, re.IGNORECASE)
    if h3_match:
        return {"type": "h3", "title": h3_match.group(1).strip()}

    return {"type": "unknown", "title": token}


async def generate_intro(
    keyword: str,
    title: str,
    brand: str,
    offer_text: str,
    state: str,
    style_profile: str,
) -> str:
    """Generate the intro/lede paragraph."""
    date_str = today_long()

    system_prompt = """You are an SEO news writer. Write a 2-3 sentence intro (lede) that sits
between the H1 and first H2. It should be factual, concise, and compliant."""

    user_prompt = f"""DATE: {date_str}

TITLE: {title}
KEYWORD: {keyword}
BRAND: {brand or "(none)"}
OFFER: {offer_text or "(none)"}
STATE: {state}
STYLE: {style_profile}

RULES:
- Start with the provided date (weekday, month day, year)
- Mention the brand and promo succinctly if relevant; no CTA link
- One short paragraph; 2-3 sentences; no subheadings
- Neutral tone, no guaranteed outcomes; include state caveat if necessary

Write the intro paragraph:"""

    return await generate_completion(
        prompt=user_prompt,
        system_prompt=system_prompt,
        temperature=0.3,
        max_tokens=300,
    )


async def generate_section(
    heading_title: str,
    heading_level: str,
    keyword: str,
    brand: str,
    offer_text: str,
    bonus_code: str,
    state: str,
    style_profile: str,
    allow_cta: bool = True,
) -> str:
    """Generate content for a single H2 or H3 section."""

    # Get RAG snippets for STYLE reference (not facts)
    query = f"{heading_title} {keyword}"
    snippets = await query_articles(query, k=4, snippet_chars=600)
    snippets_md = "\n\n".join([
        f"[{s['source']}]: {s['snippet']}"
        for s in snippets
    ]) or "(none)"

    # Get style constraints
    style_constraints = format_constraints_for_prompt(style_profile)
    rag_guidance = get_rag_usage_guidance()

    # Get internal link suggestions
    links = await suggest_links_for_section(heading_title, [keyword], k=3)
    links_md = format_links_markdown(links)

    # Get disclaimer
    disclaimer = get_disclaimer_for_state(state)

    # Build rules
    rules = [
        "Begin directly under the heading with helpful, concise copy (no fluff).",
        "Match the TONE and STYLE of the example snippets, but write original content.",
        "Use only the provided internal links where they fit naturally; do not invent URLs.",
        "Maintain a neutral, compliant tone; avoid implying guaranteed outcomes.",
        "No tables, no HTML except the supplied CTA block (if any).",
        "Do NOT print or restate the heading; write paragraphs only.",
    ]

    if not allow_cta:
        rules.append("Do NOT include any CTA; a promo block is inserted elsewhere.")
    else:
        rules.append("Include at most one brief CTA sentence if it serves the reader.")

    system_prompt = """You are an SEO editor writing short, timely Top Stories sections for U.S. sports betting.
Your output must be well-structured Markdown paragraphs, compliant, and scannable.

CRITICAL: The "WRITING STYLE EXAMPLES" section shows HOW we write (tone, structure, readability).
Do NOT copy facts or claims from those examples. Write original content matching our style."""

    user_prompt = f"""WRITE UNDER THIS HEADING EXACTLY (DO NOT PRINT THE HEADING):
{heading_title}

STYLE CONSTRAINTS:
{style_constraints}

BRAND/OFFER CONTEXT (use only if relevant; no hard sell):
- Brand: {brand or "(none)"}
- Offer: {offer_text or "(none)"}
- Bonus code: {bonus_code or "(none)"}

INTERNAL LINKS (weave in if natural; 0-3 max):
{links_md}

WRITING STYLE EXAMPLES (match tone/structure, NOT facts):
{rag_guidance}

{snippets_md}

DISCLAIMER (append if required):
{disclaimer}

RULES:
- {chr(10).join('- ' + r for r in rules)}

Write the section content:"""

    return await generate_completion(
        prompt=user_prompt,
        system_prompt=system_prompt,
        temperature=0.3,
        max_tokens=800,
    )


async def generate_draft(
    outline_tokens: list[str],
    keyword: str,
    title: str,
    offer: dict[str, Any] | None = None,
    state: str = "ALL",
    style_profile: str = "Top Stories – Informative",
) -> str:
    """Generate full article draft from outline tokens.

    Returns complete markdown article.
    """
    offer = offer or {}
    brand = offer.get("brand", "")
    offer_text = offer.get("offer_text", "")
    bonus_code = offer.get("bonus_code", "")

    parts = [f"# {title}\n"]

    for token in outline_tokens:
        parsed = parse_token(token)
        token_type = parsed["type"]
        section_title = parsed["title"]

        if token_type == "intro":
            intro = await generate_intro(
                keyword=keyword,
                title=title,
                brand=brand,
                offer_text=offer_text,
                state=state,
                style_profile=style_profile,
            )
            parts.append(intro.strip() + "\n")

        elif token_type == "shortcode":
            if offer:
                block = render_bam_offer_block(offer)
                parts.append(block + "\n")
            else:
                parts.append("> [Promo module placeholder]\n")

        elif token_type == "h2":
            content = await generate_section(
                heading_title=section_title,
                heading_level="h2",
                keyword=keyword,
                brand=brand,
                offer_text=offer_text,
                bonus_code=bonus_code,
                state=state,
                style_profile=style_profile,
                allow_cta=True,
            )
            parts.append(f"\n## {section_title}\n\n{content.strip()}\n")

        elif token_type == "h3":
            content = await generate_section(
                heading_title=section_title,
                heading_level="h3",
                keyword=keyword,
                brand=brand,
                offer_text=offer_text,
                bonus_code=bonus_code,
                state=state,
                style_profile=style_profile,
                allow_cta=False,
            )
            parts.append(f"\n### {section_title}\n\n{content.strip()}\n")

    # Add final disclaimer
    disclaimer = get_disclaimer_for_state(state)
    parts.append(f"\n---\n\n*{disclaimer}*\n")

    return "\n".join(parts)


async def generate_draft_streaming(
    outline_tokens: list[str],
    keyword: str,
    title: str,
    offer: dict[str, Any] | None = None,
    state: str = "ALL",
    style_profile: str = "Top Stories – Informative",
) -> AsyncGenerator[dict, None]:
    """Generate draft with streaming updates.

    Yields dicts: {type: 'status'|'content'|'done', ...}
    """
    offer = offer or {}
    brand = offer.get("brand", "")
    offer_text = offer.get("offer_text", "")
    bonus_code = offer.get("bonus_code", "")

    draft_parts = [f"# {title}\n"]
    total = len(outline_tokens)

    yield {"type": "status", "message": f"Processing {total} sections..."}
    yield {"type": "content", "section": "title", "content": f"# {title}\n\n"}

    for i, token in enumerate(outline_tokens):
        parsed = parse_token(token)
        token_type = parsed["type"]
        section_title = parsed["title"]

        yield {"type": "status", "message": f"Generating section {i+1}/{total}: {section_title}"}

        if token_type == "intro":
            intro = await generate_intro(
                keyword=keyword,
                title=title,
                brand=brand,
                offer_text=offer_text,
                state=state,
                style_profile=style_profile,
            )
            content = intro.strip() + "\n\n"
            draft_parts.append(content)
            yield {"type": "content", "section": token, "content": content}

        elif token_type == "shortcode":
            if offer:
                block = render_bam_offer_block(offer)
            else:
                block = "> [Promo module placeholder]\n"
            content = block + "\n"
            draft_parts.append(content)
            yield {"type": "content", "section": token, "content": content}

        elif token_type == "h2":
            section_content = await generate_section(
                heading_title=section_title,
                heading_level="h2",
                keyword=keyword,
                brand=brand,
                offer_text=offer_text,
                bonus_code=bonus_code,
                state=state,
                style_profile=style_profile,
                allow_cta=True,
            )
            content = f"\n## {section_title}\n\n{section_content.strip()}\n"
            draft_parts.append(content)
            yield {"type": "content", "section": token, "content": content}

        elif token_type == "h3":
            section_content = await generate_section(
                heading_title=section_title,
                heading_level="h3",
                keyword=keyword,
                brand=brand,
                offer_text=offer_text,
                bonus_code=bonus_code,
                state=state,
                style_profile=style_profile,
                allow_cta=False,
            )
            content = f"\n### {section_title}\n\n{section_content.strip()}\n"
            draft_parts.append(content)
            yield {"type": "content", "section": token, "content": content}

    # Final disclaimer
    disclaimer = get_disclaimer_for_state(state)
    footer = f"\n---\n\n*{disclaimer}*\n"
    draft_parts.append(footer)
    yield {"type": "content", "section": "footer", "content": footer}

    full_draft = "\n".join(draft_parts)
    yield {"type": "done", "draft": full_draft, "word_count": len(full_draft.split())}
