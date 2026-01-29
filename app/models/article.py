"""Article and ArticleVersion models."""

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, Float, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Article(Base):
    """Main article model with outline and draft content."""

    __tablename__ = "articles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    keyword: Mapped[str] = mapped_column(String(200), nullable=False)
    state: Mapped[str] = mapped_column(String(10), default="ALL")
    offer_id: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    offer_property: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    outline: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    draft: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft, published, archived
    compliance_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    word_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    versions: Mapped[list["ArticleVersion"]] = relationship(
        "ArticleVersion", back_populates="article", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Article(id={self.id}, title='{self.title[:30]}...', status='{self.status}')>"


class ArticleVersion(Base):
    """Version history for articles."""

    __tablename__ = "article_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    article_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("articles.id"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)

    outline: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    draft: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    # Relationships
    article: Mapped["Article"] = relationship("Article", back_populates="versions")

    def __repr__(self) -> str:
        return f"<ArticleVersion(article_id={self.article_id}, version={self.version})>"
