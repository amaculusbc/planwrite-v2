"""Outline and draft generation schemas."""

from typing import Optional

from pydantic import BaseModel


class OutlineRequest(BaseModel):
    """Request schema for outline generation."""

    keyword: str
    title: str
    offer_id: Optional[str] = None
    state: str = "ALL"
    competitor_urls: Optional[list[str]] = None
    style_profile_id: Optional[int] = None


class DraftRequest(BaseModel):
    """Request schema for draft generation."""

    keyword: str
    title: str
    outline_tokens: list[str]
    offer_id: Optional[str] = None
    state: str = "ALL"
    style_profile_id: Optional[int] = None


class ValidationResult(BaseModel):
    """Response schema for content validation."""

    valid: bool
    issues: list[dict]
    word_count: int
    compliance_score: float
