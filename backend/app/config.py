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
# Settings field holding each provider's API key; other providers read their own env vars.
API_KEY_FIELDS = {
    "anthropic": "anthropic_api_key",
    "openai": "openai_api_key",
    "google_genai": "google_api_key",
    "groq": "groq_api_key",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", BACKEND_DIR / ".env"), extra="ignore")

    # --- LLM -----------------------------------------------------------------
    llm_provider: str = "anthropic"  # any provider supported by langchain's init_chat_model
    llm_model: str | None = None  # defaults to DEFAULT_MODELS[llm_provider]
    llm_fast_model: str | None = None  # optional cheaper model for routing; defaults to llm_model
    llm_effort: str | None = "medium"  # Claude effort / Gemini 3+ thinking_level / Groq reasoning_effort
    llm_fast_effort: str | None = "low"  # effort for routing / explanation calls
    llm_timeout_s: float = 60.0
    # Backup provider tried when a call to the primary one fails (rate limit, timeout, outage).
    # It always uses its DEFAULT_MODELS / DEFAULT_FAST_MODELS and is skipped if its API key is missing.
    llm_fallback_provider: str | None = None
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

    @field_validator("llm_provider", "llm_fallback_provider")
    @classmethod
    def _normalize_provider(cls, value: str | None) -> str | None:
        value = (value or "").strip().lower()
        return PROVIDER_ALIASES.get(value, value) or None

    @property
    def resolved_model(self) -> str:
        return self.llm_model or DEFAULT_MODELS.get(self.llm_provider, "")

    @property
    def resolved_fast_model(self) -> str:
        return self.llm_fast_model or DEFAULT_FAST_MODELS.get(self.llm_provider) or self.resolved_model

    def fallback_model(self, *, fast: bool = False) -> str:
        provider = self.llm_fallback_provider or ""
        return (fast and DEFAULT_FAST_MODELS.get(provider)) or DEFAULT_MODELS.get(provider, "")

    def has_credentials(self, provider: str) -> bool:
        field = API_KEY_FIELDS.get(provider)
        return bool(getattr(self, field)) if field else True  # other providers: trust their own env vars

    @property
    def llm_configured(self) -> bool:
        return self.has_credentials(self.llm_provider) and bool(self.resolved_model)

    @property
    def fallback_configured(self) -> bool:
        provider = self.llm_fallback_provider
        return bool(
            provider and provider != self.llm_provider and self.has_credentials(provider) and self.fallback_model()
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
