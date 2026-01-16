"""Offers service - Google Sheets sync and management.

Handles fetching offers from Google Sheets with database caching.
"""

import json
import os
import re
from datetime import datetime, timedelta
from typing import Any, Optional

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.offer import Offer

settings = get_settings()

# Cache duration for offers
CACHE_DURATION = timedelta(minutes=15)

# Last sync timestamp
_last_sync: Optional[datetime] = None


def _normalize_token(s: str) -> str:
    """Normalize column header to standard token."""
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def _map_header(col: str) -> str:
    """Map various header names to normalized keys."""
    t = _normalize_token(col)

    mappings = {
        "affiliateoffer": "affiliate_offer",
        "affiliate": "affiliate_offer",
        "offer": "affiliate_offer",
        "offertext": "offer_text",
        "offernarrative": "offer_text",
        "states": "states",
        "statelist": "states",
        "terms": "terms",
        "legal": "terms",
        "disclaimer": "terms",
        "bonuscode": "bonus_code",
        "code": "bonus_code",
        "promocode": "bonus_code",
        "pagetype": "page_type",
        "shortcode": "shortcode",
        "switchboardlink": "switchboard_link",
        "link": "switchboard_link",
        "url": "switchboard_link",
    }

    for key, value in mappings.items():
        if key in t:
            return value

    return _normalize_token(col)


def _parse_brand(affiliate_offer: str) -> str:
    """Extract brand name from affiliate offer string."""
    s = (affiliate_offer or "").strip()
    return s.split(":", 1)[0].strip() if ":" in s else s


def _parse_states(states: Any) -> list[str]:
    """Parse states string into list."""
    if not isinstance(states, str) or not states.strip():
        return []

    txt = states.strip()
    if txt.upper() == "ALL":
        return ["ALL"]

    parts = [p.strip().upper() for p in re.split(r"[,\|/]+", txt) if p.strip()]
    seen = set()
    result = []
    for p in parts:
        if p not in seen:
            seen.add(p)
            result.append(p)
    return result


async def sync_offers_from_sheets(db: AsyncSession) -> int:
    """Sync offers from Google Sheets to database.

    Returns number of offers synced.
    """
    global _last_sync

    if not settings.offers_sheet_id or settings.offers_source != "gspread":
        return 0

    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        # Build credentials
        scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

        if settings.google_service_account_json:
            if os.path.exists(settings.google_service_account_json):
                creds = service_account.Credentials.from_service_account_file(
                    settings.google_service_account_json, scopes=scopes
                )
            else:
                # Assume it's JSON content
                info = json.loads(settings.google_service_account_json)
                creds = service_account.Credentials.from_service_account_info(info, scopes=scopes)
        else:
            raise RuntimeError("Google credentials not configured")

        # Build service
        service = build("sheets", "v4", credentials=creds, cache_discovery=False)
        sheet = service.spreadsheets()

        # Read data
        tab = settings.offers_worksheet
        needs_quotes = any(ch in tab for ch in " +-&()!./")
        sheet_ref = f"'{tab}'" if needs_quotes else tab
        range_str = f"{sheet_ref}!A1:K2000"

        resp = sheet.values().get(
            spreadsheetId=settings.offers_sheet_id,
            range=range_str,
            valueRenderOption="UNFORMATTED_VALUE",
        ).execute()

        values = resp.get("values", [])
        if not values:
            return 0

        # Parse into DataFrame
        header = [str(h).strip() for h in values[0]]
        rows = values[1:]

        # Pad rows to match header length
        for r in rows:
            if len(r) < len(header):
                r.extend([""] * (len(header) - len(r)))

        df = pd.DataFrame(rows, columns=header)
        df = df.replace("", pd.NA).dropna(how="all")

        # Normalize columns
        df = df.rename(columns={c: _map_header(c) for c in df.columns})

        # Ensure required columns exist
        required_cols = [
            "affiliate_offer", "offer_text", "states", "terms",
            "bonus_code", "page_type", "shortcode", "switchboard_link"
        ]
        for col in required_cols:
            if col not in df.columns:
                df[col] = ""

        # Fill NaN with empty string
        df = df.fillna("")

        # Filter empty rows
        df = df[df["affiliate_offer"].astype(str).str.strip().ne("")]

        # Process and save to database
        count = 0
        for _, row in df.iterrows():
            brand = _parse_brand(row.get("affiliate_offer", ""))
            states = _parse_states(row.get("states", ""))

            offer_id = Offer.generate_id(
                brand,
                str(row.get("affiliate_offer", "")).strip(),
                str(row.get("bonus_code", "")).strip(),
            )

            # Check if exists
            existing = await db.execute(
                select(Offer).where(Offer.id == offer_id)
            )
            offer = existing.scalar_one_or_none()

            if offer:
                # Update
                offer.brand = brand
                offer.affiliate_offer = str(row.get("affiliate_offer", "")).strip()
                offer.offer_text = str(row.get("offer_text", "")).strip()
                offer.bonus_code = str(row.get("bonus_code", "")).strip()
                offer.states = states
                offer.terms = str(row.get("terms", "")).strip()
                offer.page_type = str(row.get("page_type", "")).strip()
                offer.shortcode = str(row.get("shortcode", "")).strip()
                offer.switchboard_link = str(row.get("switchboard_link", "")).strip()
                offer.synced_at = datetime.utcnow()
            else:
                # Create
                offer = Offer(
                    id=offer_id,
                    brand=brand,
                    affiliate_offer=str(row.get("affiliate_offer", "")).strip(),
                    offer_text=str(row.get("offer_text", "")).strip(),
                    bonus_code=str(row.get("bonus_code", "")).strip(),
                    states=states,
                    terms=str(row.get("terms", "")).strip(),
                    page_type=str(row.get("page_type", "")).strip(),
                    shortcode=str(row.get("shortcode", "")).strip(),
                    switchboard_link=str(row.get("switchboard_link", "")).strip(),
                )
                db.add(offer)

            count += 1

        await db.flush()
        _last_sync = datetime.utcnow()
        return count

    except Exception as e:
        print(f"Failed to sync offers from sheets: {e}")
        raise


