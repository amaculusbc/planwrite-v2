"""Switchboard link injection service.

Injects affiliate tracking links into article content.
"""

import re
from typing import Optional


def _token_pattern(value: str) -> str:
    """Build a regex pattern that tolerates spaces/hyphens between letter/number groups."""
    if not value:
        return ""
    cleaned = re.sub(r"[^A-Za-z0-9]+", " ", value).strip()
    if not cleaned:
        return re.escape(value)
    parts = re.findall(r"[A-Za-z]+|\d+", cleaned)
    if not parts:
        return re.escape(value)
    return r"[\s\-]*".join(re.escape(p) for p in parts)


def _normalize_token(value: str) -> str:
    """Normalize to alphanumeric only for comparisons."""
    return re.sub(r"[^A-Za-z0-9]+", "", value or "").upper()


def _inside_heading(text: str, pos: int) -> bool:
    """Return True if position is inside an h1/h2/h3 tag."""
    before = text[:pos].lower()
    for tag in ("h1", "h2", "h3"):
        if before.rfind(f"<{tag}") > before.rfind(f"</{tag}>"):
            return True
    return False


def inject_switchboard_links(
    text: str,
    brand: str,
    bonus_code: str,
    switchboard_url: str,
    max_links: int = 6,
) -> str:
    """Inject clickable switchboard links into text with natural anchor control.

    Preference order:
    1) Wrap existing <strong> anchors that mention the brand or promo language.
    2) If no links were injected, wrap the first plain brand mention.
    """
    if not (brand and switchboard_url):
        return text

    links_injected = 0
    brand_lower = brand.lower()
    code_lower = (bonus_code or "").lower()

    strong_any_pattern = re.compile(r"<strong>(.*?)</strong>", re.IGNORECASE | re.DOTALL)

    def strong_replacer(match):
        nonlocal links_injected
        start = match.start()
        if _inside_heading(text, start):
            return match.group(0)
        before = text[:start]
        if "<a " in before and "</a>" not in before[before.rfind("<a "):]:
            return match.group(0)
        if links_injected >= max_links:
            return match.group(0)
        inner = match.group(1)
        inner_lower = inner.lower()
        if brand_lower not in inner_lower and "promo code" not in inner_lower and "bonus code" not in inner_lower and (code_lower and code_lower not in inner_lower):
            return match.group(0)
        links_injected += 1
        return (
            f'<a data-id="switchboard_tracking" '
            f'href="{switchboard_url}" '
            f'rel="nofollow">'
            f"<strong>{inner}</strong>"
            f"</a>"
        )

    result = strong_any_pattern.sub(strong_replacer, text)

    # Fallback: ensure at least one link using first brand mention
    if links_injected == 0:
        brand_pattern = re.compile(rf"({re.escape(brand)})", re.IGNORECASE)

        def brand_replacer(match):
            nonlocal links_injected
            if links_injected >= 1:
                return match.group(0)
            start = match.start()
            if _inside_heading(result, start):
                return match.group(0)
            before_text = result[:start]
            if "<a " in before_text and "</a>" not in before_text[before_text.rfind("<a "):]:
                return match.group(0)
            links_injected += 1
            return (
                f'<a data-id="switchboard_tracking" '
                f'href="{switchboard_url}" '
                f'rel="nofollow">'
                f"{match.group(1)}"
                f"</a>"
            )

        result = brand_pattern.sub(brand_replacer, result, count=1)

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
