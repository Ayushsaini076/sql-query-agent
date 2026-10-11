import pytest

from app.agent.llm import create_chat_model
from app.config import Settings


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ("LLM_PROVIDER", "LLM_MODEL", "LLM_FAST_MODEL", "GOOGLE_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("provider", ["gemini", "Google", "google_genai"])
def test_gemini_provider_aliases(provider):
    settings = Settings(_env_file=None, llm_provider=provider)
    assert settings.llm_provider == "google_genai"
    assert settings.resolved_model == "gemini-3.8-flash"
    assert settings.resolved_fast_model == "gemini-3.5-flash-lite"


@pytest.mark.parametrize("env_name", ["GEMINI_API_KEY", "GOOGLE_API_KEY"])
def test_gemini_key_from_env(monkeypatch, env_name):
    monkeypatch.setenv(env_name, "test-key")
    settings = Settings(_env_file=None, llm_provider="gemini")
    assert settings.google_api_key == "test-key"
    assert settings.llm_configured


def test_gemini_not_configured_without_key():
    assert not Settings(_env_file=None, llm_provider="gemini").llm_configured


def test_gemini_chat_models_use_thinking_level():
    settings = Settings(_env_file=None, llm_provider="gemini", google_api_key="test-key")
    main, fast = create_chat_model(settings), create_chat_model(settings, fast=True)
    assert (main.model, main.thinking_level) == ("gemini-3.8-flash", "medium")
    assert (fast.model, fast.thinking_level) == ("gemini-3.5-flash-lite", "low")


def test_gemini_2_5_skips_thinking_level():
    settings = Settings(_env_file=None, llm_provider="gemini", google_api_key="k", llm_model="gemini-2.5-flash")
    assert create_chat_model(settings).thinking_level is None


def test_groq_defaults_and_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    settings = Settings(_env_file=None, llm_provider="groq")
    assert (settings.resolved_model, settings.resolved_fast_model) == ("openai/gpt-oss-120b", "openai/gpt-oss-20b")
    assert not settings.llm_configured
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    assert Settings(_env_file=None, llm_provider="groq").llm_configured


def test_groq_chat_models_use_reasoning_effort():
    settings = Settings(_env_file=None, llm_provider="groq", groq_api_key="test-key")
    main, fast = create_chat_model(settings), create_chat_model(settings, fast=True)
    assert (main.model_name, main.reasoning_effort) == ("openai/gpt-oss-120b", "medium")
    assert (fast.model_name, fast.reasoning_effort) == ("openai/gpt-oss-20b", "low")


def test_groq_non_gpt_oss_skips_reasoning_effort():
    settings = Settings(_env_file=None, llm_provider="groq", groq_api_key="k", llm_model="qwen/qwen3.8-27b")
    assert create_chat_model(settings).reasoning_effort is None