async def get_offers(
    db: AsyncSession,
    state: str | None = None,
    brand: str | None = None,
    force_sync: bool = False,
) -> list[Offer]:
    """Get offers from database, optionally syncing first."""
    global _last_sync

    # Auto-sync if cache expired
    if force_sync or _last_sync is None or datetime.utcnow() - _last_sync > CACHE_DURATION:
        try:
            await sync_offers_from_sheets(db)
        except Exception:
            pass  # Fall back to existing DB data

    # Query
    query = select(Offer).order_by(Offer.brand)

    if brand:
        query = query.where(Offer.brand == brand)

    result = await db.execute(query)
    offers = list(result.scalars().all())

    # Filter by state in Python (JSON column)
    if state and state != "ALL":
        offers = [
            o for o in offers
            if state in (o.states or []) or "ALL" in (o.states or [])
        ]

    return offers


async def get_offer_by_id(db: AsyncSession, offer_id: str) -> Optional[Offer]:
    """Get a single offer by ID."""
    result = await db.execute(select(Offer).where(Offer.id == offer_id))
    return result.scalar_one_or_none()


def render_offer_block(offer: Offer | dict[str, Any], placement: str = "inline") -> str:
    """Render offer as markdown CTA block."""

    def _get(key: str, alt: str | None = None) -> str:
        if isinstance(offer, Offer):
            val = getattr(offer, key, None)
        else:
            val = offer.get(key)
            if not val and alt:
                val = offer.get(alt)
        return (str(val) if val else "").strip()

    brand = _get("brand")
    headline = _get("offer_text") or _get("affiliate_offer")
    code = _get("bonus_code")
    url = _get("switchboard_link") or _get("url") or "#"
    terms = _get("terms")

    title = f"**{brand} Promo**" if brand else "**Promo**"
    code_line = f"\n**Bonus code:** `{code}`" if code else ""
    link_line = f"\n[Claim Offer]({url})" if url and url != "#" else ""

    block = f"> {title}  \n> {headline}  \n{code_line}{link_line}\n"

    if terms:
        block += f"\n<details><summary>Terms apply</summary><p>{terms}</p></details>\n"

    block += "\n21+. Gambling problem? Call 1-800-GAMBLER. Please bet responsibly.\n"
    block = re.sub(r"\n{3,}", "\n\n", block).strip() + "\n"

    return block


def default_title_for_offer(offer: Offer | dict[str, Any]) -> str:
    """Generate a default title for an offer."""
    if isinstance(offer, Offer):
        brand = offer.brand or ""
        main = offer.offer_text or offer.affiliate_offer or ""
    else:
        brand = (offer.get("brand") or "").strip()
        main = (offer.get("offer_text") or offer.get("affiliate_offer") or "").strip()

    if brand and main:
        return f"{brand} Promo: {main}"
    return main or brand or "Sportsbook Promo"
