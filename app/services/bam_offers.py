"""BAM API offers fetcher.

Fetches promotional offers from the BAM API with caching support.
Supports multiple properties (Action Network, VegasInsider, etc.).
"""

import hashlib
import pickle
import re
import asyncio
from datetime import datetime, timedelta
from typing import Any, Optional


from app.config import get_settings
from app.services.offer_parsing import (
    enrich_offer_dict,
    extract_excluded_states_from_terms,
    extract_states_from_terms,
    parse_states,
)
from app.services.http_utils import get_json

settings = get_settings()

# BAM API configuration
BAM_CONTEXT = "web-article-top-stories"
CACHE_DURATION = timedelta(hours=6)
BAM_CACHE_SCHEMA_VERSION = "v2"
BAM_CATALOG_LOCATIONS = (
    "AZ", "CO", "CT", "DC", "IA", "IL", "IN", "KS", "KY",
    "LA", "MA", "MD", "MI", "NC", "NJ", "NY", "OH", "ON",
    "PA", "TN", "VA", "WV", "WY",
)

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


def _build_cache_scope_key(
    property_key: str,
    *,
    context: str,
    location: str = "",
    country_code: str = "",
    subdivision_id: str = "",
) -> str:
    """Build a stable cache key for a BAM request scope."""
    parts = [
        BAM_CACHE_SCHEMA_VERSION,
        property_key.strip().lower(),
        context.strip().lower(),
        location.strip().upper(),
        country_code.strip().upper(),
        subdivision_id.strip().upper(),
    ]
    return "__".join(part or "none" for part in parts)


def _build_catalog_scope_key(property_key: str, *, context: str) -> str:
    return _build_cache_scope_key(property_key, context=context, location="CATALOG")


def _geo_params_for_state(state: str | None) -> dict[str, str]:
    """Translate app state selection to BAM geo override params."""
    state_code = str(state or "").strip().upper()
    if not state_code or state_code == "ALL":
        return {}

    params = {"location": state_code}
    # BAM expects country_code for non-US regional overrides such as Ontario.
    if state_code == "ON":
        params["country_code"] = "CA"
    return params


def _merge_offer_variants(existing: dict, incoming: dict, *, source_location: str = "") -> dict:
    """Merge duplicate offer variants from multiple BAM location overrides."""
    merged = dict(existing or {})
    incoming = dict(incoming or {})

    for key, value in incoming.items():
        if key == "source_locations":
            continue
        if not merged.get(key) and value:
            merged[key] = value

    merged_states = parse_states(merged.get("states") or merged.get("states_list") or [])
    incoming_states = parse_states(incoming.get("states") or incoming.get("states_list") or [])
    if incoming_states and (not merged_states or merged_states == ["ALL"]):
        merged["states"] = incoming_states
        merged["states_list"] = incoming_states

    locations = list(merged.get("source_locations") or [])
    location_value = source_location or ",".join(incoming.get("source_locations") or [])
    for loc in [part.strip().upper() for part in location_value.split(",") if part.strip()]:
        if loc not in locations:
            locations.append(loc)
    if locations:
        merged["source_locations"] = locations
    return merged


def _normalize_catalog_offer_states(offer: dict) -> dict:
    """Replace placeholder ALL state with known location-union states when available."""
    normalized = dict(offer or {})
    source_locations = [
        str(loc).strip().upper()
        for loc in normalized.get("source_locations") or []
        if str(loc).strip()
    ]
    states = parse_states(normalized.get("states") or normalized.get("states_list") or [])
    if source_locations and (not states or states == ["ALL"]):
        normalized["states"] = source_locations
        normalized["states_list"] = source_locations
    return enrich_offer_dict(normalized)


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


def _looks_foreign_market_offer(offer_text: str, terms: str) -> bool:
    """Return True when an offer clearly targets a non-US market."""
    haystack = f"{offer_text}\n{terms}".lower()
    return haystack.startswith("mexico:") or "nuevo cliente" in haystack or "apuestas gratis" in haystack


def _offer_reward_amount(offer: dict) -> float:
    raw = str(offer.get("reward_amount") or offer.get("bonus_amount") or "").replace(",", "")
    match = re.search(r"(\d+(?:\.\d+)?)", raw)
    if not match:
        return 0.0
    try:
        return float(match.group(1))
    except ValueError:
        return 0.0


def _offer_type_priority(offer: dict) -> int:
    text = str(offer.get("offer_text") or offer.get("affiliate_offer") or "").lower()
    if "safety net" in text or "refund" in text:
        return 2
    if _looks_foreign_market_offer(text, str(offer.get("terms") or "")):
        return 3
    return 0


