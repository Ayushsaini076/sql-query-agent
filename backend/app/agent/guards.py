"""Deterministic input guardrails that run before any LLM call.

They are intentionally conservative (high precision): anything they miss is still caught
by the LLM router, and any SQL that is produced is still checked by the SQL validator and
the read-only database connection.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

INJECTION_PATTERNS = [
    r"\b(ignore|disregard|forget|override|bypass)\b[^.\n]{0,40}\b(instructions?|prompts?|rules|guardrails|directives|system message)",
    r"\b(reveal|show|print|repeat|leak|output|display)\b[^.\n]{0,30}\b(system prompt|your (prompt|instructions|rules))",
    r"\byou are (now|no longer)\b",
    r"\b(pretend|act|behave)\b[^.\n]{0,20}\b(you are|to be|as if)\b(?![^.\n]{0,30}\b(sql|database|dba|analyst)\b)",
    r"\b(jailbreak|DAN mode|developer mode|god mode)\b",
    r"</?\s*(system|assistant|instructions?|user_request|conversation_history|schema)\s*>",
    r"\bnew (instructions|rules|persona)\s*:",
]
_INJECTION = re.compile("|".join(f"(?:{p})" for p in INJECTION_PATTERNS), re.IGNORECASE)

# Explicit write/DDL statements typed by the user (natural-language requests such as
# "remove inactive customers" are left to the LLM router).
_DESTRUCTIVE_SQL = re.compile(
    r"(?:^|[;:\n`])\s*(?:"
    r"insert\s+(?:or\s+\w+\s+)?into|update\s+\w+\s+set|delete\s+from|drop\s+(?:table|database|index|view|schema|trigger)"
    r"|alter\s+table|truncate\s+(?:table\s+)?\w+|create\s+(?:table|database|view|trigger|schema)|replace\s+into"
    r"|attach\s+database|detach\s+database|grant\s+\w+|revoke\s+\w+|merge\s+into"
    r")\b",
    re.IGNORECASE,
)


@dataclass
class GuardResult:
    blocked: bool
    reason: str | None = None  # "too_long" | "empty" | "prompt_injection" | "destructive"


def check_input(text: str, *, max_chars: int) -> GuardResult:
    stripped = text.strip()
    if not stripped:
        return GuardResult(True, "empty")
    if len(stripped) > max_chars:
        return GuardResult(True, "too_long")
    if _INJECTION.search(stripped):
        return GuardResult(True, "prompt_injection")
    if contains_destructive_sql(stripped):
        return GuardResult(True, "destructive")
    return GuardResult(False)


def contains_destructive_sql(text: str) -> bool:
    return bool(_DESTRUCTIVE_SQL.search(text))
