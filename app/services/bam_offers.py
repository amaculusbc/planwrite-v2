"""BAM API offers fetcher.

Fetches promotional offers from the BAM API with caching support.
"""

import json
import pickle
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import httpx

from app.config import get_settings

settings = get_settings()

# BAM API configuration
BAM_API_URL = "https://b.bet-links.com/v1/affiliate/properties/1/placements/2037/promotions"
BAM_CONTEXT = "web-article-top-stories"
CACHE_FILE = settings.storage_dir / "bam_offers_cache.pkl"
CACHE_DURATION = timedelta(hours=6)

# Last fetch timestamp
_last_fetch: Optional[datetime] = None
_cached_offers: list[dict] = []


def _generate_offer_id(brand: str, affiliate_offer: str, bonus_code: str) -> str:
    """Generate a unique offer ID."""
    combined = f"{brand}|{affiliate_offer}|{bonus_code}".lower()
    return hashlib.md5(combined.encode()).hexdigest()[:16]


def _parse_promotion(promo: dict) -> dict:
    """Parse a single promotion from BAM API response."""
    affiliate = promo.get("affiliate", {})
    campaign = promo.get("campaign", {})
    images = promo.get("images", [])

    # Extract brand name
    brand = affiliate.get("name", "").strip()

    # Extract bonus code
    bonus_code = promo.get("bonus_code", "") or ""

    # Extract offer text
    offer_text = promo.get("title", "") or promo.get("description", "") or ""

    # Extract terms
    terms = promo.get("terms", "") or ""

    # Get logo and promo images
    logo_url = ""
    promo_image = ""
    for img in images:
        img_type = img.get("type", "").lower()
        if img_type == "logo" and not logo_url:
            logo_url = img.get("url", "")
        elif img_type == "promo" and not promo_image:
            promo_image = img.get("url", "")

    # Build switchboard link
    affiliate_id = affiliate.get("id", "")
    campaign_id = campaign.get("id", "")
    switchboard_link = (
        f"https://switchboard.actionnetwork.com/offers"
        f"?affiliateId={affiliate_id}&campaignId={campaign_id}"
        f"&context={BAM_CONTEXT}&stateCode="
    )

    # Extract internal IDs for shortcode
    internal_ids = promo.get("internal_ids", {})
    # Prioritize 'fbo' over others
    internal_id = internal_ids.get("fbo") or next(iter(internal_ids.values()), "")

    # Build shortcode
    affiliate_type = affiliate.get("type", "sportsbook")
    shortcode = (
        f'[bam-inline-promotion placement-id="2037" property-id="1" '
        f'context="{BAM_CONTEXT}" internal-id="{internal_id}" '
        f'affiliate-type="{affiliate_type}" affiliate="{brand}"]'
    )

    # Parse states (if available)
    states = promo.get("states", [])
    if not states:
        states = ["ALL"]

    offer_id = _generate_offer_id(brand, offer_text, bonus_code)

    return {
        "id": offer_id,
        "brand": brand,
        "affiliate_offer": f"{brand}: {offer_text}" if brand and offer_text else brand or offer_text,
        "offer_text": offer_text,
        "bonus_code": bonus_code,
        "terms": terms,
        "states": states,
        "switchboard_link": switchboard_link,
        "shortcode": shortcode,
        "logo_url": logo_url,
        "promo_image": promo_image,
        "affiliate_id": affiliate_id,
        "campaign_id": campaign_id,
        "page_type": "sportsbook",
    }


def _load_cache() -> tuple[Optional[datetime], list[dict]]:
    """Load cached offers from disk."""
    if not CACHE_FILE.exists():
        return None, []

    try:
        with open(CACHE_FILE, "rb") as f:
            data = pickle.load(f)
            return data.get("timestamp"), data.get("offers", [])
    except Exception:
        return None, []


def _save_cache(offers: list[dict]) -> None:
    """Save offers to disk cache."""
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(CACHE_FILE, "wb") as f:
            pickle.dump({
                "timestamp": datetime.utcnow(),
                "offers": offers,
            }, f)
    except Exception as e:
        print(f"Failed to save BAM cache: {e}")


