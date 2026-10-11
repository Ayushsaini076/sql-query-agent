"""Regenerate docs/PROMPTS.md, docs/SCHEMA.md and docs/workflow.mmd from the code.

Usage (from backend/):  python -m scripts.generate_docs
"""

from __future__ import annotations

import inspect
from pathlib import Path

from app.agent import prompts
from app.agent.graph import build_graph
from app.agent.models import DebugResult, IntentDecision, OptimizationResult, SQLDraft
from app.agent.nodes import SQLAgentNodes
from app.config import get_settings
from app.db.catalog import SchemaCatalog
from app.db.executor import ReadOnlyDatabase
from app.db.seed import SCHEMA_PATH, build_database

DOCS = Path(__file__).resolve().parents[2] / "docs"

PROMPT_ORDER = [
    ("Router: intent detection + scope validation", "ROUTER_SYSTEM", "ROUTER_USER", IntentDecision),
    ("SQL generation", "GENERATE_SYSTEM", "GENERATE_USER", SQLDraft),
    ("Self-correction feedback (appended on retry)", None, "RETRY_FEEDBACK", None),
    ("SQL debugging", "DEBUG_SYSTEM", "DEBUG_USER", DebugResult),
    ("SQL optimization", "OPTIMIZE_SYSTEM", "OPTIMIZE_USER", OptimizationResult),
    ("Explanation", "EXPLAIN_SYSTEM", "EXPLAIN_USER", None),
    ("Database / SQL concept questions", "DB_QUESTION_SYSTEM", "DB_QUESTION_USER", None),
]


def prompts_md() -> str:
    out = [
        "# Prompts",
        "",
        "> Generated from `backend/app/agent/prompts.py` by `python -m scripts.generate_docs`. Do not edit by hand.",
        "",
        "Placeholders in `{braces}` are filled at runtime: `{schema}` with the retrieved tables from the live",
        "catalog, `{history}` with the last turns of the conversation, `{previous_sql}` with the last validated",
        "query. Untrusted input is always wrapped in XML-style tags that the system prompts declare to be data.",
        "",
        "## Fixed responses",
        "",
        f"- **Out of scope:** {prompts.SCOPE_STATEMENT}",
        f"- **Destructive request:** {prompts.DESTRUCTIVE_REFUSAL}",
        f"- **Prompt injection:** {prompts.INJECTION_REFUSAL}",
        "",
    ]
    for title, system, user, schema in PROMPT_ORDER:
        out += [f"## {title}", ""]
        if system:
            out += ["**System prompt**", "", "```text", getattr(prompts, system), "```", ""]
        out += ["**User message template**" if system else "**Template**", "", "```text", getattr(prompts, user), "```", ""]
        if schema:
            out += [f"**Structured output:** `{schema.__name__}`", "", "| Field | Description |", "|---|---|"]
            for name, field in schema.model_fields.items():
                out.append(f"| `{name}` | {field.description or ''} |")
            out.append("")
    return "\n".join(out)


def schema_md(catalog: SchemaCatalog) -> str:
    er = ["erDiagram"]
    for t in catalog.tables.values():
        er.append(f"    {t.name} {{")
        for c in t.columns:
            key = "PK" if c.primary_key else ("FK" if c.references else "")
            er.append(f"        {c.type} {c.name} {key}".rstrip())
        er.append("    }")
    for fk in catalog.foreign_keys:
        er.append(f'    {fk.ref_table} ||--o{{ {fk.table} : "{fk.column}"')
    out = [
        "# Sample database schema",
        "",
        "> Generated from the live SQLite database by `python -m scripts.generate_docs`.",
        "> Source DDL: [`backend/app/db/schema.sql`](../backend/app/db/schema.sql); data: `backend/app/db/seed.py`",
        "> (deterministic, seed 42).",
        "",
        "```mermaid", *er, "```", "",
    ]
    for t in catalog.tables.values():
        out += [f"## {t.name}", "", f"{t.description} — **{t.row_count:,} rows**", "",
                "| Column | Type | Key | Notes |", "|---|---|---|---|"]
        for c in t.columns:
            key = "PK" if c.primary_key else (f"FK → {c.references[0]}.{c.references[1]}" if c.references else "")
            notes = c.description
            if c.values:
                notes += ("; " if notes else "") + "values: " + ", ".join(f"`{v}`" for v in c.values)
            elif c.value_range:
                notes += ("; " if notes else "") + f"range {c.value_range[0]} … {c.value_range[1]}"
            out.append(f"| `{c.name}` | {c.type} | {key} | {notes} |")
        if t.indexes:
            out += ["", "Indexes: " + ", ".join(f"`{n}({', '.join(cols)})`" for n, cols in t.indexes.items())]
        out.append("")
    out += ["## DDL", "", "```sql", SCHEMA_PATH.read_text().strip(), "```", ""]
    return "\n".join(out)


def main() -> None:
    settings = get_settings()
    build_database(settings.database_path)
    catalog = SchemaCatalog.from_sqlite(settings.database_path)
    db = ReadOnlyDatabase(settings.database_path)

    class _NoLLM:  # graph structure only
        def with_structured_output(self, *a, **k): ...

    graph = build_graph(SQLAgentNodes(llm=_NoLLM(), provider="none", catalog=catalog, db=db))
    DOCS.mkdir(exist_ok=True)
    (DOCS / "PROMPTS.md").write_text(prompts_md())
    (DOCS / "SCHEMA.md").write_text(schema_md(catalog))
    (DOCS / "workflow.mmd").write_text(graph.get_graph().draw_mermaid())
    print("Wrote", ", ".join(p.name for p in (DOCS / "PROMPTS.md", DOCS / "SCHEMA.md", DOCS / "workflow.mmd")))


if __name__ == "__main__":
    main()
