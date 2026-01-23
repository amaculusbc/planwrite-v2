"""Style constraints and writing guidelines.

Provides style profiles and constraints for content generation.
The RAG layer provides writing STYLE examples, not factual content.
"""

from typing import Any

# Style profile definitions
STYLE_PROFILES = {
    "Top Stories – Informative": {
        "reading_level": "Grade 8-10",
        "paragraph_target_sentences": "2-4",
        "list_density": "low",
        "tone": "clear, direct, non-hype, informative",
        "voice": "second person (you/your)",
        "avoid": [
            "superlatives without evidence",
            "guaranteed outcome language",
            "aggressive sales tactics",
            "clickbait phrasing",
        ],
        "include": [
            "specific details about the offer",
            "step-by-step instructions where appropriate",
            "responsible gambling reminder",
        ],
    },
    "News – Breaking": {
        "reading_level": "Grade 7-9",
        "paragraph_target_sentences": "1-3",
        "list_density": "medium",
        "tone": "urgent, factual, concise",
        "voice": "third person neutral",
        "avoid": [
            "speculation",
            "editorial opinion",
            "promotional language",
        ],
        "include": [
            "who, what, when, where",
            "attribution of sources",
            "latest updates first",
        ],
    },
    "Guide – Educational": {
        "reading_level": "Grade 9-11",
        "paragraph_target_sentences": "3-5",
        "list_density": "high",
        "tone": "educational, thorough, helpful",
        "voice": "second person (you/your)",
        "avoid": [
            "assuming prior knowledge",
            "jargon without explanation",
            "rushing through concepts",
        ],
        "include": [
            "clear definitions",
            "examples where helpful",
            "logical progression",
        ],
    },
    "Promo – Conversion": {
        "reading_level": "Grade 7-9",
        "paragraph_target_sentences": "2-3",
        "list_density": "medium",
        "tone": "enthusiastic but compliant, benefit-focused",
        "voice": "second person (you/your)",
        "avoid": [
            "guaranteed wins",
            "risk-free language (unless legally accurate)",
            "pressure tactics",
        ],
        "include": [
            "clear value proposition",
            "how to claim steps",
            "terms acknowledgment",
        ],
    },
}

DEFAULT_PROFILE = "Top Stories – Informative"


def get_style_constraints(profile_name: str = DEFAULT_PROFILE) -> dict[str, Any]:
    """Get style constraints for a given profile.

    Args:
        profile_name: Name of the style profile

    Returns:
        Dict of style constraints including reading level, tone, etc.
    """
    profile = STYLE_PROFILES.get(profile_name, STYLE_PROFILES[DEFAULT_PROFILE])
    return profile


def get_available_profiles() -> list[str]:
    """Get list of available style profile names."""
    return list(STYLE_PROFILES.keys())


def format_constraints_for_prompt(profile_name: str = DEFAULT_PROFILE) -> str:
    """Format style constraints as text for inclusion in prompts.

    Args:
        profile_name: Name of the style profile

    Returns:
        Formatted string for LLM prompt
    """
    constraints = get_style_constraints(profile_name)

    lines = [
        f"Reading Level: {constraints.get('reading_level', 'Grade 8-10')}",
        f"Paragraph Length: {constraints.get('paragraph_target_sentences', '2-4')} sentences",
        f"Tone: {constraints.get('tone', 'clear and direct')}",
        f"Voice: {constraints.get('voice', 'second person')}",
    ]

    avoid = constraints.get("avoid", [])
    if avoid:
        lines.append(f"Avoid: {', '.join(avoid)}")

    include = constraints.get("include", [])
    if include:
        lines.append(f"Include: {', '.join(include)}")

    return "\n".join(lines)


# Guidance for how to use RAG snippets
RAG_USAGE_GUIDANCE = """
IMPORTANT: The background snippets below are from our published articles.
Use them to understand our WRITING STYLE, not as sources of facts.

DO:
- Match the tone, sentence structure, and readability level
- Follow similar paragraph lengths and formatting patterns
- Use comparable vocabulary and phrasing approaches

DO NOT:
- Copy sentences or phrases verbatim
- Use specific facts, statistics, or claims from the snippets
- Quote or reference the snippets as sources
- Assume the information is current or accurate

The snippets show HOW we write, not WHAT to write about.
"""


def get_rag_usage_guidance() -> str:
    """Get guidance text for how LLM should use RAG snippets."""
    return RAG_USAGE_GUIDANCE
