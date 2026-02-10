"""HTTP utility helpers with retry/backoff."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import structlog

logger = structlog.get_logger()


async def get_json(
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
    timeout: float = 10.0,
    retries: int = 3,
    backoff: float = 0.5,
) -> Any:
    """Fetch JSON from a URL with simple retry/backoff."""
    last_exc: Exception | None = None

    for attempt in range(retries):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(url, params=params, headers=headers)
                response.raise_for_status()
                return response.json()
        except (httpx.TimeoutException, httpx.HTTPError) as exc:
            last_exc = exc
            logger.warning(
                "HTTP request failed",
                url=url,
                attempt=attempt + 1,
                retries=retries,
                error=str(exc),
            )
            if attempt < retries - 1:
                await asyncio.sleep(backoff * (2 ** attempt))
                continue
            raise

    if last_exc:
        raise last_exc
    return None
