"""Article schemas."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class ArticleBase(BaseModel):
    """Base article schema."""

    title: str
    keyword: str
    state: str = "ALL"
    offer_id: Optional[str] = None
    offer_property: Optional[str] = None


class ArticleCreate(ArticleBase):
    """Schema for creating an article."""

    outline: Optional[str] = None
    draft: Optional[str] = None


class ArticleUpdate(BaseModel):
    """Schema for updating an article."""

    title: Optional[str] = None
    keyword: Optional[str] = None
    state: Optional[str] = None
    offer_id: Optional[str] = None
    offer_property: Optional[str] = None
    outline: Optional[str] = None
    draft: Optional[str] = None
    status: Optional[str] = None
    compliance_score: Optional[float] = None
    word_count: Optional[int] = None


class ArticleResponse(ArticleBase):
    """Schema for article responses."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    outline: Optional[str] = None
    draft: Optional[str] = None
    status: str
    compliance_score: Optional[float] = None
    word_count: Optional[int] = None
    created_at: datetime
    updated_at: datetime


class ArticleVersionResponse(BaseModel):
    """Schema for article version responses."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    article_id: int
    version: int
    outline: Optional[str] = None
    draft: Optional[str] = None
    created_at: datetime
