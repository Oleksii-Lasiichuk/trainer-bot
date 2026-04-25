"""Application settings, loaded from environment via pydantic-settings."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    telegram_bot_token: str = Field(..., alias="TELEGRAM_BOT_TOKEN", min_length=1)

    groq_api_key: str = Field(..., alias="GROQ_API_KEY", min_length=1)
    groq_model_primary: str = Field("llama-3.3-70b-versatile", alias="GROQ_MODEL_PRIMARY")
    groq_model_fallback: str = Field("llama-3.1-8b-instant", alias="GROQ_MODEL_FALLBACK")
    groq_base_url: str = Field("https://api.groq.com/openai/v1", alias="GROQ_BASE_URL")

    allowed_telegram_user_ids: list[int] = Field(
        default_factory=list, alias="ALLOWED_TELEGRAM_USER_IDS"
    )

    database_url: str = Field("sqlite+aiosqlite:///./data/bot.db", alias="DATABASE_URL")

    log_level: str = Field("INFO", alias="LOG_LEVEL")

    max_history_messages: int = Field(12, alias="MAX_HISTORY_MESSAGES", ge=2, le=200)
    max_tool_iterations: int = Field(5, alias="MAX_TOOL_ITERATIONS", ge=1, le=20)
    request_timeout_seconds: int = Field(30, alias="REQUEST_TIMEOUT_SECONDS", ge=5, le=300)

    # Soft cap on tokens sent to Groq per request (system + tools + history + new turn).
    # Default 9000 leaves ~3000-token headroom under the 12k TPM cap on the free 70B tier.
    groq_token_budget: int = Field(9000, alias="GROQ_TOKEN_BUDGET", ge=1000, le=200000)

    default_timezone: str = Field("Europe/Kyiv", alias="DEFAULT_TIMEZONE")

    @field_validator("allowed_telegram_user_ids", mode="before")
    @classmethod
    def _parse_id_list(cls, v: object) -> list[int]:
        if v is None or v == "":
            return []
        if isinstance(v, bool):
            raise ValueError("ALLOWED_TELEGRAM_USER_IDS cannot be a boolean")
        if isinstance(v, int):
            return [v]
        if isinstance(v, list):
            return [int(x) for x in v if str(x).strip()]
        if isinstance(v, str):
            return [int(x.strip()) for x in v.split(",") if x.strip()]
        raise ValueError(f"Cannot parse ALLOWED_TELEGRAM_USER_IDS from {v!r}")

    @property
    def whitelist_enabled(self) -> bool:
        return bool(self.allowed_telegram_user_ids)

    def is_user_allowed(self, telegram_id: int) -> bool:
        if not self.whitelist_enabled:
            return True
        return telegram_id in self.allowed_telegram_user_ids


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
