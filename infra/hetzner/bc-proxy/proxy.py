"""BC Core proxy."""

from __future__ import annotations

import os
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request, Response

UPSTREAM_HOST = os.environ.get("BC_CORE_UPSTREAM", "core-external-api.actionnetwork.com")
UPSTREAM_BASE = f"https://{UPSTREAM_HOST}"
BC_API_KEY = (os.environ.get("BC_CORE_API_KEY") or "").strip()
UPSTREAM_TIMEOUT = float(os.environ.get("BC_CORE_TIMEOUT_SECONDS") or "30")

if not BC_API_KEY:
    raise RuntimeError("BC_CORE_API_KEY env var is required.")

HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "host",
    "authorization", "x-api-key", "content-length", "content-encoding",
}

app = FastAPI(title="BC Core proxy", docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    return {"status": "ok"}


@app.get("/healthz/upstream")
async def healthz_upstream() -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=UPSTREAM_TIMEOUT) as client:
        try:
            r = await client.get(
                f"{UPSTREAM_BASE}/league-types",
                headers={"X-Api-Key": BC_API_KEY, "User-Agent": "bc-proxy/healthz"},
            )
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"upstream_unreachable: {exc}") from exc
    if r.status_code != 200:
        raise HTTPException(status_code=503, detail=f"upstream_status_{r.status_code}")
    return {"status": "ok", "upstream_status": r.status_code}


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
async def proxy(path: str, request: Request) -> Response:
    forward_headers = {
        k: v for k, v in request.headers.items() if k.lower() not in HOP_BY_HOP
    }
    forward_headers["X-Api-Key"] = BC_API_KEY
    forward_headers.setdefault("User-Agent", "bc-proxy/1.0")

    body = await request.body()
    upstream_url = f"{UPSTREAM_BASE}/{path}"

    async with httpx.AsyncClient(timeout=UPSTREAM_TIMEOUT) as client:
        try:
            upstream_response = await client.request(
                request.method,
                upstream_url,
                params=request.query_params,
                headers=forward_headers,
                content=body if body else None,
            )
        except httpx.RequestError as exc:
            raise HTTPException(status_code=502, detail=f"upstream_error: {exc}") from exc

    response_headers = {
        k: v for k, v in upstream_response.headers.items()
        if k.lower() not in HOP_BY_HOP
    }
    return Response(
        content=upstream_response.content,
        status_code=upstream_response.status_code,
        headers=response_headers,
        media_type=upstream_response.headers.get("content-type"),
    )
