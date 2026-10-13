"""LLM service: Anthropic Claude for generation, OpenAI for embeddings only.

Completions, structured output, and streaming run on Anthropic's Messages API.
Embeddings have no Anthropic equivalent, so they stay on OpenAI; every caller
wraps embedding use in try/except, so a missing or invalid OpenAI key degrades
RAG style-reference but never breaks generation.
"""

from typing import AsyncGenerator, Callable, Any

import json
import re

import asyncio

import structlog
from anthropic import (
    AsyncAnthropic,
    APIError,
    APIConnectionError,
    APITimeoutError,
    RateLimitError,
    InternalServerError,
)

from app.config import get_settings

logger = structlog.get_logger()
settings = get_settings()

# Anthropic client for all generation.
client = AsyncAnthropic(api_key=settings.anthropic_api_key)

# OpenAI is lazy-initialized and used ONLY for embeddings. A bad/empty OpenAI
# key must never block import or generation.
_openai_client = None


def _get_openai_client():
    global _openai_client
    if _openai_client is None:
        from openai import AsyncOpenAI
        _openai_client = AsyncOpenAI(api_key=settings.openai_api_key)
    return _openai_client


# --- token usage + cost accounting (per request) ---
# A ContextVar scopes accounting to one request so concurrent generations never cross-count.
# reset_token_usage() installs a fresh dict at the top of a handler; the pipeline's gather'd
# section calls inherit the same dict object and mutate it in place, so their tokens roll up.
from contextvars import ContextVar  # noqa: E402


def _empty_usage() -> dict:
    return {"calls": 0, "prompt_tokens": 0, "cached_input_tokens": 0,
            "completion_tokens": 0, "total_tokens": 0, "by_op": {}}


_TOKEN_USAGE: ContextVar[dict] = ContextVar("_TOKEN_USAGE", default=None)


def reset_token_usage() -> None:
    _TOKEN_USAGE.set(_empty_usage())


def usage_cost_usd(usage: dict) -> float:
    """USD cost of a usage dict at the configured rates (cached input billed at the cheap rate)."""
    cached = int(usage.get("cached_input_tokens", 0) or 0)
    uncached_input = max(0, int(usage.get("prompt_tokens", 0) or 0) - cached)
    out = int(usage.get("completion_tokens", 0) or 0)
    return round(
        uncached_input / 1_000_000 * settings.llm_price_input_per_m
        + cached / 1_000_000 * settings.llm_price_cached_input_per_m
        + out / 1_000_000 * settings.llm_price_output_per_m,
        6,
    )


def get_token_usage() -> dict:
    acc = _TOKEN_USAGE.get()
    acc = dict(acc) if acc else _empty_usage()
    acc["cost_usd"] = usage_cost_usd(acc)
    return acc


def _record_usage(op: str, model: str, usage: Any) -> None:
    """Roll Anthropic usage into the per-request accumulator.

    Anthropic splits input into fresh/cache-read/cache-write; `prompt_tokens` holds the
    full input (so uncached = prompt - cached for the cost model), `cached_input_tokens`
    is the cheap cache-read portion, `completion_tokens` is the output.
    """
    inp = int(getattr(usage, "input_tokens", 0) or 0)
    out = int(getattr(usage, "output_tokens", 0) or 0)
    cache_read = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
    cache_write = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
    prompt = inp + cache_read + cache_write
    total = prompt + out
    acc = _TOKEN_USAGE.get()
    if acc is not None:
        acc["calls"] += 1
        acc["prompt_tokens"] += prompt
        acc["cached_input_tokens"] += cache_read
        acc["completion_tokens"] += out
        acc["total_tokens"] += total
        acc["by_op"][op] = acc["by_op"].get(op, 0) + total
    logger.info("llm_usage", op=op, model=model, input_tokens=inp,
                cached_tokens=cache_read, output_tokens=out, total_tokens=total)


RETRYABLE_ERRORS = (RateLimitError, APITimeoutError, APIConnectionError, InternalServerError, APIError)
MAX_RETRIES = 3
BASE_BACKOFF = 0.5

# Copywriting needs voice, not deliberation: keep reasoning light. Headroom above the
# caller's requested budget so adaptive thinking never truncates the visible answer.
_THINKING_HEADROOM = 2000


def _request_kwargs(model: str, max_tokens: int) -> dict:
    """Shared Messages API params. Claude reasoning models reject `temperature`;
    depth is controlled with effort instead (low = voice over deliberation)."""
    return {
        "max_tokens": max_tokens + _THINKING_HEADROOM,
        "output_config": {"effort": "low"},
    }


