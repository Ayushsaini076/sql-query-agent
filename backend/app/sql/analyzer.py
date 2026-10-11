"""Deterministic query analysis: formatting, cost estimation, index advice and dialect translation.

Everything here is derived from the SQLite query planner (EXPLAIN QUERY PLAN) and
table statistics – no LLM involved – so the numbers are reproducible.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.qualify import qualify

from app.db.catalog import SchemaCatalog
from app.db.executor import ReadOnlyDatabase

DIALECTS = ("sqlite", "postgres", "mysql")
DIALECT_LABELS = {"sqlite": "SQLite", "postgres": "PostgreSQL", "mysql": "MySQL"}
INDEX_ADVICE_MIN_ROWS = 100
COMPACT_MAX_CHARS = 72  # shorter queries stay on one line

# SQLite functions sqlglot passes through untranslated; flag them for other dialects.
SQLITE_ONLY = {
    "JULIANDAY": "JULIANDAY() is SQLite-specific; use date subtraction / DATEDIFF instead.",
    "UNIXEPOCH": "UNIXEPOCH() is SQLite-specific; use EXTRACT(EPOCH ...) / UNIX_TIMESTAMP().",
    "PRINTF": "PRINTF() is SQLite-specific; use FORMAT()/CONCAT().",
    "TOTAL": "TOTAL() is SQLite-specific; use COALESCE(SUM(...), 0).",
}
_DATE_MODIFIER = re.compile(r"\b(DATE|DATETIME)\s*\(\s*'now'", re.IGNORECASE)
_PLAN_ROW = re.compile(r"^(SCAN|SEARCH)\s+(\S+)(?:\s+AS\s+\S+)?(?:\s+USING\s+(.*))?$")


@dataclass
class IndexSuggestion:
    table: str
    columns: list[str]
    statement: str
    reason: str


@dataclass
class CostEstimate:
    level: str  # low | medium | high
    estimated_rows_examined: int
    full_scans: list[str] = field(default_factory=list)
    index_lookups: list[str] = field(default_factory=list)
    temp_structures: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class Analysis:
    formatted_sql: str
    dialects: dict[str, str]
    dialect_notes: dict[str, list[str]]
    plan: list[str]
    cost: CostEstimate
    index_suggestions: list[IndexSuggestion]

    def to_dict(self) -> dict:
        return asdict(self)


class QueryAnalyzer:
    def __init__(self, catalog: SchemaCatalog, db: ReadOnlyDatabase):
        self.catalog = catalog
        self.db = db
        self._schema = catalog.sqlglot_schema()
        self._rows_per_key = self._load_index_stats()

    def _load_index_stats(self) -> dict[str, int]:
        """Average rows per key for each index, from the statistics ANALYZE stored in sqlite_stat1."""
        try:
            rows = self.db.execute("SELECT idx, stat FROM sqlite_stat1 WHERE idx IS NOT NULL", max_rows=10_000).rows
        except Exception:
            return {}
        stats = {}
        for idx, stat in rows:
            parts = str(stat).split()
            if len(parts) > 1 and parts[1].isdigit():
                stats[idx.lower()] = int(parts[1])
        return stats

    def analyze(self, sql: str, plan: list[dict] | None = None) -> Analysis:
        formatted = self.format(sql)
        plan = plan if plan is not None else self.db.explain_plan(formatted)
        tree = sqlglot.parse_one(formatted, read="sqlite")
        aliases = _alias_map(tree, self.catalog)
        cost = self.estimate_cost(plan, aliases)
        dialects, notes = self.translate(formatted)
        return Analysis(
            formatted_sql=formatted,
            dialects=dialects,
            dialect_notes=notes,
            plan=[row["detail"] for row in plan],
            cost=cost,
            index_suggestions=self.suggest_indexes(tree, plan, aliases),
        )

    # -------------------------------------------------------------- formatting
    def format(self, sql: str) -> str:
        try:
            formatted = sqlglot.transpile(sql, read="sqlite", write="sqlite")[0]
            if len(formatted) > COMPACT_MAX_CHARS:
                formatted = sqlglot.transpile(sql, read="sqlite", write="sqlite", pretty=True, max_text_width=100)[0]
            self.db.explain_plan(formatted)  # never return a formatting that breaks the query
            return formatted
        except Exception:
            return sql

    def translate(self, sql: str) -> tuple[dict[str, str], dict[str, list[str]]]:
        out, notes = {"sqlite": sql}, {"sqlite": []}
        for dialect in DIALECTS[1:]:
            try:
                pretty = len(sql) > COMPACT_MAX_CHARS or "\n" in sql
                translated = sqlglot.transpile(sql, read="sqlite", write=dialect, pretty=pretty, max_text_width=100)[0]
            except Exception as exc:  # pragma: no cover - defensive
                out[dialect], notes[dialect] = sql, [f"Automatic translation failed: {exc}"]
                continue
            dialect_notes = [msg for fn, msg in SQLITE_ONLY.items() if re.search(rf"\b{fn}\s*\(", translated, re.I)]
            if _DATE_MODIFIER.search(translated):
                dialect_notes.append("DATE('now', modifier) is SQLite syntax; use CURRENT_DATE - INTERVAL ... instead.")
            out[dialect], notes[dialect] = translated, dialect_notes
        return out, notes

    # -------------------------------------------------------------------- cost
    def estimate_cost(self, plan: list[dict], aliases: dict[str, str]) -> CostEstimate:
        """Nested-loop cost model over the SQLite plan (a heuristic, not a measurement)."""
        examined, loop_rows = 0, 1
        full_scans, lookups, temps, notes = [], [], [], []
        for row in plan:
            detail = row["detail"]
            if "TEMP B-TREE" in detail:
                temps.append(detail.replace("USE ", "").lower())
                continue
            m = _PLAN_ROW.match(detail)
            if not m:
                continue
            op, ref, using = m.group(1), m.group(2), m.group(3) or ""
            table = self.catalog.table(aliases.get(ref.lower(), ref))
            if table is None:  # CTE / subquery materialisation
                continue
            n = max(table.row_count, 1)
            if op == "SCAN":
                covering = "COVERING INDEX" in using
                examined += loop_rows * n
                if loop_rows > 1:
                    notes.append(f"{table.name} is scanned once per row of an outer table (nested full scan).")
                full_scans.append(f"{table.name} (~{n:,} rows{', covering index' if covering else ''})")
                loop_rows = loop_rows * n if loop_rows == 1 else loop_rows * max(1, n // 10)
            else:
                per_probe = self._rows_per_probe(using, n)
                examined += loop_rows * per_probe
                lookups.append(f"{table.name} via {using or 'index'}")
                loop_rows *= per_probe
        if temps:
            notes.append("Results are sorted/grouped with a temporary B-tree (an extra pass over the rows).")
        level = "low"
        if examined > 100_000 or any("nested full scan" in n for n in notes):
            level = "high"
        elif examined > 2_000 or temps:
            level = "medium"
        return CostEstimate(level, int(examined), full_scans, lookups, temps, notes)

    def _rows_per_probe(self, using: str, table_rows: int) -> int:
        if "PRIMARY KEY" in using:
            return 1
        idx = re.search(r"INDEX\s+(\w+)", using)
        if idx and idx.group(1).lower() in self._rows_per_key:
            return self._rows_per_key[idx.group(1).lower()]
        return max(1, table_rows // 10)  # unknown/automatic index: assume ~10% selectivity

    # ----------------------------------------------------------------- indexes
    def suggest_indexes(self, tree: exp.Expression, plan: list[dict], aliases: dict[str, str]) -> list[IndexSuggestion]:
        scanned, suggestions = set(), []
        for row in plan:
            m = _PLAN_ROW.match(row["detail"])
            if m and "AUTOMATIC" in (m.group(3) or ""):
                table = self.catalog.table(aliases.get(m.group(2).lower(), m.group(2)))
                cols = re.findall(r"(\w+)[=<>]", m.group(3))
                if table and cols:
                    cols = [table.column(c).name for c in cols if table.column(c)]
                    name = f"idx_{table.name.lower()}_{'_'.join(c.lower() for c in cols)}"
                    suggestions.append(IndexSuggestion(
                        table=table.name, columns=cols,
                        statement=f"CREATE INDEX {name} ON {table.name}({', '.join(cols)});",
                        reason=(f"SQLite had to build a temporary automatic index on {table.name}({', '.join(cols)}) "
                                "for this query; a permanent index avoids rebuilding it on every execution."),
                    ))
            if m and m.group(1) == "SCAN" and "COVERING INDEX" not in (m.group(3) or ""):
                table = self.catalog.table(aliases.get(m.group(2).lower(), m.group(2)))
                if table and table.row_count >= INDEX_ADVICE_MIN_ROWS:
                    scanned.add(table.name)
        if not scanned:
            return suggestions
        try:
            qualified = qualify(tree.copy(), schema=self._schema, dialect="sqlite", quote_identifiers=False)
        except Exception:
            return suggestions
        q_aliases = _alias_map(qualified, self.catalog)

        filters: dict[str, list[tuple[str, bool]]] = {}
        for node in qualified.find_all(exp.EQ, exp.In, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.Between, exp.Like):
            column = node.this if isinstance(node.this, exp.Column) else None
            if column is None and isinstance(node, exp.EQ) and isinstance(node.expression, exp.Column):
                column = node.expression
            if column is None or not column.table:
                continue
            table = self.catalog.table(q_aliases.get(column.table.lower(), ""))
            if table is None or table.name not in scanned:
                continue
            if isinstance(node, exp.Like) and str(node.expression.this if isinstance(node.expression, exp.Literal) else "").startswith("%"):
                continue  # leading wildcard cannot use an index
            col = table.column(column.name)
            if col and col.name.lower() not in table.indexed_leading_columns():
                entries = filters.setdefault(table.name, [])
                if col.name not in [c for c, _ in entries]:
                    entries.append((col.name, isinstance(node, (exp.EQ, exp.In))))

        for table_name, entries in filters.items():
            # Equality columns first, then range columns: the order a composite B-tree can use.
            cols = [c for c, eq in sorted(entries, key=lambda e: not e[1])][:3]
            name = f"idx_{table_name.lower()}_{'_'.join(c.lower() for c in cols)}"
            suggestions.append(IndexSuggestion(
                table=table_name, columns=cols,
                statement=f"CREATE INDEX {name} ON {table_name}({', '.join(cols)});",
                reason=(f"{table_name} is fully scanned while filtering/joining on {', '.join(cols)}; "
                        f"an index lets SQLite seek directly to matching rows (benefit grows with table size)."),
            ))
        return suggestions


def _alias_map(tree: exp.Expression, catalog: SchemaCatalog) -> dict[str, str]:
    out = {}
    for t in tree.find_all(exp.Table):
        table = catalog.table(t.name)
        if table:
            out[t.alias_or_name.lower()] = table.name
            out[t.name.lower()] = table.name
    return out
