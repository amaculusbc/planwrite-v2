"""Style profile model for content generation templates."""

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class StyleProfile(Base):
    """Saved style profile for content generation."""

    __tablename__ = "style_profiles"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)

    reading_level: Mapped[str] = mapped_column(String(50), default="Grade 8-10")
    paragraph_sentences: Mapped[str] = mapped_column(String(20), default="2-4")
    list_density: Mapped[str] = mapped_column(String(20), default="low")
    tone: Mapped[str] = mapped_column(String(200), default="clear, direct, non-hype")

    custom_instructions: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    is_default: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<StyleProfile(name='{self.name}', is_default={self.is_default})>"

    def to_constraints(self) -> dict:
        """Convert to constraints dict for prompt generation."""
        return {
            "reading_level": self.reading_level,
            "paragraph_target_sentences": self.paragraph_sentences,
            "list_density": self.list_density,
            "tone": self.tone,
            "custom_instructions": self.custom_instructions,
        }
