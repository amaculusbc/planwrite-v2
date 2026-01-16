"""Batch job models for bulk article generation."""

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, JSON, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class BatchJob(Base):
    """Batch job for generating multiple articles."""

    __tablename__ = "batch_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    status: Mapped[str] = mapped_column(
        String(20), default="pending"
    )  # pending, running, completed, failed

    total_count: Mapped[int] = mapped_column(Integer, default=0)
    completed_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)

    # Job configuration as JSON
    config: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Relationships
    items: Mapped[list["BatchJobItem"]] = relationship(
        "BatchJobItem", back_populates="job", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<BatchJob(id={self.id}, status='{self.status}', {self.completed_count}/{self.total_count})>"

    @property
    def progress_percent(self) -> float:
        """Calculate completion percentage."""
        if self.total_count == 0:
            return 0.0
        return (self.completed_count / self.total_count) * 100


class BatchJobItem(Base):
    """Individual item in a batch job."""

    __tablename__ = "batch_job_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("batch_jobs.id"), nullable=False
    )
    article_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("articles.id"), nullable=True
    )

    keyword: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    title: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    offer_id: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    status: Mapped[str] = mapped_column(
        String(20), default="pending"
    )  # pending, running, completed, failed
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    # Relationships
    job: Mapped["BatchJob"] = relationship("BatchJob", back_populates="items")

    def __repr__(self) -> str:
        return f"<BatchJobItem(id={self.id}, job_id={self.job_id}, status='{self.status}')>"
