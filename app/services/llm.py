"""OpenAI LLM service with streaming support."""

from typing import AsyncGenerator, Callable, Any

import json

import asyncio

import structlog
from openai import (
    AsyncOpenAI,
    APIError,
    APIConnectionError,
    APITimeoutError,
    RateLimitError,
)

from app.config import get_settings

logger = structlog.get_logger()
settings = get_settings()

# Initialize async OpenAI client
client = AsyncOpenAI(api_key=settings.openai_api_key)

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
    acc = _TOKEN_USAGE.get()
    pt = int(getattr(usage, "prompt_tokens", 0) or 0)
    ct = int(getattr(usage, "completion_tokens", 0) or 0)
    tt = int(getattr(usage, "total_tokens", 0) or (pt + ct))
    details = getattr(usage, "prompt_tokens_details", None)
    cached = int(getattr(details, "cached_tokens", 0) or 0) if details else 0
    if acc is not None:
        acc["calls"] += 1
        acc["prompt_tokens"] += pt
        acc["cached_input_tokens"] += cached
        acc["completion_tokens"] += ct
        acc["total_tokens"] += tt
        acc["by_op"][op] = acc["by_op"].get(op, 0) + tt
    logger.info("llm_usage", op=op, model=model, prompt_tokens=pt,
                cached_tokens=cached, completion_tokens=ct, total_tokens=tt)

RETRYABLE_ERRORS = (RateLimitError, APITimeoutError, APIError, APIConnectionError)
MAX_RETRIES = 3
BASE_BACKOFF = 0.5


# gpt-5.4/5.5 are reasoning-first: they reject non-default temperature and
# spend hidden reasoning tokens from the completion budget.
_REASONING_FIRST_PREFIXES = ("gpt-5.4", "gpt-5.5")


def _token_param(model: str, max_tokens: int) -> dict:
    """Return the correct token limit parameter for the given model."""
    if model.startswith(_REASONING_FIRST_PREFIXES):
        # Headroom for hidden reasoning tokens so the visible answer never truncates.
        return {"max_completion_tokens": max_tokens + 1500}
    if model.startswith("gpt-5"):
        return {"max_completion_tokens": max_tokens}
    return {"max_tokens": max_tokens}


def _sampling_params(model: str, temperature: float) -> dict:
    """Return sampling params supported by the given model."""
    if model.startswith(_REASONING_FIRST_PREFIXES):
        # Copywriting needs voice, not deliberation - keep reasoning light.
        return {"reasoning_effort": "low"}
    return {"temperature": temperature}


async def _with_openai_retries(op_name: str, fn: Callable[[], Any]) -> Any:
    """Run an OpenAI request with simple retry/backoff."""
    last_exc: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            return await fn()
        except RETRYABLE_ERRORS as exc:
            last_exc = exc
            logger.warning(
                "OpenAI request failed",
                op=op_name,
                attempt=attempt + 1,
                retries=MAX_RETRIES,
                error=str(exc),
            )
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
    temperature: float = 0.3,
    max_tokens: int = 4096,
) -> str:
    """Generate a completion (non-streaming)."""
    model = model or settings.llm_model

    async def _call():
        return await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            **_sampling_params(model, temperature),
            **_token_param(model, max_tokens),
        )

    response = await _with_openai_retries("chat.completions.create", _call)
    _record_usage("completion", model, getattr(response, "usage", None))

    return response.choices[0].message.content or ""


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
    """Generate a structured JSON response using JSON Schema."""
    model = model or settings.llm_model

    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": name,
            "schema": schema,
            "strict": strict,
        },
    }
    if description:
        response_format["json_schema"]["description"] = description

    async def _call():
        return await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            **_sampling_params(model, temperature),
            **_token_param(model, max_tokens),
            response_format=response_format,
        )

    resp = await _with_openai_retries("chat.completions.create.structured", _call)
    _record_usage("structured", model, getattr(resp, "usage", None))
    content = resp.choices[0].message.content or ""
    try:
        return json.loads(content)
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
    """Generate a completion with streaming response."""
    model = model or settings.llm_model

    async def _call_stream():
        return await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            **_sampling_params(model, temperature),
            **_token_param(model, max_tokens),
            stream=True,
            stream_options={"include_usage": True},
        )

    stream = await _with_openai_retries("chat.completions.stream", _call_stream)

    async for chunk in stream:
        # The final include_usage chunk carries usage and an empty choices list.
        if getattr(chunk, "usage", None) is not None:
            _record_usage("streaming", model, chunk.usage)
        if chunk.choices and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content


async def get_embedding(text: str, model: str | None = None) -> list[float]:
    """Get embedding vector for text."""
    model = model or settings.embed_model

    async def _call_embed():
        return await client.embeddings.create(
            model=model,
            input=text,
        )

    response = await _with_openai_retries("embeddings.create", _call_embed)

    return response.data[0].embedding


async def get_embeddings_batch(texts: list[str], model: str | None = None) -> list[list[float]]:
    """Get embedding vectors for multiple texts."""
    model = model or settings.embed_model

    async def _call_embed_batch():
        return await client.embeddings.create(
            model=model,
            input=texts,
        )

    response = await _with_openai_retries("embeddings.create.batch", _call_embed_batch)

    return [item.embedding for item in response.data]
