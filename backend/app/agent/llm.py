"""Provider-agnostic chat model construction (LangChain init_chat_model)."""

from __future__ import annotations

from typing import TypeVar

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage
from pydantic import BaseModel

from app.config import Settings

T = TypeVar("T", bound=BaseModel)

# Providers with native JSON-schema structured output. Claude Opus 5.5 / Sonnet 5.5 reject
# forced tool choice, so the tool-calling fallback is only used for other providers.
_JSON_SCHEMA_PROVIDERS = {"anthropic", "openai", "google_genai", "groq"}
# Providers where strict mode (constrained decoding) must be requested explicitly.
_STRICT_SCHEMA_PROVIDERS = {"groq"}


def create_chat_model(settings: Settings, *, fast: bool = False) -> BaseChatModel:
    provider = settings.llm_provider
    model = settings.resolved_fast_model if fast else settings.resolved_model
    kwargs: dict = {"timeout": settings.llm_timeout_s, "max_retries": 2}
    if provider == "anthropic":
        # Current Claude models use adaptive thinking; effort is the depth control and
        # sampling parameters such as temperature are not accepted.
        kwargs["max_tokens"] = 16_000
        effort = settings.llm_fast_effort if fast else settings.llm_effort
        if effort:
            kwargs["effort"] = effort
        if settings.anthropic_api_key:
            kwargs["api_key"] = settings.anthropic_api_key
    elif provider == "openai":
        kwargs["temperature"] = 0
        if settings.openai_api_key:
            kwargs["api_key"] = settings.openai_api_key
    elif provider == "google_genai":
        # Gemini 3+ models control thinking depth with thinking_level (minimal/low/medium/high);
        # Gemini 2.5 models do not accept it. Google advises keeping the default temperature.
        effort = settings.llm_fast_effort if fast else settings.llm_effort
        if effort and not model.startswith("gemini-2"):
            kwargs["thinking_level"] = effort
        if settings.google_api_key:
            kwargs["api_key"] = settings.google_api_key
    elif provider == "groq":
        # gpt-oss models take reasoning_effort (low/medium/high); other Groq models may reject it.
        kwargs["temperature"] = 0
        effort = settings.llm_fast_effort if fast else settings.llm_effort
        if effort and model.startswith("openai/gpt-oss"):
            kwargs["reasoning_effort"] = effort
        if settings.groq_api_key:
            kwargs["api_key"] = settings.groq_api_key
    return init_chat_model(model, model_provider=provider, **kwargs)


def structured_call(model: BaseChatModel, schema: type[T], messages: list[BaseMessage], provider: str) -> T:
    method = "json_schema" if provider in _JSON_SCHEMA_PROVIDERS else "function_calling"
    options = {"strict": True} if provider in _STRICT_SCHEMA_PROVIDERS else {}
    result = model.with_structured_output(schema, method=method, **options).invoke(messages)
    if isinstance(result, dict):  # some providers return a dict for pydantic schemas
        result = schema.model_validate(result)
    return result


def message_text(message: BaseMessage) -> str:
    """Plain text of a model reply, ignoring thinking or tool blocks."""
    content = message.content
    if isinstance(content, str):
        return content
    return "".join(
        block.get("text", "") for block in content if isinstance(block, dict) and block.get("type") == "text"
    )
