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

RETRYABLE_ERRORS = (RateLimitError, APITimeoutError, APIError, APIConnectionError)
MAX_RETRIES = 3
BASE_BACKOFF = 0.5


def _token_param(model: str, max_tokens: int) -> dict:
    """Return the correct token limit parameter for the given model."""
    if model.startswith("gpt-5"):
        return {"max_completion_tokens": max_tokens}
    return {"max_tokens": max_tokens}


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
            temperature=temperature,
            **_token_param(model, max_tokens),
        )

    response = await _with_openai_retries("chat.completions.create", _call)

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
            temperature=temperature,
            **_token_param(model, max_tokens),
            response_format=response_format,
        )

    resp = await _with_openai_retries("chat.completions.create.structured", _call)
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
            temperature=temperature,
            **_token_param(model, max_tokens),
            stream=True,
        )

    stream = await _with_openai_retries("chat.completions.stream", _call_stream)

    async for chunk in stream:
        if chunk.choices[0].delta.content:
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