def _offer_matches_state(offer: dict, state_code: str) -> bool:
    """Return True when an offer is usable for the requested state."""
    if not state_code or state_code == "ALL":
        return True

    terms = str(offer.get("terms") or "")
    states = parse_states(offer.get("states") or offer.get("states_list") or [])
    positive_states = extract_states_from_terms(terms)
    excluded_states = extract_excluded_states_from_terms(terms)

    if state_code in excluded_states:
        return False
    if positive_states:
        return state_code in positive_states
    if state_code in states:
        return True
    if "ALL" in states:
        return not _looks_foreign_market_offer(
            str(offer.get("offer_text") or offer.get("affiliate_offer") or ""),
            terms,
        )
    return False


def _offer_state_sort_key(offer: dict, state_code: str) -> tuple[int, int, float, str]:
    """Rank state-appropriate offers by specificity and editorial usefulness."""
    states = parse_states(offer.get("states") or offer.get("states_list") or [])
    terms = str(offer.get("terms") or "")
    positive_states = extract_states_from_terms(terms)
    specificity = 1
    if positive_states:
        specificity = 0 if state_code in positive_states else 9
    elif state_code in states and "ALL" not in states:
        specificity = 0
    elif "ALL" not in states and states:
        specificity = 9
    return (
        specificity,
        _offer_type_priority(offer),
        -_offer_reward_amount(offer),
        str(offer.get("offer_text") or offer.get("affiliate_offer") or "").lower(),
    )


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
    affiliate_type = str(
        affiliate.get("affiliate_type")
        or affiliate.get("type")
        or "sportsbook"
    ).strip().lower()
    shortcode = (
        f'[bam-inline-promotion placement-id="{property_config.get("placement_id", "2037")}" '
        f'property-id="{property_config.get("property_id", "1")}" '
        f'context="{context}" internal-id="{internal_id}" '
        f'affiliate-type="{affiliate_type}" affiliate="{brand}"]'
    )

    # Parse states from explicit payload first, then terms text.
    states = parse_states(promo.get("states", []))
    terms_states = extract_states_from_terms(terms)
    if not terms_states:
        terms_states = extract_states_from_terms(affiliate.get("terms", ""))
    if terms_states:
        states = terms_states
    if not states:
        states = terms_states
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


def _cache_file(scope_key: str) -> Any:
    return settings.storage_dir / f"bam_offers_{scope_key}.pkl"


def _normalize_cached_offers(offers: list[dict]) -> list[dict]:
    """Re-enrich cached offers so parser fixes apply without waiting for cache expiry."""
    return [enrich_offer_dict(dict(offer or {})) for offer in (offers or []) if offer]


def _load_cache(scope_key: str) -> tuple[Optional[datetime], list[dict]]:
    """Load cached offers from disk."""
    cache_file = _cache_file(scope_key)
    if not cache_file.exists():
        return None, []

    try:
        with open(cache_file, "rb") as f:
            data = pickle.load(f)
            return data.get("timestamp"), _normalize_cached_offers(data.get("offers", []))
    except Exception:
        return None, []


def _save_cache(scope_key: str, offers: list[dict]) -> None:
    """Save offers to disk cache."""
    try:
        cache_file = _cache_file(scope_key)
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
    *,
    location: str | None = None,
    country_code: str | None = None,
    subdivision_id: str | None = None,
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
    location = str(location or "").strip().upper()
    country_code = str(country_code or "").strip().upper()
    subdivision_id = str(subdivision_id or "").strip().upper()
    scope_key = _build_cache_scope_key(
        property_key,
        context=context,
        location=location,
        country_code=country_code,
        subdivision_id=subdivision_id,
    )
    api_url = (
        f"https://b.bet-links.com/v1/affiliate/properties/"
        f"{property_config['property_id']}/placements/"
        f"{property_config['placement_id']}/promotions"
    )

    # Check memory cache first
    if not force_refresh and _cached_offers.get(scope_key) and _last_fetch.get(scope_key):
        if datetime.utcnow() - _last_fetch[scope_key] < CACHE_DURATION:
            _cached_offers[scope_key] = _normalize_cached_offers(_cached_offers[scope_key])
            return _cached_offers[scope_key]

    # Check disk cache
    if not force_refresh:
        cache_time, cached = _load_cache(scope_key)
        if cache_time and datetime.utcnow() - cache_time < CACHE_DURATION:
            _last_fetch[scope_key] = cache_time
            _cached_offers[scope_key] = cached
            return cached

    # Fetch from API
    try:
        params = {
            "user_parent_book_ids": "",
            "context": context,
        }
        if location:
            params["location"] = location
        if country_code:
            params["country_code"] = country_code
        if subdivision_id:
            params["subdivision_id"] = subdivision_id
        data = await get_json(
            api_url,
            params=params,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            },
            timeout=10.0,
            retries=3,
        )
    except Exception as e:
        print(f"BAM API fetch failed: {e}")
        # Fall back to cache
        _, cached = _load_cache(scope_key)
        if cached:
            _cached_offers[scope_key] = cached
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
    _last_fetch[scope_key] = datetime.utcnow()
    _cached_offers[scope_key] = offers
    _save_cache(scope_key, offers)

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
    geo_params = _geo_params_for_state(state)
    offers = await fetch_offers_from_bam(
        force_refresh,
        property_key=property_key,
        context=context,
        **geo_params,
    )

    if brand:
        brand_lower = brand.lower()
        offers = [o for o in offers if o.get("brand", "").lower() == brand_lower]

    if state and state.upper() != "ALL":
        state_upper = state.upper()
        offers = [o for o in offers if _offer_matches_state(o, state_upper)]
        offers.sort(key=lambda o: _offer_state_sort_key(o, state_upper))

    return offers


