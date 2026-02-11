"""Persistent usage tracking helpers."""

from __future__ import annotations

from datetime import datetime, timedelta
import csv
import io
import json
from typing import Optional

from sqlalchemy import desc, func, select

from app.database import async_session_maker
from app.models.usage_event import UsageEvent


def _to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    try:
        return value.isoformat()
    except Exception:
        return str(value)


async def record_usage_event(
    *,
    username: str,
    event_type: str,
    method: str | None = None,
    path: str | None = None,
    status_code: int | None = None,
    duration_ms: float | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    details: dict | str | None = None,
) -> None:
    """Persist one usage event row. Never raises."""
    try:
        details_text: str | None
        if details is None:
            details_text = None
        elif isinstance(details, str):
            details_text = details
        else:
            details_text = json.dumps(details, ensure_ascii=False)

        row = UsageEvent(
            username=(username or "anonymous").strip() or "anonymous",
            event_type=(event_type or "unknown").strip() or "unknown",
            method=(method or "").strip() or None,
            path=(path or "").strip() or None,
            status_code=status_code,
            duration_ms=duration_ms,
            ip_address=(ip_address or "").strip() or None,
            user_agent=(user_agent or "").strip()[:1024] or None,
            details=details_text,
        )
        async with async_session_maker() as session:
            session.add(row)
            await session.commit()
    except Exception:
        # Usage tracking must never break app functionality.
        return


async def list_usage_events(
    *,
    days: int = 30,
    limit: int = 200,
    username: str | None = None,
    event_type: str | None = None,
) -> list[dict]:
    """Return recent usage events."""
    cutoff = datetime.utcnow() - timedelta(days=max(1, min(days, 3650)))
    cap = max(1, min(limit, 5000))

    stmt = (
        select(UsageEvent)
        .where(UsageEvent.created_at >= cutoff)
        .order_by(desc(UsageEvent.created_at))
        .limit(cap)
    )
    if username:
        stmt = stmt.where(UsageEvent.username == username.strip())
    if event_type:
        stmt = stmt.where(UsageEvent.event_type == event_type.strip())

    async with async_session_maker() as session:
        result = await session.execute(stmt)
        rows = result.scalars().all()

    events: list[dict] = []
    for row in rows:
        events.append({
            "id": row.id,
            "created_at": _to_iso(row.created_at),
            "username": row.username,
            "event_type": row.event_type,
            "method": row.method,
            "path": row.path,
            "status_code": row.status_code,
            "duration_ms": row.duration_ms,
            "ip_address": row.ip_address,
            "user_agent": row.user_agent,
            "details": row.details,
        })
    return events


async def usage_summary(*, days: int = 30) -> dict:
    """Return aggregated usage metrics."""
    cutoff = datetime.utcnow() - timedelta(days=max(1, min(days, 3650)))

    async with async_session_maker() as session:
        total_stmt = select(func.count(UsageEvent.id)).where(UsageEvent.created_at >= cutoff)
        total = int((await session.execute(total_stmt)).scalar() or 0)

        unique_users_stmt = (
            select(func.count(func.distinct(UsageEvent.username)))
            .where(UsageEvent.created_at >= cutoff)
        )
        unique_users = int((await session.execute(unique_users_stmt)).scalar() or 0)

        by_event_stmt = (
            select(UsageEvent.event_type, func.count(UsageEvent.id))
            .where(UsageEvent.created_at >= cutoff)
            .group_by(UsageEvent.event_type)
            .order_by(desc(func.count(UsageEvent.id)))
        )
        by_event_rows = (await session.execute(by_event_stmt)).all()

        by_user_stmt = (
            select(UsageEvent.username, func.count(UsageEvent.id))
            .where(UsageEvent.created_at >= cutoff)
            .group_by(UsageEvent.username)
            .order_by(desc(func.count(UsageEvent.id)))
        )
        by_user_rows = (await session.execute(by_user_stmt)).all()

        top_paths_stmt = (
            select(UsageEvent.path, func.count(UsageEvent.id))
            .where(
                UsageEvent.created_at >= cutoff,
                UsageEvent.event_type == "api_request",
                UsageEvent.path.is_not(None),
            )
            .group_by(UsageEvent.path)
            .order_by(desc(func.count(UsageEvent.id)))
            .limit(25)
        )
        top_paths_rows = (await session.execute(top_paths_stmt)).all()

    return {
        "days": days,
        "total_events": total,
        "unique_users": unique_users,
        "by_event_type": [{"event_type": row[0], "count": int(row[1])} for row in by_event_rows],
        "by_user": [{"username": row[0], "count": int(row[1])} for row in by_user_rows],
        "top_api_paths": [{"path": row[0], "count": int(row[1])} for row in top_paths_rows],
    }


async def usage_events_csv(
    *,
    days: int = 30,
    limit: int = 5000,
    username: str | None = None,
    event_type: str | None = None,
) -> str:
    """Export recent usage events as CSV."""
    events = await list_usage_events(days=days, limit=limit, username=username, event_type=event_type)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "id",
        "created_at",
        "username",
        "event_type",
        "method",
        "path",
        "status_code",
        "duration_ms",
        "ip_address",
        "user_agent",
        "details",
    ])
    for row in events:
        writer.writerow([
            row.get("id"),
            row.get("created_at"),
            row.get("username"),
            row.get("event_type"),
            row.get("method"),
            row.get("path"),
            row.get("status_code"),
            row.get("duration_ms"),
            row.get("ip_address"),
            row.get("user_agent"),
            row.get("details"),
        ])
    return output.getvalue()