def _text_of(response: Any) -> str:
    """Join the visible text blocks of a Messages response (ignores thinking blocks)."""
    parts = []
    for block in getattr(response, "content", None) or []:
        if getattr(block, "type", None) == "text":
            parts.append(getattr(block, "text", "") or "")
    return "".join(parts)


_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def _parse_json_loose(text: str) -> Any:
    """Parse a JSON object from model text, tolerating code fences or surrounding prose."""
    cleaned = _JSON_FENCE_RE.sub("", (text or "").strip()).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(cleaned[start:end + 1])
        raise


async def _with_retries(op_name: str, fn: Callable[[], Any]) -> Any:
    """Run an Anthropic request with simple retry/backoff on transient errors."""
    last_exc: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            return await fn()
        except RETRYABLE_ERRORS as exc:
            last_exc = exc
            logger.warning("Anthropic request failed", op=op_name, attempt=attempt + 1,
                           retries=MAX_RETRIES, error=str(exc))
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(BASE_BACKOFF * (2 ** attempt))
                continue
            raise
    if last_exc:
        raise last_exc
    return None


async def generate_completion(
    prompt: str,
    system_prompt: str = "You are a helpful assistant.",
    model: str | None = None,
    temperature: float = 0.3,  # accepted for call-site compatibility; Claude reasoning models reject it
    max_tokens: int = 4096,
) -> str:
    """Generate a completion (non-streaming)."""
    model = model or settings.llm_model

    async def _call():
        return await client.messages.create(
            model=model,
            system=system_prompt,
            messages=[{"role": "user", "content": prompt}],
            **_request_kwargs(model, max_tokens),
        )

    response = await _with_retries("messages.create", _call)
    _record_usage("completion", model, getattr(response, "usage", None))
    return _text_of(response)


async def generate_completion_structured(
    prompt: str,
    schema: dict,
    system_prompt: str = "You are a helpful assistant.",
    model: str | None = None,
    temperature: float = 0.3,
    max_tokens: int = 2048,
    name: str = "structured_output",
    description: str | None = None,
    strict: bool = True,
) -> Any:
    """Generate a structured JSON response that conforms to the given JSON Schema."""
    model = model or settings.llm_model
    schema_instruction = (
        "\n\nReturn ONLY a single valid JSON object that conforms to this JSON Schema. "
        "Do not include any prose, explanation, or markdown code fences.\n"
        f"JSON Schema:\n{json.dumps(schema)}"
    )

    async def _call():
        return await client.messages.create(
            model=model,
            system=system_prompt,
            messages=[{"role": "user", "content": prompt + schema_instruction}],
            **_request_kwargs(model, max_tokens),
        )

    resp = await _with_retries("messages.structured", _call)
    _record_usage("structured", model, getattr(resp, "usage", None))
    content = _text_of(resp)
    try:
        return _parse_json_loose(content)
    except json.JSONDecodeError:
        logger.warning("Structured output JSON parse failed", content=content[:500])
        raise


async def generate_completion_streaming(
    prompt: str,
    system_prompt: str = "You are a helpful assistant.",
    model: str | None = None,
    temperature: float = 0.3,
    max_tokens: int = 4096,
) -> AsyncGenerator[str, None]:
    """Generate a completion with streaming response (yields visible text deltas)."""
    model = model or settings.llm_model
    async with client.messages.stream(
        model=model,
        system=system_prompt,
        messages=[{"role": "user", "content": prompt}],
        **_request_kwargs(model, max_tokens),
    ) as stream:
        async for text in stream.text_stream:
            yield text
        final = await stream.get_final_message()
        _record_usage("streaming", model, getattr(final, "usage", None))


async def get_embedding(text: str, model: str | None = None) -> list[float]:
    """Get embedding vector for text (OpenAI; Anthropic has no embeddings API)."""
    model = model or settings.embed_model
    oai = _get_openai_client()

    async def _call_embed():
        return await oai.embeddings.create(model=model, input=text)

    response = await _call_embed()
    return response.data[0].embedding


async def get_embeddings_batch(texts: list[str], model: str | None = None) -> list[list[float]]:
    """Get embedding vectors for multiple texts (OpenAI; Anthropic has no embeddings API)."""
    model = model or settings.embed_model
    oai = _get_openai_client()

    async def _call_embed_batch():
        return await oai.embeddings.create(model=model, input=texts)

    response = await _call_embed_batch()
    return [item.embedding for item in response.data]