async def get_offer_catalog_bam(
    *,
    state: str | None = None,
    brand: str | None = None,
    force_refresh: bool = False,
    property_key: str | None = None,
    context: str | None = None,
) -> list[dict]:
    """Return a union catalog of offers across BAM location overrides.

    This is used by the picker so operators gated behind BAM geo overrides
    still appear even when they are absent from the base placement response.
    """
    property_config = _get_property_config(property_key)
    property_key = next(
        (k for k, v in PROPERTIES.items() if v == property_config),
        DEFAULT_PROPERTY,
    )
    context = context or property_config.get("default_context", BAM_CONTEXT)
    scope_key = _build_catalog_scope_key(property_key, context=context)

    if not force_refresh and _cached_offers.get(scope_key) and _last_fetch.get(scope_key):
        if datetime.utcnow() - _last_fetch[scope_key] < CACHE_DURATION:
            _cached_offers[scope_key] = _normalize_cached_offers(_cached_offers[scope_key])
            offers = list(_cached_offers[scope_key])
        else:
            offers = []
    else:
        offers = []

    if not offers and not force_refresh:
        cache_time, cached = _load_cache(scope_key)
        if cache_time and datetime.utcnow() - cache_time < CACHE_DURATION:
            _last_fetch[scope_key] = cache_time
            _cached_offers[scope_key] = cached
            offers = list(cached)

    if not offers:
        requested_state = str(state or "").strip().upper()
        locations: list[str] = []
        if requested_state and requested_state != "ALL":
            locations.append(requested_state)
        for location in BAM_CATALOG_LOCATIONS:
            if location not in locations:
                locations.append(location)

        base_offers = await fetch_offers_from_bam(
            force_refresh,
            property_key=property_key,
            context=context,
        )
        scoped_results = await asyncio.gather(
            *[
                fetch_offers_from_bam(
                    force_refresh,
                    property_key=property_key,
                    context=context,
                    **_geo_params_for_state(location),
                )
                for location in locations
            ]
        )

        merged_by_id: dict[str, dict] = {}
        for offer in base_offers:
            offer_id = str(offer.get("id") or "")
            if not offer_id:
                continue
            merged_by_id[offer_id] = _merge_offer_variants({}, offer)

        for location, scoped_offers in zip(locations, scoped_results):
            for offer in scoped_offers:
                offer_id = str(offer.get("id") or "")
                if not offer_id:
                    continue
                merged_by_id[offer_id] = _merge_offer_variants(
                    merged_by_id.get(offer_id, {}),
                    offer,
                    source_location=location,
                )

        offers = [_normalize_catalog_offer_states(offer) for offer in merged_by_id.values()]
        _last_fetch[scope_key] = datetime.utcnow()
        _cached_offers[scope_key] = offers
        _save_cache(scope_key, offers)

    if brand:
        brand_lower = brand.lower()
        offers = [o for o in offers if o.get("brand", "").lower() == brand_lower]

    requested_state = str(state or "").strip().upper()
    if requested_state and requested_state != "ALL":
        def _catalog_sort_key(offer: dict) -> tuple[int, tuple[int, int, float, str]]:
            source_locations = {str(loc).upper() for loc in offer.get("source_locations") or []}
            is_direct_location_hit = 0 if requested_state in source_locations else 1
            return (is_direct_location_hit, _offer_state_sort_key(offer, requested_state))

        offers.sort(key=_catalog_sort_key)

    return offers


async def get_offer_by_id_bam(
    offer_id: str,
    property_key: str | None = None,
    context: str | None = None,
    state: str | None = None,
) -> Optional[dict]:
    """Get a single offer by its ID."""
    geo_params = _geo_params_for_state(state)
    offers = await fetch_offers_from_bam(
        property_key=property_key,
        context=context,
        **geo_params,
    )
    for offer in offers:
        if offer.get("id") == offer_id:
            if not state or str(state).strip().upper() == "ALL":
                break
            return offer
    catalog_offers = await get_offer_catalog_bam(
        state=state,
        property_key=property_key,
        context=context,
    )
    for offer in catalog_offers:
        if offer.get("id") == offer_id:
            return offer
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
