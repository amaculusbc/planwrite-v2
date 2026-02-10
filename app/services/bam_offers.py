"""BAM API offers fetcher.

Fetches promotional offers from the BAM API with caching support.
Supports multiple properties (Action Network, VegasInsider, etc.).
"""

import hashlib
import pickle
from datetime import datetime, timedelta
from typing import Any, Optional


from app.config import get_settings
from app.services.offer_parsing import enrich_offer_dict
from app.services.http_utils import get_json

settings = get_settings()

# BAM API configuration
BAM_CONTEXT = "web-article-top-stories"
CACHE_DURATION = timedelta(hours=6)

# Property configurations (parity with v1)
PROPERTIES = {
    "action_network": {
        "property_id": "1",
        "placement_id": "2037",
        "switchboard_domain": "switchboard.actionnetwork.com",
        "name": "Action Network",
        "default_context": "web-article-top-stories",
    },
    "vegas_insider": {
        "property_id": "2",
        "placement_id": "2035",
        "switchboard_domain": "switchboard.vegasinsider.com",
        "name": "VegasInsider",
        "default_context": "web-article-top-stories",
    },
    "rotogrinders": {
        "property_id": "3",
        "placement_id": "2039",
        "switchboard_domain": "switchboard.rotogrinders.com",
        "name": "RotoGrinders",
        "default_context": "web-article-top-stories",
    },
    "scores_and_odds": {
        "property_id": "4",
        "placement_id": "2029",
        "switchboard_domain": "switchboard.scoresandodds.com",
        "name": "ScoresAndOdds",
        "default_context": "web-article-top-stories",
    },
    "sportshandle": {
        "property_id": "5",
        "placement_id": "2040",
        "switchboard_domain": "switchboard.actionnetwork.com",
        "name": "SportsHandle",
        "default_context": "web-article-top-stories",
    },
    "fantasy_labs": {
        "property_id": "11",
        "placement_id": "2041",
        "switchboard_domain": "switchboard.actionnetwork.com",
        "name": "FantasyLabs",
        "default_context": "web-article-top-stories",
    },
}

DEFAULT_PROPERTY = "action_network"

# Last fetch timestamp (per property)
_last_fetch: dict[str, datetime] = {}
_cached_offers: dict[str, list[dict]] = {}


def _generate_offer_id(
    affiliate_id: str | int,
    campaign_id: str | int,
    brand: str,
    offer_text: str,
) -> str:
    """Generate a unique offer ID using API identifiers."""
    # Use affiliate_id and campaign_id as primary unique identifiers
    combined = f"{affiliate_id}|{campaign_id}|{brand}|{offer_text}".lower()
    return hashlib.md5(combined.encode()).hexdigest()[:16]


def _get_property_config(property_key: str | None) -> dict:
    """Return property config with defaults."""
    key = (property_key or DEFAULT_PROPERTY).strip().lower()
    if key not in PROPERTIES:
        key = DEFAULT_PROPERTY
    return PROPERTIES[key]


def _select_internal_id(internal_identifiers: list[str]) -> str:
    """Select the best internal identifier from a list."""
    if not internal_identifiers:
        return "evergreen"

    priority_ids = ["fbo", "bet-get", "lpb", "omni", "evergreen", "evergreen2"]
    generic_ids = {"sportsbook", "bonus-code", "canada", "mo"}

    for priority_id in priority_ids:
        if priority_id in internal_identifiers:
            return priority_id

    for internal_id in internal_identifiers:
        if internal_id not in generic_ids:
            return internal_id

    return internal_identifiers[0]


def _parse_promotion(promo: dict, property_config: dict, context: str) -> dict:
    """Parse a single promotion from BAM API response."""
    affiliate = promo.get("affiliate", {})
    campaign = promo.get("campaign", {})
    images = promo.get("images", [])

    # Extract brand name
    brand = affiliate.get("name", "").strip()

    # Extract bonus code
    bonus_code = promo.get("bonus_code", "") or promo.get("additional_attributes", {}).get("bonus_code", "") or ""

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
    switchboard_domain = property_config.get("switchboard_domain", "switchboard.actionnetwork.com")
    property_id = property_config.get("property_id", "1")
    switchboard_link = (
        f"https://{switchboard_domain}/offers"
        f"?affiliateId={affiliate_id}&campaignId={campaign_id}"
        f"&context={context}&stateCode=&propertyId={property_id}"
    )

    # Extract internal IDs for shortcode
    internal_ids = promo.get("internal_ids", {}) or {}
    internal_identifiers = promo.get("internal_identifiers", []) or []
    # Prioritize 'fbo' over others
    internal_id = internal_ids.get("fbo") or next(iter(internal_ids.values()), "") or _select_internal_id(internal_identifiers)

    # Build shortcode
    affiliate_type = affiliate.get("type", "sportsbook")
    shortcode = (
        f'[bam-inline-promotion placement-id="{property_config.get("placement_id", "2037")}" '
        f'property-id="{property_config.get("property_id", "1")}" '
        f'context="{context}" internal-id="{internal_id}" '
        f'affiliate-type="{affiliate_type}" affiliate="{brand}"]'
    )

    # Parse states (if available)
    states = promo.get("states", [])
    if not states:
        states = ["ALL"]

    offer_id = _generate_offer_id(affiliate_id, campaign_id, brand, offer_text)

    offer = {
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
    return enrich_offer_dict(offer)


def _cache_file(property_key: str) -> Any:
    return settings.storage_dir / f"bam_offers_{property_key}.pkl"


def _load_cache(property_key: str) -> tuple[Optional[datetime], list[dict]]:
    """Load cached offers from disk."""
    cache_file = _cache_file(property_key)
    if not cache_file.exists():
        return None, []

    try:
        with open(cache_file, "rb") as f:
            data = pickle.load(f)
            return data.get("timestamp"), data.get("offers", [])
    except Exception:
        return None, []


def _save_cache(property_key: str, offers: list[dict]) -> None:
    """Save offers to disk cache."""
    try:
        cache_file = _cache_file(property_key)
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_file, "wb") as f:
            pickle.dump({
                "timestamp": datetime.utcnow(),
                "offers": offers,
            }, f)
    except Exception as e:
        print(f"Failed to save BAM cache: {e}")


