"""Scripted stand-in for a LangChain chat model, so the full graph runs without an API key."""

from __future__ import annotations

from typing import Any, Callable

from langchain_core.messages import AIMessage, BaseMessage


class ScriptedLLM:
    def __init__(self, structured: dict[type, list] | None = None, text: list[str] | None = None):
        self.structured = {schema: list(items) for schema, items in (structured or {}).items()}
        self.text = list(text or [])
        self.calls: list[tuple[str, list[BaseMessage]]] = []

    def with_structured_output(self, schema: type, **_: Any) -> "_Bound":
        return _Bound(self, schema)

    def invoke(self, messages: list[BaseMessage], *_: Any, **__: Any) -> AIMessage:
        self.calls.append(("text", messages))
        return AIMessage(self.text.pop(0) if self.text else "This query retrieves the requested rows.")

    def prompts_for(self, schema: type) -> list[str]:
        return ["\n".join(str(m.content) for m in msgs) for kind, msgs in self.calls if kind == schema.__name__]


class _Bound:
    def __init__(self, llm: ScriptedLLM, schema: type):
        self.llm, self.schema = llm, schema

    def invoke(self, messages: list[BaseMessage], *_: Any, **__: Any) -> Any:
        self.llm.calls.append((self.schema.__name__, messages))
        queue = self.llm.structured.get(self.schema)
        if not queue:
            raise AssertionError(f"No scripted response left for {self.schema.__name__}")
        item = queue.pop(0)
        return item(messages) if isinstance(item, Callable) and not isinstance(item, type) else item
