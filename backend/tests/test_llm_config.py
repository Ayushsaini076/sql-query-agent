import pytest

from langchain_core.messages import HumanMessage

from app.agent.llm import FallbackLLM, create_chat_model, create_models, structured_call
from app.agent.models import SQLDraft
from app.config import Settings
from tests.fakes import ScriptedLLM


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ("LLM_PROVIDER", "LLM_MODEL", "LLM_FAST_MODEL", "LLM_FALLBACK_PROVIDER",
                 "GOOGLE_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY"):
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


def _groq_with_gemini_fallback(**overrides):
    values = dict(llm_provider="groq", groq_api_key="g", llm_fallback_provider="gemini", google_api_key="k")
    return Settings(_env_file=None, **(values | overrides))


def test_fallback_uses_its_own_default_models():
    settings = _groq_with_gemini_fallback(llm_model="openai/gpt-oss-120b")
    assert settings.llm_fallback_provider == "google_genai"
    assert settings.fallback_configured
    assert settings.fallback_model() == "gemini-3.8-flash"
    assert settings.fallback_model(fast=True) == "gemini-3.5-flash-lite"


@pytest.mark.parametrize("overrides", [
    {"google_api_key": None},  # no key for the fallback provider
    {"llm_fallback_provider": "groq"},  # same as the primary
    {"llm_fallback_provider": ""},  # unset
])
def test_fallback_not_configured(overrides):
    assert not _groq_with_gemini_fallback(**overrides).fallback_configured


def test_create_models_wraps_both_roles():
    main, fast = create_models(_groq_with_gemini_fallback())
    assert isinstance(main, FallbackLLM) and isinstance(fast, FallbackLLM)
    assert (main.primary.model_name, main.fallback.model) == ("openai/gpt-oss-120b", "gemini-3.8-flash")
    assert (fast.primary.model_name, fast.fallback.model) == ("openai/gpt-oss-20b", "gemini-3.5-flash-lite")


def test_create_models_without_fallback_returns_plain_models():
    main, _ = create_models(_groq_with_gemini_fallback(google_api_key=None))
    assert not isinstance(main, FallbackLLM)


class _FailingLLM:
    """Stands in for a provider that is rate-limiting every call."""

    def __init__(self):
        self.calls = 0

    def with_structured_output(self, *_, **__):
        return self

    def invoke(self, *_, **__):
        self.calls += 1
        raise RuntimeError("RateLimitError: 429 Too Many Requests")


def test_fallback_answers_when_primary_fails():
    draft = SQLDraft(sql="SELECT 1", assumptions=[], unanswerable_reason=None)
    primary, backup = _FailingLLM(), ScriptedLLM({SQLDraft: [draft]}, text=["Backup explanation."])
    llm = FallbackLLM(primary, backup, "google_genai")
    messages = [HumanMessage("hi")]
    assert structured_call(llm, SQLDraft, messages, "groq") == draft
    assert llm.invoke(messages).content == "Backup explanation."
    assert primary.calls == 2


def test_fallback_unused_when_primary_succeeds():
    draft = SQLDraft(sql="SELECT 1", assumptions=[], unanswerable_reason=None)
    primary, backup = ScriptedLLM({SQLDraft: [draft]}), ScriptedLLM()
    assert structured_call(FallbackLLM(primary, backup, "google_genai"), SQLDraft, [HumanMessage("hi")], "groq") == draft
    assert backup.calls == []
