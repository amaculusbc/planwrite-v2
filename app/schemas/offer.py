"""Offer schemas."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class OfferBase(BaseModel):
    """Base offer schema."""

    brand: str
    affiliate_offer: Optional[str] = None
    offer_text: Optional[str] = None
    bonus_code: Optional[str] = None
    states: Optional[list[str]] = None
    terms: Optional[str] = None
    page_type: Optional[str] = None
    shortcode: Optional[str] = None
    switchboard_link: Optional[str] = None


class OfferCreate(OfferBase):
    """Schema for creating an offer."""

    id: Optional[str] = None  # Will be auto-generated if not provided


class OfferResponse(OfferBase):
    """Schema for offer responses."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    synced_at: datetime