async def fetch_offers_from_bam(
    force_refresh: bool = False,
    property_key: str | None = None,
    context: str | None = None,
) -> list[dict]:
    """Fetch offers from BAM API with caching.

    Args:
        force_refresh: If True, bypass cache and fetch fresh data.
        property_key: Property key (e.g., "action_network")
        context: Override default context for the property.

    Returns:
        List of parsed offer dictionaries.
    """
    global _last_fetch, _cached_offers

    property_config = _get_property_config(property_key)
    property_key = next(
        (k for k, v in PROPERTIES.items() if v == property_config),
        DEFAULT_PROPERTY,
    )
    context = context or property_config.get("default_context", BAM_CONTEXT)
    api_url = (
        f"https://b.bet-links.com/v1/affiliate/properties/"
        f"{property_config['property_id']}/placements/"
        f"{property_config['placement_id']}/promotions"
    )

    # Check memory cache first
    if not force_refresh and _cached_offers.get(property_key) and _last_fetch.get(property_key):
        if datetime.utcnow() - _last_fetch[property_key] < CACHE_DURATION:
            return _cached_offers[property_key]

    # Check disk cache
    if not force_refresh:
        cache_time, cached = _load_cache(property_key)
        if cache_time and datetime.utcnow() - cache_time < CACHE_DURATION:
            _last_fetch[property_key] = cache_time
            _cached_offers[property_key] = cached
            return cached

    # Fetch from API
    try:
        data = await get_json(
            api_url,
            params={
                "user_parent_book_ids": "",
                "context": context,
            },
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            },
            timeout=10.0,
            retries=3,
        )
    except Exception as e:
        print(f"BAM API fetch failed: {e}")
        # Fall back to cache
        _, cached = _load_cache(property_key)
        if cached:
            return cached
        return []

    # Parse promotions
    promotions = data if isinstance(data, list) else data.get("promotions", [])
    offers = []
    seen_ids = set()

    for idx, promo in enumerate(promotions):
        try:
            parsed = _parse_promotion(promo, property_config, context)
            if parsed.get("brand"):  # Only include offers with a brand
                # Ensure unique ID by appending index if duplicate
                offer_id = parsed["id"]
                if offer_id in seen_ids:
                    # Append index to make it unique
                    offer_id = f"{offer_id}_{idx}"
                    parsed["id"] = offer_id
                seen_ids.add(offer_id)
                offers.append(parsed)
        except Exception as e:
            print(f"Failed to parse promotion: {e}")
            continue

    # Update caches
    _last_fetch[property_key] = datetime.utcnow()
    _cached_offers[property_key] = offers
    _save_cache(property_key, offers)

    return offers


async def get_offers_bam(
    state: str | None = None,
    brand: str | None = None,
    force_refresh: bool = False,
    property_key: str | None = None,
    context: str | None = None,
) -> list[dict]:
    """Get offers from BAM API with optional filtering.

    Args:
        state: Filter by state code (e.g., "NJ", "PA")
        brand: Filter by brand name
        force_refresh: Bypass cache

    Returns:
        List of offer dictionaries.
    """
    offers = await fetch_offers_from_bam(force_refresh, property_key=property_key, context=context)

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


async def get_offer_by_id_bam(
    offer_id: str,
    property_key: str | None = None,
    context: str | None = None,
) -> Optional[dict]:
    """Get a single offer by its ID."""
    offers = await fetch_offers_from_bam(property_key=property_key, context=context)
    for offer in offers:
        if offer.get("id") == offer_id:
            return offer
    return None


async def get_all_brands(property_key: str | None = None) -> list[str]:
    """Get list of all available brands."""
    offers = await fetch_offers_from_bam(property_key=property_key)
    brands = sorted(set(o.get("brand", "") for o in offers if o.get("brand")))
    return brands


def get_available_properties() -> dict[str, str]:
    """Return available property keys and display names."""
    return {key: config["name"] for key, config in PROPERTIES.items()}


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

    import re
    block = re.sub(r"\n{3,}", "\n\n", block).strip() + "\n"

    return block
