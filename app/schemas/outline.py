"""Outline and draft generation schemas."""

from typing import Optional, Any

from pydantic import BaseModel


class GameContext(BaseModel):
    """Game context for content generation."""

    event_type: Optional[str] = None
    custom_event: Optional[str] = None
    event_date: Optional[str] = None
    sport: Optional[str] = None
    away_team: Optional[str] = None
    home_team: Optional[str] = None
    start_time: Optional[str] = None
    network: Optional[str] = None
    headline: Optional[str] = None
    odds: Optional[dict] = None
    bet_example: Optional[str] = None
    bet_example_data: Optional[dict[str, Any]] = None


class ArticlePreferences(BaseModel):
    """Writer-controlled article settings for structure, links, and voice."""

    secondary_keywords: Optional[list[str]] = None
    preferred_internal_urls: Optional[list[str]] = None
    section_count: Optional[int] = None
    allow_h3: Optional[bool] = None
    include_daily_promos: Optional[bool] = None
    include_bullets: Optional[bool] = None
    include_table: Optional[bool] = None
    enforce_active_voice: Optional[bool] = None
    structure_notes: Optional[str] = None


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
    article_preferences: Optional[ArticlePreferences] = None


class DraftRequest(BaseModel):
    """Request schema for draft generation."""

    keyword: str
    title: str
    outline_tokens: Optional[list[str]] = None
    outline_text: Optional[str] = None
    outline_structured: Optional[list[dict[str, Any]]] = None
    offer_id: Optional[str] = None
    offer_property: Optional[str] = None
    alt_offer_ids: Optional[list[str]] = None
    state: str = "ALL"
    style_profile_id: Optional[int] = None
    game_context: Optional[GameContext] = None
    article_preferences: Optional[ArticlePreferences] = None


class ValidationResult(BaseModel):
    """Response schema for content validation."""

    valid: bool
    issues: list[dict]
    word_count: int
    compliance_score: float
