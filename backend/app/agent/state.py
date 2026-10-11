"""LangGraph state for the SQL agent.

Conversation-level fields (messages, last_sql) persist across turns through the
checkpointer; every other field is per-turn and reset by `new_turn()`.
"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict, total=False):
    # --- conversation (persisted) -------------------------------------------
    messages: Annotated[list[AnyMessage], add_messages]
    last_sql: str | None  # most recent validated SQL, used for follow-ups

    # --- turn input -----------------------------------------------------------
    question: str
    execute: bool

    # --- routing ----------------------------------------------------------------
    intent: str | None
    refusal_reason: str | None
    clarification: str | None
    router_rationale: str | None
    refers_to_previous: bool
    source_sql: str | None  # SQL supplied by the user (or previous SQL) to optimize/debug/explain
    source_validation: dict | None
    routed_to_debug: bool  # optimize/explain request rerouted because the SQL was broken

    # --- SQL pipeline -----------------------------------------------------------
    relevant_tables: list[str]
    candidate_sql: str | None
    assumptions: list[str]
    debug: dict | None
    optimization: dict | None
    validation: dict | None
    plan: list[dict]
    attempts: int
    final_sql: str | None
    analysis: dict | None
    execution: dict | None
    execution_error: str | None
    equivalence: dict | None
    explanation: str | None

    # --- output -------------------------------------------------------------------
    response: dict[str, Any] | None


TURN_DEFAULTS: dict[str, Any] = {
    "intent": None,
    "refusal_reason": None,
    "clarification": None,
    "router_rationale": None,
    "refers_to_previous": False,
    "source_sql": None,
    "source_validation": None,
    "routed_to_debug": False,
    "relevant_tables": [],
    "candidate_sql": None,
    "assumptions": [],
    "debug": None,
    "optimization": None,
    "validation": None,
    "plan": [],
    "attempts": 0,
    "final_sql": None,
    "analysis": None,
    "execution": None,
    "execution_error": None,
    "equivalence": None,
    "explanation": None,
    "response": None,
}


def new_turn(question: str, *, execute: bool = True) -> dict[str, Any]:
    """Graph input for a new user turn: resets every per-turn field."""
    return {**TURN_DEFAULTS, "question": question, "execute": execute}
