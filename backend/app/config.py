"""Application settings, loaded from environment variables (or a .env file)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent

# Sensible per-provider defaults; override with LLM_MODEL.
DEFAULT_MODELS = {
    "anthropic": "claude-opus-5-5",
    "openai": "gpt-4.1",
    "google_genai": "gemini-3.8-flash",  # free tier available
    "groq": "openai/gpt-oss-120b",  # free tier available
}
# Per-provider defaults for LLM_FAST_MODEL; providers not listed reuse the main model.
# Gemini and Groq free-tier quotas are per model, so a second model also spreads the load.
DEFAULT_FAST_MODELS = {
    "google_genai": "gemini-3.5-flash-lite",
    "groq": "openai/gpt-oss-20b",
}
# Friendly names accepted for LLM_PROVIDER, mapped to init_chat_model provider ids.
PROVIDER_ALIASES = {"gemini": "google_genai", "google": "google_genai"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", BACKEND_DIR / ".env"), extra="ignore")

    # --- LLM -----------------------------------------------------------------
    llm_provider: str = "anthropic"  # any provider supported by langchain's init_chat_model
    llm_model: str | None = None  # defaults to DEFAULT_MODELS[llm_provider]
    llm_fast_model: str | None = None  # optional cheaper model for routing; defaults to llm_model
    llm_effort: str | None = "medium"  # Claude effort / Gemini 3+ thinking_level / Groq reasoning_effort
    llm_fast_effort: str | None = "low"  # effort for routing / explanation calls
    llm_timeout_s: float = 60.0
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    google_api_key: str | None = Field(default=None, validation_alias=AliasChoices("google_api_key", "gemini_api_key"))
    groq_api_key: str | None = None

    # --- Agent ---------------------------------------------------------------
    max_sql_attempts: int = 3  # generation + self-correction rounds
    history_turns: int = 6  # conversation turns shown to the LLM
    max_input_chars: int = 4000

    # --- Database ------------------------------------------------------------
    database_path: Path = BACKEND_DIR / "data" / "acme.db"
    checkpoint_path: Path = BACKEND_DIR / "data" / "checkpoints.db"
    max_result_rows: int = 200
    query_timeout_s: float = 5.0

    # --- API -----------------------------------------------------------------
    rate_limit_per_minute: int = 20
    # Number of reverse proxies in front of the app that append to X-Forwarded-For (Render: 1).
    # 0 = use the socket peer address; client-supplied header values are never trusted.
    trusted_proxy_hops: int = 0
    cors_origins: list[str] = ["http://localhost:5173"]
    static_dir: Path = BACKEND_DIR / "static"

    @field_validator("llm_provider")
    @classmethod
    def _normalize_provider(cls, value: str) -> str:
        value = value.strip().lower()
        return PROVIDER_ALIASES.get(value, value)

    @property
    def resolved_model(self) -> str:
        return self.llm_model or DEFAULT_MODELS.get(self.llm_provider, "")

    @property
    def resolved_fast_model(self) -> str:
        return self.llm_fast_model or DEFAULT_FAST_MODELS.get(self.llm_provider) or self.resolved_model

    @property
    def llm_configured(self) -> bool:
        if self.llm_provider == "anthropic":
            return bool(self.anthropic_api_key)
        if self.llm_provider == "openai":
            return bool(self.openai_api_key)
        if self.llm_provider == "google_genai":
            return bool(self.google_api_key)
        if self.llm_provider == "groq":
            return bool(self.groq_api_key)
        return bool(self.resolved_model)  # other providers: trust their own env vars


@lru_cache
def get_settings() -> Settings:
    return Settings()
