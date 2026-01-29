"""Application configuration using Pydantic Settings."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # App
    debug: bool = False
    secret_key: str = "change-me-in-production"

    # Database
    database_url: str = "sqlite+aiosqlite:///./storage/planwrite.db"

    # OpenAI
    openai_api_key: str = ""
    embed_model: str = "text-embedding-3-small"
    llm_model: str = "gpt-4o-mini"

    # Offers (BAM)
    offers_property: str = "action_network"

    # Paths
    @property
    def base_dir(self) -> Path:
        return Path(__file__).parent.parent

    @property
    def data_dir(self) -> Path:
        return self.base_dir / "data"

    @property
    def storage_dir(self) -> Path:
        return self.base_dir / "storage"

    @property
    def templates_dir(self) -> Path:
        return self.base_dir / "app" / "templates"

    @property
    def static_dir(self) -> Path:
        return self.base_dir / "app" / "static"


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
