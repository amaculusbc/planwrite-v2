"""Content generation endpoints with SSE streaming."""

import json
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.outline import OutlineRequest, DraftRequest

router = APIRouter()


async def stream_outline(request: OutlineRequest) -> AsyncGenerator[str, None]:
    """Generate outline with streaming response."""
    # TODO: Implement actual LLM streaming
    # This will be ported from v1's prompt_factory.py

    # Placeholder streaming response
    yield f"data: {json.dumps({'type': 'status', 'message': 'Querying RAG index...'})}\n\n"

    yield f"data: {json.dumps({'type': 'status', 'message': 'Generating outline...'})}\n\n"

    # Simulated outline tokens
    tokens = [
        "[INTRO]",
        "[SHORTCODE]",
        f"[H2: What is {request.keyword}?]",
        f"[H2: How to Use {request.keyword}]",
        "[H3: Step 1]",
        "[H3: Step 2]",
        "[H2: Tips and Strategies]",
        "[H2: Frequently Asked Questions]",
    ]

    for token in tokens:
        yield f"data: {json.dumps({'type': 'token', 'content': token})}\n\n"

    yield f"data: {json.dumps({'type': 'done', 'outline': tokens})}\n\n"


async def stream_draft(request: DraftRequest) -> AsyncGenerator[str, None]:
    """Generate draft with streaming response."""
    # TODO: Implement actual LLM streaming
    # This will be ported from v1's prompt_factory.py

    yield f"data: {json.dumps({'type': 'status', 'message': 'Processing outline...'})}\n\n"

    for i, section in enumerate(request.outline_tokens):
        yield f"data: {json.dumps({'type': 'status', 'message': f'Generating section {i+1}/{len(request.outline_tokens)}...'})}\n\n"

        # Placeholder section content
        content = f"\n\n## {section.replace('[H2: ', '').replace(']', '')}\n\nSection content goes here...\n"
        yield f"data: {json.dumps({'type': 'content', 'section': section, 'content': content})}\n\n"

    yield f"data: {json.dumps({'type': 'done'})}\n\n"


@router.post("/outline")
async def generate_outline(request: OutlineRequest):
    """Generate article outline with streaming SSE response."""
    return StreamingResponse(
        stream_outline(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/draft")
async def generate_draft(request: DraftRequest):
    """Generate article draft with streaming SSE response."""
    return StreamingResponse(
        stream_draft(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/validate")
async def validate_content(
    content: str,
    db: AsyncSession = Depends(get_db),
):
    """Validate content for compliance issues."""
    # TODO: Implement compliance validation
    # This will be ported from v1's validators.py

    issues = []

    # Basic banned phrases check
    banned = ["guaranteed", "risk-free", "surefire", "can't lose"]
    for phrase in banned:
        if phrase.lower() in content.lower():
            issues.append({
                "type": "banned_phrase",
                "phrase": phrase,
                "severity": "error",
            })

    # Word count
    word_count = len(content.split())

    return {
        "valid": len([i for i in issues if i["severity"] == "error"]) == 0,
        "issues": issues,
        "word_count": word_count,
        "compliance_score": max(0, 100 - len(issues) * 10),
    }
