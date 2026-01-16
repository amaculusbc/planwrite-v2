"""OpenAI LLM service with streaming support."""

from typing import AsyncGenerator

import structlog
from openai import AsyncOpenAI

from app.config import get_settings

logger = structlog.get_logger()
settings = get_settings()

# Initialize async OpenAI client
client = AsyncOpenAI(api_key=settings.openai_api_key)


async def generate_completion(
    prompt: str,
    system_prompt: str = "You are a helpful assistant.",
    model: str | None = None,
    temperature: float = 0.3,
    max_tokens: int = 4096,
) -> str:
    """Generate a completion (non-streaming)."""
    model = model or settings.llm_model

    response = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
    )

    return response.choices[0].message.content or ""


async def generate_completion_streaming(
    prompt: str,
    system_prompt: str = "You are a helpful assistant.",
    model: str | None = None,
    temperature: float = 0.3,
    max_tokens: int = 4096,
) -> AsyncGenerator[str, None]:
    """Generate a completion with streaming response."""
    model = model or settings.llm_model

    stream = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
        stream=True,
    )

    async for chunk in stream:
        if chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content


async def get_embedding(text: str, model: str | None = None) -> list[float]:
    """Get embedding vector for text."""
    model = model or settings.embed_model

    response = await client.embeddings.create(
        model=model,
        input=text,
    )

    return response.data[0].embedding


async def get_embeddings_batch(texts: list[str], model: str | None = None) -> list[list[float]]:
    """Get embedding vectors for multiple texts."""
    model = model or settings.embed_model

    response = await client.embeddings.create(
        model=model,
        input=texts,
    )

    return [item.embedding for item in response.data]
