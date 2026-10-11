"""LangGraph workflow for the SQL agent.

    guard_input ──blocked──────────────────────────────────────────────► refuse
        │
    classify_intent ──out_of_scope / destructive──────────────────────► refuse
        │            ──clarify────────────────────────────────────────► clarify
        │            ──database_question──────────────────────────────► answer_question
        │            ──optimize / debug / explain──► check_user_sql ──destructive──► refuse
        │                                                 │
        ▼                                                 ▼
    retrieve_schema ◄─────────────────────────────────────┘
        │
        ├─ generate_sql ──unanswerable──► clarify
        ├─ debug_sql
        ├─ optimize_sql
        └─ load_user_sql (explain)
                │
           validate_sql ──invalid, attempts left──► back to the producer (self-correction)
                │       ──invalid, no attempts left──► fail
                ▼
           analyze_sql (format, cost, indexes, dialects)
                │
           execute_sql (read-only, optional)
                │
           generate_explanation
                │
           respond ──► END
"""

from __future__ import annotations

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from app.agent.nodes import SQLAgentNodes
from app.agent.state import AgentState

# Human-readable progress labels, streamed to the UI as each node finishes.
NODE_LABELS = {
    "guard_input": "Checked input guardrails",
    "classify_intent": "Detected intent and scope",
    "check_user_sql": "Inspected the supplied SQL",
    "retrieve_schema": "Retrieved relevant schema",
    "generate_sql": "Generated SQL",
    "debug_sql": "Debugged SQL",
    "optimize_sql": "Optimized SQL",
    "load_user_sql": "Loaded your SQL",
    "validate_sql": "Validated SQL",
    "analyze_sql": "Analyzed query plan and cost",
    "execute_sql": "Executed query (read-only)",
    "generate_explanation": "Wrote explanation",
    "respond": "Prepared response",
    "refuse": "Declined request",
    "clarify": "Asked for clarification",
    "answer_question": "Answered question",
    "fail": "Validation failed",
}


def _after_guard(state: AgentState) -> str:
    return "refuse" if state.get("refusal_reason") else "classify_intent"


def _after_classify(state: AgentState) -> str:
    intent = state["intent"]
    if intent in ("out_of_scope", "destructive"):
        return "refuse"
    if intent == "clarify":
        return "clarify"
    if intent == "database_question":
        return "answer_question"
    if state.get("source_sql"):
        return "check_user_sql"
    return "retrieve_schema"


def _after_check(state: AgentState) -> str:
    return "refuse" if state["intent"] == "destructive" else "retrieve_schema"


def _after_retrieve(state: AgentState) -> str:
    return {
        "generate_sql": "generate_sql",
        "debug_sql": "debug_sql",
        "optimize_sql": "optimize_sql",
        "explain_sql": "load_user_sql",
    }[state["intent"]]


def _after_generate(state: AgentState) -> str:
    return "validate_sql" if state.get("candidate_sql") else "clarify"


def build_graph(nodes: SQLAgentNodes, checkpointer: BaseCheckpointSaver | None = None):
    g = StateGraph(AgentState)
    for name in NODE_LABELS:
        g.add_node(name, getattr(nodes, name))

    g.add_edge(START, "guard_input")
    g.add_conditional_edges("guard_input", _after_guard, ["refuse", "classify_intent"])
    g.add_conditional_edges(
        "classify_intent", _after_classify,
        ["refuse", "clarify", "answer_question", "check_user_sql", "retrieve_schema"],
    )
    g.add_conditional_edges("check_user_sql", _after_check, ["refuse", "retrieve_schema"])
    g.add_conditional_edges(
        "retrieve_schema", _after_retrieve, ["generate_sql", "debug_sql", "optimize_sql", "load_user_sql"],
    )
    g.add_conditional_edges("generate_sql", _after_generate, ["validate_sql", "clarify"])
    for producer in ("debug_sql", "optimize_sql", "load_user_sql"):
        g.add_edge(producer, "validate_sql")
    g.add_conditional_edges(
        "validate_sql", nodes.route_after_validation,
        ["analyze_sql", "generate_sql", "debug_sql", "optimize_sql", "fail"],
    )
    g.add_edge("analyze_sql", "execute_sql")
    g.add_edge("execute_sql", "generate_explanation")
    g.add_edge("generate_explanation", "respond")
    for terminal in ("respond", "refuse", "clarify", "answer_question", "fail"):
        g.add_edge(terminal, END)
    return g.compile(checkpointer=checkpointer)
