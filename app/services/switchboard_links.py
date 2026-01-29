"""Switchboard link injection service.

Injects affiliate tracking links into article content.
"""

import re
from typing import Optional


def inject_switchboard_links(
    text: str,
    brand: str,
    bonus_code: str,
    switchboard_url: str,
    max_links: int = 12,
) -> str:
    """Inject clickable switchboard links into text wherever brand + code are mentioned.

    Handles both plain text AND text already wrapped in <strong> tags.

    Converts:
    - "bet365 bonus code TOPACTION" → linked
    - "<strong>bet365 bonus code TOPACTION</strong>" → linked (preserves strong)

    Args:
        text: Article HTML content
        brand: Brand name (e.g., "bet365")
        bonus_code: Promo code (e.g., "TOPACTION")
        switchboard_url: Affiliate tracking URL
        max_links: Maximum number of links to inject

    Returns:
        Text with injected links
    """
    if not (brand and switchboard_url):
        return text

    brand_escaped = re.escape(brand)
    code_escaped = re.escape(bonus_code) if bonus_code else ""
    links_injected = 0

    # First, handle already-wrapped <strong> tags WITH bonus code (don't double-wrap)
    # Pattern: <strong>bet365 bonus code TOPACTION</strong>
    if bonus_code:
        strong_with_code_pattern = re.compile(
            rf"<strong>((?:the\s+)?{brand_escaped}\s+(?:bonus\s+code|promo\s+code|Promo\s+Code|Bonus\s+Code|code)\s+{code_escaped})</strong>",
            re.IGNORECASE,
        )
    else:
        strong_with_code_pattern = None

    # Also match <strong>bet365 bonus code</strong> WITHOUT the specific code
    # (LLM sometimes forgets to include the actual code value)
    strong_pattern = re.compile(
        rf"<strong>((?:the\s+)?{brand_escaped}\s+(?:bonus\s+code|promo\s+code|Promo\s+Code|Bonus\s+Code))</strong>",
        re.IGNORECASE,
    )

    def make_strong_replacer(include_code: bool = True):
        """Create a replacer function for strong-wrapped patterns."""
        def replacer(match):
            nonlocal links_injected
            # Skip if already inside an <a> tag
            start = match.start()
            before = text[:start]
            if "<a " in before and "</a>" not in before[before.rfind("<a "):]:
                return match.group(0)  # Already linked
            if links_injected >= max_links:
                return match.group(0)
            links_injected += 1
            inner_text = match.group(1)
            # If the match doesn't include the code, append it
            if not include_code and bonus_code and bonus_code.upper() not in inner_text.upper():
                inner_text = f"{inner_text} {bonus_code}"
            return (
                f'<a data-id="switchboard_tracking" '
                f'href="{switchboard_url}" '
                f'rel="nofollow">'
                f"<strong>{inner_text}</strong>"
                f"</a>"
            )
        return replacer

    # First try the pattern WITH the code (if we have a code)
    result = text
    if strong_with_code_pattern:
        result = strong_with_code_pattern.sub(make_strong_replacer(include_code=True), result)

    # Then try the pattern WITHOUT the code (will add the code if missing)
    result = strong_pattern.sub(make_strong_replacer(include_code=False), result)

    # Then, handle plain text (not already wrapped in <strong> tags)
    # Pattern WITH code: bet365 bonus code TOPACTION
    if bonus_code:
        plain_with_code_pattern = re.compile(
            rf"\b(the\s+)?({brand_escaped})\s+(bonus\s+code|promo\s+code|Promo\s+Code|Bonus\s+Code|code)\s+({code_escaped})\b",
            re.IGNORECASE,
        )
    else:
        plain_with_code_pattern = None

    # Pattern WITHOUT code: bet365 bonus code (not followed by the specific code)
    # This catches cases where LLM wrote "bet365 promo code" without the actual code value
    if bonus_code:
        # Don't match if followed by the code value
        plain_pattern = re.compile(
            rf"\b(the\s+)?({brand_escaped})\s+(bonus\s+code|promo\s+code|Promo\s+Code|Bonus\s+Code)\b(?!\s+{code_escaped})",
            re.IGNORECASE,
        )
    else:
        # No code to avoid, just match the pattern
        plain_pattern = re.compile(
            rf"\b(the\s+)?({brand_escaped})\s+(bonus\s+code|promo\s+code|Promo\s+Code|Bonus\s+Code)\b",
            re.IGNORECASE,
        )

    def plain_with_code_replacer(match):
        nonlocal links_injected
        match_text = match.group(0)
        match_start = match.start()
        before_text = result[:match_start]
        # Check if we're inside a <strong> tag
        last_strong_open = before_text.rfind("<strong>")
        last_strong_close = before_text.rfind("</strong>")
        if last_strong_open > last_strong_close:
            return match_text  # Inside <strong>, skip
        # Check if we're inside an <a> tag
        last_a_open = before_text.rfind("<a ")
        last_a_close = before_text.rfind("</a>")
        if last_a_open > last_a_close:
            return match_text  # Inside <a>, skip

        if links_injected >= max_links:
            return match_text
        links_injected += 1
        the_prefix = match.group(1) or ""
        brand_text = match.group(2)
        code_type = match.group(3)
        code_text = match.group(4)
        return (
            f"{the_prefix}"
            f'<a data-id="switchboard_tracking" '
            f'href="{switchboard_url}" '
            f'rel="nofollow">'
            f"<strong>{brand_text} {code_type} {code_text}</strong>"
            f"</a>"
        )

    def plain_replacer(match):
        nonlocal links_injected
        match_text = match.group(0)
        match_start = match.start()
        before_text = result[:match_start]
        # Check if we're inside a <strong> tag
        last_strong_open = before_text.rfind("<strong>")
        last_strong_close = before_text.rfind("</strong>")
        if last_strong_open > last_strong_close:
            return match_text  # Inside <strong>, skip
        # Check if we're inside an <a> tag
        last_a_open = before_text.rfind("<a ")
        last_a_close = before_text.rfind("</a>")
        if last_a_open > last_a_close:
            return match_text  # Inside <a>, skip

        if links_injected >= max_links:
            return match_text
        links_injected += 1
        the_prefix = match.group(1) or ""
        brand_text = match.group(2)
        code_type = match.group(3)
        # Add the bonus code if we have one
        code_suffix = f" {bonus_code}" if bonus_code else ""
        return (
            f"{the_prefix}"
            f'<a data-id="switchboard_tracking" '
            f'href="{switchboard_url}" '
            f'rel="nofollow">'
            f"<strong>{brand_text} {code_type}{code_suffix}</strong>"
            f"</a>"
        )

    # Apply plain text replacers (with code first, then without)
    if plain_with_code_pattern:
        result = plain_with_code_pattern.sub(plain_with_code_replacer, result)
    result = plain_pattern.sub(plain_replacer, result)
    return result


