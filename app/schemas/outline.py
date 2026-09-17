"""Outline and draft generation schemas."""

from typing import Optional, Any

from pydantic import BaseModel, model_validator

# Properties that only run in the Canadian market. Generation for these coerces
# market to CA, so the batch/API path is correct even when the caller omits it.
CA_ONLY_PROPERTIES = {"csb"}


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
    market: str = "US"
    state: str = "ALL"
    competitor_urls: Optional[list[str]] = None
    style_profile_id: Optional[int] = None
    game_context: Optional[GameContext] = None
    article_preferences: Optional[ArticlePreferences] = None

    @model_validator(mode="after")
    def _default_ca_market(self):
        if (self.offer_property or "").strip().lower() in CA_ONLY_PROPERTIES:
            self.market = "CA"
        return self


class DraftRequest(BaseModel):
    """Request schema for draft generation."""

    run_id: Optional[str] = None
    keyword: str
    title: str
    outline_tokens: Optional[list[str]] = None
    outline_text: Optional[str] = None
    outline_structured: Optional[list[dict[str, Any]]] = None
    offer_id: Optional[str] = None
    offer_property: Optional[str] = None
    alt_offer_ids: Optional[list[str]] = None
    market: str = "US"
    state: str = "ALL"
    style_profile_id: Optional[int] = None
    game_context: Optional[GameContext] = None
    article_preferences: Optional[ArticlePreferences] = None

    @model_validator(mode="after")
    def _default_ca_market(self):
        if (self.offer_property or "").strip().lower() in CA_ONLY_PROPERTIES:
            self.market = "CA"
        return self


class ValidationResult(BaseModel):
    """Response schema for content validation."""

    valid: bool
    issues: list[dict]
    word_count: int
    compliance_score: float
