"""Offer model for cached sportsbook offers."""

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, String, Text, JSON, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Offer(Base):
    """Cached offer from Google Sheets or admin UI."""

    __tablename__ = "offers"

    # Composite ID: brand|affiliate_offer|bonus_code
    id: Mapped[str] = mapped_column(String(500), primary_key=True)

    brand: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    affiliate_offer: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    offer_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    bonus_code: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    # States as JSON array: ["NY", "NJ", "PA"] or ["ALL"]
    states: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)

    terms: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    page_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    shortcode: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    switchboard_link: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    synced_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<Offer(id='{self.id}', brand='{self.brand}')>"

    @classmethod
    def generate_id(cls, brand: str, affiliate_offer: str, bonus_code: str) -> str:
        """Generate composite ID from components."""
        parts = [brand or "", affiliate_offer or "", bonus_code or ""]
        return "|".join(parts)