def inject_brand_links(
    text: str,
    brand: str,
    review_url: Optional[str] = None,
    max_links: int = 3,
) -> str:
    """Inject links to brand review page when brand name mentioned alone.

    Converts: "bet365" -> "<a href='/reviews/bet365'>bet365</a>"
    Only when NOT already part of "bet365 bonus code"

    Args:
        text: Article HTML content
        brand: Brand name
        review_url: URL to brand review page
        max_links: Maximum number of links to inject

    Returns:
        Text with injected links
    """
    if not (brand and review_url):
        return text

    brand_escaped = re.escape(brand)

    # Match brand name NOT followed by "bonus code" or "promo code"
    # Negative lookahead to avoid double-linking
    pattern = re.compile(
        rf"\b({brand_escaped})(?!\s+(?:bonus|promo|code)\s+code)",
        re.IGNORECASE,
    )

    links_injected = 0

    def replacer(match):
        nonlocal links_injected

        # Skip if already inside an <a> tag
        before_text = text[: match.start()]
        if "<a" in before_text and "</a>" not in before_text[before_text.rfind("<a") :]:
            return match.group(0)

        if links_injected >= max_links:
            return match.group(0)

        links_injected += 1
        return f'<a href="{review_url}" rel="follow">{match.group(1)}</a>'

    result = pattern.sub(replacer, text)
    return result


def build_switchboard_url(
    affiliate_id: str | int,
    campaign_id: str | int,
    context: str = "web-article-top-stories",
    state_code: str = "",
    property_id: str | int = "1",
) -> str:
    """Build a switchboard tracking URL.

    Args:
        affiliate_id: Affiliate ID from offer
        campaign_id: Campaign ID from offer
        context: Context string for tracking
        state_code: State code (optional)
        property_id: Property ID

    Returns:
        Fully constructed switchboard URL
    """
    base = "https://switchboard.actionnetwork.com/offers"
    params = [
        f"affiliateId={affiliate_id}",
        f"campaignId={campaign_id}",
        f"context={context}",
        f"propertyId={property_id}",
    ]
    if state_code:
        params.append(f"stateCode={state_code}")

    return f"{base}?{'&'.join(params)}"