async def fetch_offers_from_bam(force_refresh: bool = False) -> list[dict]:
    """Fetch offers from BAM API with caching.

    Args:
        force_refresh: If True, bypass cache and fetch fresh data.

    Returns:
        List of parsed offer dictionaries.
    """
    global _last_fetch, _cached_offers

    # Check memory cache first
    if not force_refresh and _cached_offers and _last_fetch:
        if datetime.utcnow() - _last_fetch < CACHE_DURATION:
            return _cached_offers

    # Check disk cache
    if not force_refresh:
        cache_time, cached = _load_cache()
        if cache_time and datetime.utcnow() - cache_time < CACHE_DURATION:
            _last_fetch = cache_time
            _cached_offers = cached
            return cached

    # Fetch from API
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                BAM_API_URL,
                params={
                    "user_parent_book_ids": "",
                    "context": BAM_CONTEXT,
                },
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                },
                timeout=10.0,
            )
            response.raise_for_status()
            data = response.json()
    except Exception as e:
        print(f"BAM API fetch failed: {e}")
        # Fall back to cache
        _, cached = _load_cache()
        if cached:
            return cached
        return []

    # Parse promotions
    promotions = data if isinstance(data, list) else data.get("promotions", [])
    offers = []

    for promo in promotions:
        try:
            parsed = _parse_promotion(promo)
            if parsed.get("brand"):  # Only include offers with a brand
                offers.append(parsed)
        except Exception as e:
            print(f"Failed to parse promotion: {e}")
            continue

    # Update caches
    _last_fetch = datetime.utcnow()
    _cached_offers = offers
    _save_cache(offers)

    return offers


async def get_offers_bam(
    state: str | None = None,
    brand: str | None = None,
    force_refresh: bool = False,
) -> list[dict]:
    """Get offers from BAM API with optional filtering.

    Args:
        state: Filter by state code (e.g., "NJ", "PA")
        brand: Filter by brand name
        force_refresh: Bypass cache

    Returns:
        List of offer dictionaries.
    """
    offers = await fetch_offers_from_bam(force_refresh)

    if brand:
        brand_lower = brand.lower()
        offers = [o for o in offers if o.get("brand", "").lower() == brand_lower]

    if state and state.upper() != "ALL":
        state_upper = state.upper()
        offers = [
            o for o in offers
            if state_upper in (o.get("states") or []) or "ALL" in (o.get("states") or [])
        ]

    return offers


async def get_offer_by_id_bam(offer_id: str) -> Optional[dict]:
    """Get a single offer by its ID."""
    offers = await fetch_offers_from_bam()
    for offer in offers:
        if offer.get("id") == offer_id:
            return offer
    return None


async def get_all_brands() -> list[str]:
    """Get list of all available brands."""
    offers = await fetch_offers_from_bam()
    brands = sorted(set(o.get("brand", "") for o in offers if o.get("brand")))
    return brands


def render_bam_offer_block(offer: dict, placement: str = "inline") -> str:
    """Render offer as markdown CTA block."""
    brand = offer.get("brand", "")
    headline = offer.get("offer_text", "") or offer.get("affiliate_offer", "")
    code = offer.get("bonus_code", "")
    url = offer.get("switchboard_link", "") or "#"
    terms = offer.get("terms", "")

    title = f"**{brand} Promo**" if brand else "**Promo**"
    code_line = f"\n**Bonus code:** `{code}`" if code else ""
    link_line = f"\n[Claim Offer]({url})" if url and url != "#" else ""

    block = f"> {title}  \n> {headline}  \n{code_line}{link_line}\n"

    if terms:
        block += f"\n<details><summary>Terms apply</summary><p>{terms}</p></details>\n"

    block += "\n21+. Gambling problem? Call 1-800-GAMBLER. Please bet responsibly.\n"

    import re
    block = re.sub(r"\n{3,}", "\n\n", block).strip() + "\n"

    return block
