"""Structured outputs the LLM must return at each step.

All fields are required (nullable instead of defaulted) so the same schemas work with
strict JSON-schema structured output on every provider.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Intent = Literal[
    "generate_sql",    # natural-language data request (new or follow-up)
    "optimize_sql",    # improve a user-supplied / previous query
    "debug_sql",       # fix a broken user-supplied / previous query
    "explain_sql",     # explain what a user-supplied / previous query does
    "database_question",  # question about the schema or SQL concepts, no query needed
    "destructive",     # wants to modify data or schema
    "clarify",         # in scope but too ambiguous to act on
    "out_of_scope",    # unrelated to SQL / this database, or tries to change the assistant's rules
]


class IntentDecision(BaseModel):
    intent: Intent = Field(description="The single best intent for the latest user message.")
    user_sql: str | None = Field(
        description="SQL text the user pasted in the latest message, copied verbatim; null if none."
    )
    refers_to_previous_sql: bool = Field(
        description="True if the request acts on the assistant's previous query (e.g. 'optimize it', 'only those from CA')."
    )
    clarification_question: str | None = Field(
        description="For intent 'clarify': one short question that would resolve the ambiguity; otherwise null."
    )
    rationale: str = Field(description="One short sentence explaining the decision.")


class SQLDraft(BaseModel):
    sql: str | None = Field(description="A single read-only SQLite SELECT statement, or null if not answerable.")
    assumptions: list[str] = Field(description="Interpretations made to resolve vague wording (may be empty).")
    unanswerable_reason: str | None = Field(
        description="If the schema cannot answer the request: which data is missing. Otherwise null."
    )


class DebugIssue(BaseModel):
    problem: str = Field(description="What is wrong, quoting the offending fragment.")
    explanation: str = Field(description="Why it is wrong.")


class DebugResult(BaseModel):
    issues: list[DebugIssue] = Field(description="Every problem found, most important first.")
    corrected_sql: str = Field(description="The corrected, read-only SQLite query.")
    summary: str = Field(description="One or two sentences summarising the fix.")


class OptimizationResult(BaseModel):
    optimized_sql: str = Field(description="Semantically equivalent, improved read-only SQLite query.")
    readability_changes: list[str] = Field(description="Formatting / clarity improvements made.")
    performance_changes: list[str] = Field(description="Performance improvements made or suggested.")
    removed_joins: list[str] = Field(description="Joins removed because they did not affect the result.")
    index_recommendations: list[str] = Field(description="CREATE INDEX statements worth adding, with a reason each.")
