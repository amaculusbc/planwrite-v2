"""Application configuration using Pydantic Settings."""

from functools import lru_cache
import json
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

    # Authentication
    auth_enabled: bool = True
    auth_username: str = "admin"
    auth_password: str = "change-me"
    auth_session_secret: str = "change-session-secret"
    auth_users_json: str = ""

    # Database
    database_url: str = "sqlite+aiosqlite:///./storage/planwrite.db"

    # OpenAI
    openai_api_key: str = ""
    embed_model: str = "text-embedding-3-small"
    llm_model: str = "gpt-5.2-2025-12-11"

    # Offers (BAM)
    offers_property: str = "action_network"

    # Odds (Charlotte/RotoGrinders)
    odds_api_key: str = ""

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

    @property
    def auth_users(self) -> dict[str, str]:
        """Return configured username/password pairs for login.

        Preferred format is JSON object in AUTH_USERS_JSON:
        {"admin":"...","usteam":"..."}
        Falls back to AUTH_USERNAME/AUTH_PASSWORD.
        """
        parsed: dict[str, str] = {}
        raw = (self.auth_users_json or "").strip()
        if raw:
            try:
                data = json.loads(raw)
                if isinstance(data, dict):
                    for username, password in data.items():
                        user = str(username).strip()
                        secret = str(password)
                        if user and secret:
                            parsed[user] = secret
            except Exception:
                # Fallback to single-user auth settings below.
                parsed = {}

        if parsed:
            return parsed

        user = (self.auth_username or "").strip()
        secret = self.auth_password or ""
        if user and secret:
            return {user: secret}
        return {}


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
