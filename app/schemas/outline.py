"""Outline and draft generation schemas."""

from typing import Optional, Any

from pydantic import BaseModel


class GameContext(BaseModel):
    """Game context for content generation."""

    sport: Optional[str] = None
    away_team: Optional[str] = None
    home_team: Optional[str] = None
    start_time: Optional[str] = None
    network: Optional[str] = None
    headline: Optional[str] = None
    odds: Optional[dict] = None
    bet_example: Optional[str] = None


class OutlineRequest(BaseModel):
    """Request schema for outline generation."""

    keyword: str
    title: str
    offer_id: Optional[str] = None
    offer_property: Optional[str] = None
    alt_offer_ids: Optional[list[str]] = None
    state: str = "ALL"
    competitor_urls: Optional[list[str]] = None
    style_profile_id: Optional[int] = None
    game_context: Optional[GameContext] = None


class DraftRequest(BaseModel):
    """Request schema for draft generation."""

    keyword: str
    title: str
    outline_tokens: list[str]
    offer_id: Optional[str] = None
    offer_property: Optional[str] = None
    alt_offer_ids: Optional[list[str]] = None
    state: str = "ALL"
    style_profile_id: Optional[int] = None
    game_context: Optional[GameContext] = None


class ValidationResult(BaseModel):
    """Response schema for content validation."""

    valid: bool
    issues: list[dict]
    word_count: int
    compliance_score: float
