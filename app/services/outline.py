"""Outline generation service.

Generates article outlines using RAG context and LLM.
"""

import re
from datetime import datetime
from typing import AsyncGenerator

from app.services.llm import generate_completion, generate_completion_streaming
from app.services.rag import query_articles
from app.services.compliance import get_disclaimer_for_state


# Default outline structure if parsing fails
DEFAULT_OUTLINE = [
    "[INTRO]",
    "[SHORTCODE]",
    "[H2: What is the Offer?]",
    "[H2: How to Claim]",
    "[H3: Step 1: Sign Up]",
    "[H3: Step 2: Make a Deposit]",
    "[H3: Step 3: Place Your Bet]",
    "[H2: Tips and Strategies]",
    "[H2: Frequently Asked Questions]",
]


def parse_outline_tokens(text: str) -> list[str]:
    """Parse outline text into token list.

    Tokens are lines matching: [INTRO], [SHORTCODE], [H2: Title], [H3: Title]
    """
    tokens = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue

        # Match bracket tokens
        if re.match(r"^\[(?:INTRO|SHORTCODE|H[23]:\s*.+)\]$", line, re.IGNORECASE):
            tokens.append(line)
        # Also accept lines that look like tokens without brackets
        elif re.match(r"^(INTRO|SHORTCODE)$", line, re.IGNORECASE):
            tokens.append(f"[{line.upper()}]")
        elif re.match(r"^H[23]:\s*.+$", line, re.IGNORECASE):
            tokens.append(f"[{line}]")

    return tokens if tokens else DEFAULT_OUTLINE


def tokens_to_text(tokens: list[str]) -> str:
    """Convert token list to editable text format."""
    return "\n".join(tokens)


async def generate_outline(
    keyword: str,
    title: str,
    offer_text: str = "",
    brand: str = "",
    state: str = "ALL",
    competitor_context: str = "",
    style_profile: str = "Top Stories – Informative",
) -> list[str]:
    """Generate article outline using RAG and LLM.

    Returns list of outline tokens.
    """
    # Get RAG context
    rag_snippets = await query_articles(keyword, k=6, snippet_chars=800)
    rag_context = "\n\n".join([
        f"[{s['source']}]: {s['snippet']}"
        for s in rag_snippets
    ]) or "(No relevant articles found)"

    # Build prompt
    system_prompt = """You are an SEO content planner specializing in sports betting promotional content.
Your task is to create a structured article outline using bracket tokens.

Output format (one token per line):
[INTRO] - Opening paragraph hook
[SHORTCODE] - Promo module placement
[H2: Section Title] - Main sections
[H3: Subsection Title] - Subsections under H2s

Rules:
- Start with [INTRO] then [SHORTCODE]
- Use 3-5 H2 sections
- Use H3 subsections sparingly (0-3 per H2)
- Keep titles concise and keyword-relevant
- Include an FAQ section at the end
- Output ONLY the tokens, no explanations"""

    user_prompt = f"""Create an article outline for:

KEYWORD: {keyword}
TITLE: {title}
BRAND: {brand or "(none)"}
OFFER: {offer_text or "(none)"}
STATE: {state}
STYLE: {style_profile}

REFERENCE ARTICLES (for structure inspiration):
{rag_context}

{f"COMPETITOR CONTEXT:{chr(10)}{competitor_context}" if competitor_context else ""}

Generate the outline tokens now:"""

    # Generate
    response = await generate_completion(
        prompt=user_prompt,
        system_prompt=system_prompt,
        temperature=0.3,
        max_tokens=1000,
    )

    # Parse tokens
    tokens = parse_outline_tokens(response)
    return tokens


async def generate_outline_streaming(
    keyword: str,
    title: str,
    offer_text: str = "",
    brand: str = "",
    state: str = "ALL",
    competitor_context: str = "",
    style_profile: str = "Top Stories – Informative",
) -> AsyncGenerator[dict, None]:
    """Generate outline with streaming updates.

    Yields dicts: {type: 'status'|'token'|'done', ...}
    """
    yield {"type": "status", "message": "Querying article database..."}

    # Get RAG context
    rag_snippets = await query_articles(keyword, k=6, snippet_chars=800)
    rag_context = "\n\n".join([
        f"[{s['source']}]: {s['snippet']}"
        for s in rag_snippets
    ]) or "(No relevant articles found)"

    yield {"type": "status", "message": f"Found {len(rag_snippets)} relevant articles"}

    # Build prompt (same as non-streaming)
    system_prompt = """You are an SEO content planner specializing in sports betting promotional content.
Your task is to create a structured article outline using bracket tokens.

Output format (one token per line):
[INTRO] - Opening paragraph hook
[SHORTCODE] - Promo module placement
[H2: Section Title] - Main sections
[H3: Subsection Title] - Subsections under H2s

Rules:
- Start with [INTRO] then [SHORTCODE]
- Use 3-5 H2 sections
- Use H3 subsections sparingly (0-3 per H2)
- Keep titles concise and keyword-relevant
- Include an FAQ section at the end
- Output ONLY the tokens, no explanations"""

    user_prompt = f"""Create an article outline for:

KEYWORD: {keyword}
TITLE: {title}
BRAND: {brand or "(none)"}
OFFER: {offer_text or "(none)"}
STATE: {state}
STYLE: {style_profile}

REFERENCE ARTICLES (for structure inspiration):
{rag_context}

{f"COMPETITOR CONTEXT:{chr(10)}{competitor_context}" if competitor_context else ""}

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

            if line and re.match(r"^\[(?:INTRO|SHORTCODE|H[23]:\s*.+)\]$", line, re.IGNORECASE):
                tokens_found.append(line)
                yield {"type": "token", "content": line}

    # Check remaining buffer
    if buffer.strip():
        line = buffer.strip()
        if re.match(r"^\[(?:INTRO|SHORTCODE|H[23]:\s*.+)\]$", line, re.IGNORECASE):
            tokens_found.append(line)
            yield {"type": "token", "content": line}

    # Use defaults if nothing found
    final_tokens = tokens_found if tokens_found else DEFAULT_OUTLINE
    yield {"type": "done", "outline": final_tokens}


def today_long() -> str:
    """Get today's date in long format for intro."""
    return datetime.now().strftime("%A, %B %d, %Y")
