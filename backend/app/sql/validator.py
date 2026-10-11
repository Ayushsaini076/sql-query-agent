"""Deterministic SQL validation – the guardrail every query passes before it is returned.

Checks, in order:
  1. syntax          – sqlglot parses the query as SQLite, single statement only
  2. read_only       – AST contains no DML/DDL/admin nodes + keyword backstop
  3. tables          – every referenced table exists in the catalog
  4. columns         – every column resolves against the catalog (sqlglot qualify)
  5. joins           – join conditions follow declared foreign keys and compatible types
  6. compiles        – SQLite itself prepares the statement (EXPLAIN QUERY PLAN)

Errors make a query invalid; warnings are surfaced to the user but do not block.
"""

from __future__ import annotations

import difflib
import re
import sqlite3
from dataclasses import asdict, dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import OptimizeError, ParseError
from sqlglot.optimizer.qualify import qualify

from app.db.catalog import SchemaCatalog
from app.db.executor import ReadOnlyDatabase

DIALECT = "sqlite"

FORBIDDEN_NODES = tuple(
    getattr(exp, name)
    for name in (
        "Insert", "Update", "Delete", "Drop", "Alter", "AlterTable", "Create", "TruncateTable",
        "Merge", "Command", "Pragma", "Attach", "Detach", "Grant", "Revoke", "Transaction",
        "Commit", "Rollback", "Set", "Use", "LoadData", "Copy", "Analyze",
    )
    if hasattr(exp, name)
)
FORBIDDEN_KEYWORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE|ATTACH|DETACH|PRAGMA|VACUUM|REINDEX|"
    r"GRANT|REVOKE|MERGE|UPSERT|COMMIT|ROLLBACK|SAVEPOINT|RELEASE)\b",
    re.IGNORECASE,
)
DESTRUCTIVE_OPS = ("DELETE", "UPDATE", "INSERT", "DROP", "ALTER", "TRUNCATE")
ALLOWED_TABLE_FUNCTIONS = {"json_each", "json_tree"}
FORBIDDEN_FUNCTIONS = {"load_extension", "writefile", "readfile", "edit", "fts3_tokenizer", "sqlite_offset"}
_NUMERIC = {"INTEGER", "INT", "REAL", "NUMERIC", "FLOAT", "DOUBLE", "DECIMAL", "BIGINT"}


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class ValidationReport:
    sql: str
    valid: bool = True
    checks: list[Check] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)
    plan: list[dict] = field(default_factory=list)
    destructive: bool = False

    def add(self, name: str, passed: bool, detail: str = "", *, warning: bool = False) -> None:
        self.checks.append(Check(name, passed or warning, detail))
        if not passed:
            if warning:
                self.warnings.append(detail)
            else:
                self.errors.append(detail)
                self.valid = False

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("plan")
        return d


def strip_literals_and_comments(sql: str) -> str:
    """Remove comments, string literals and quoted identifiers (e.g. AS "Last Update")."""
    sql = re.sub(r"--[^\n]*", " ", sql)
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    sql = re.sub(r"'(?:[^']|'')*'", "''", sql)
    return re.sub(r'"(?:[^"]|"")*"|`[^`]*`|\[[^\]]*\]', "x", sql)


def find_forbidden_keyword(sql: str) -> str | None:
    m = FORBIDDEN_KEYWORDS.search(strip_literals_and_comments(sql))
    return m.group(1).upper() if m else None


def clean_sql(sql: str) -> str:
    """Strip markdown fences and trailing semicolons/whitespace."""
    sql = sql.strip()
    fence = re.match(r"^```(?:sql)?\s*(.*?)\s*```$", sql, re.DOTALL | re.IGNORECASE)
    if fence:
        sql = fence.group(1)
    return sql.strip().rstrip(";").strip()


class SQLValidator:
    def __init__(self, catalog: SchemaCatalog, db: ReadOnlyDatabase):
        self.catalog = catalog
        self.db = db
        self._schema = catalog.sqlglot_schema()

    def validate(self, sql: str) -> ValidationReport:
        sql = clean_sql(sql)
        report = ValidationReport(sql=sql)
        if not sql:
            report.add("syntax", False, "The query is empty.")
            return report

        # 1. syntax ------------------------------------------------------------
        try:
            statements = [s for s in sqlglot.parse(sql, read=DIALECT) if s is not None]
        except ParseError:
            return self._unparseable(sql, report)
        if len(statements) != 1:
            report.add("syntax", False, f"Exactly one statement is allowed, found {len(statements)}.")
            for stmt in statements:
                if not self._check_read_only(stmt, report, record=False):
                    break
            return report
        tree = statements[0]
        report.add("syntax", True, "Parsed as a single SQLite statement.")

        # 2. read-only ---------------------------------------------------------
        if not self._check_read_only(tree, report):
            return report

        if not self._check_functions(tree, report):
            return report

        # 3. tables ------------------------------------------------------------
        cte_names = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
        referenced = []
        for table in tree.find_all(exp.Table):
            name = table.name
            if not name or name.lower() in cte_names:
                continue
            referenced.append(name)
        unknown = sorted({t for t in referenced if not self.catalog.table(t)}, key=str.lower)
        known = sorted({self.catalog.table(t).name for t in referenced if self.catalog.table(t)})
        report.tables = known
        if unknown:
            report.add("tables", False, "; ".join(self._unknown_table_msg(t) for t in unknown))
            return report
        report.add("tables", True, f"Tables exist: {', '.join(known) or '(none)'}.")

        # 4. columns -----------------------------------------------------------
        qualified = self._check_columns(tree, sql, known, report)

        # 5. joins -------------------------------------------------------------
        if qualified is not None:
            self._check_joins(qualified, report)

        # 6. SQLite compile (authoritative) -----------------------------------
        if not report.valid:
            return report  # already explained precisely; avoid a duplicate engine error
        try:
            report.plan = self.db.explain_plan(sql)
            report.add("compiles", True, "SQLite accepted the query plan.")
        except sqlite3.Error as exc:
            report.add("compiles", False, f"SQLite error: {exc}")
        return report

    # ------------------------------------------------------------------ checks
    def _check_read_only(self, tree: exp.Expression, report: ValidationReport, *, record: bool = True) -> bool:
        offending = next((n for n in tree.walk() if isinstance(n, FORBIDDEN_NODES)), None)
        if offending is None and not isinstance(tree, exp.Query):
            offending = tree
        if offending is not None:
            op = _operation_name(offending)
            report.destructive = op in DESTRUCTIVE_OPS or report.destructive
            report.add("read_only", False, f"Only read-only SELECT queries are allowed; found {op}.")
            return False
        keyword = find_forbidden_keyword(tree.sql(DIALECT))
        if keyword:
            report.destructive = keyword in DESTRUCTIVE_OPS
            report.add("read_only", False, f"Only read-only SELECT queries are allowed; found {keyword}.")
            return False
        if record:
            report.add("read_only", True, "Statement is a read-only SELECT.")
        return True

    def _check_functions(self, tree: exp.Expression, report: ValidationReport) -> bool:
        """Only schema tables (plus JSON table functions) may be read; no system/extension functions."""
        for func in tree.find_all(exp.Func):
            name = (func.name if isinstance(func, exp.Anonymous) else func.sql_name()).lower()
            in_from = isinstance(func.parent, exp.Table)
            if name in FORBIDDEN_FUNCTIONS or name.startswith(("pragma_", "sqlite_")) or (
                in_from and name not in ALLOWED_TABLE_FUNCTIONS
            ):
                report.add("read_only", False, f"Function {name}() is not allowed; query only the schema tables.")
                return False
        return True

    def _unparseable(self, sql: str, report: ValidationReport) -> ValidationReport:
        """sqlglot could not parse the query: rely on the keyword backstop and SQLite itself."""
        keyword = find_forbidden_keyword(sql)
        if keyword:
            report.destructive = keyword in DESTRUCTIVE_OPS
            report.add("read_only", False, f"Only read-only SELECT queries are allowed; found {keyword}.")
            return report
        try:
            report.plan = self.db.explain_plan(sql)
        except sqlite3.Error as engine_exc:
            report.add("syntax", False, f"Syntax error: {engine_exc}.")
            return report
        if not re.match(r"^\s*(SELECT|WITH|VALUES)\b", strip_literals_and_comments(sql), re.IGNORECASE):
            report.add("read_only", False, "Only read-only SELECT queries are allowed.")
            return report
        report.add("syntax", False, "Static analysis unavailable for this syntax; SQLite accepted the query.", warning=True)
        report.add("compiles", True, "SQLite accepted the query plan.")
        return report

    def _check_columns(self, tree, sql, tables, report) -> exp.Expression | None:
        try:
            qualified = qualify(
                tree.copy(), schema=self._schema, dialect=DIALECT,
                validate_qualify_columns=True, quote_identifiers=False,
            )
        except OptimizeError as exc:
            message = str(exc)
            column = _extract_column(message)
            if self._sqlite_accepts(sql) and not (column and f'"{column.lower()}"' in sql.lower()):
                # SQLite accepts it (e.g. alias referenced in WHERE) – trust the engine.
                report.add("columns", False, f"Static column check was inconclusive: {message}", warning=True)
                return None
            if column:  # recover the user's original spelling for the message
                original = re.search(rf"\b{re.escape(column)}\b", sql, re.IGNORECASE)
                column = original.group(0) if original else column
            report.add("columns", False, self._unknown_column_msg(column, tables, message))
            return None
        except Exception as exc:  # sqlglot limitation – SQLite compile still runs
            report.add("columns", False, f"Static column analysis skipped: {exc}", warning=True)
            return None
        report.add("columns", True, "All columns resolve against the schema.")
        return qualified

    def _check_joins(self, qualified: exp.Expression, report: ValidationReport) -> None:
        problems, notes = [], []
        for select in qualified.find_all(exp.Select):
            aliases = {
                t.alias_or_name.lower(): self.catalog.table(t.name).name
                for t in select.find_all(exp.Table)
                if self.catalog.table(t.name)
            }
            for join in select.args.get("joins") or []:
                on = join.args.get("on")
                if on is None:
                    if not join.args.get("using") and not _linked_in_where(select, join):
                        joined = self.catalog.table(join.this.name)
                        notes.append(f"Join with {joined.name if joined else join.this.name} has no join condition "
                                     "and produces a Cartesian product.")
                    continue
                for eq in on.find_all(exp.EQ):
                    left, right = eq.this, eq.expression
                    if not (isinstance(left, exp.Column) and isinstance(right, exp.Column)):
                        continue
                    lt, rt = aliases.get(left.table.lower()), aliases.get(right.table.lower())
                    if not lt or not rt:
                        continue  # derived table / CTE – cannot verify statically
                    a, b = (lt, left.name), (rt, right.name)
                    if self.catalog.is_foreign_key_pair(a, b):
                        continue
                    ca, cb = self.catalog.table(lt).column(left.name), self.catalog.table(rt).column(right.name)
                    label = f"{lt}.{ca.name if ca else left.name} = {rt}.{cb.name if cb else right.name}"
                    if ca and cb and _type_family(ca.type) != _type_family(cb.type):
                        problems.append(f"Join {label} compares incompatible types ({ca.type} vs {cb.type}).")
                    elif lt != rt or left.name.lower() != right.name.lower():
                        notes.append(f"Join {label} is not a declared foreign-key relationship.")
        if problems:
            report.add("joins", False, " ".join(problems))
        elif notes:
            report.add("joins", False, " ".join(dict.fromkeys(notes)), warning=True)
        else:
            report.add("joins", True, "Joins follow declared foreign-key relationships.")

    # ----------------------------------------------------------------- helpers
    def _sqlite_accepts(self, sql: str) -> bool:
        try:
            self.db.explain_plan(sql)
            return True
        except sqlite3.Error:
            return False

    def _unknown_table_msg(self, name: str) -> str:
        close = difflib.get_close_matches(name.lower(), [t.lower() for t in self.catalog.table_names], n=1)
        hint = f" Did you mean {self.catalog.table(close[0]).name}?" if close else ""
        return f"Table '{name}' does not exist.{hint} Available tables: {', '.join(self.catalog.table_names)}."

    def _unknown_column_msg(self, column: str | None, tables: list[str], raw: str) -> str:
        if not column:
            return f"Column check failed: {raw}"
        candidates = {c.name: t for t in tables for c in self.catalog.table(t).columns}
        close = difflib.get_close_matches(column.lower(), [c.lower() for c in candidates], n=2, cutoff=0.6)
        names = {c.lower(): c for c in candidates}
        hint = (" Did you mean " + " or ".join(f"{candidates[names[c]]}.{names[c]}" for c in close) + "?") if close else ""
        listing = "; ".join(f"{t}({', '.join(c.name for c in self.catalog.table(t).columns)})" for t in tables)
        return f"Column '{column}' does not exist in the referenced tables.{hint} Columns available: {listing}."


def _operation_name(node: exp.Expression) -> str:
    if isinstance(node, exp.Command):
        return str(node.this).upper()
    name = type(node).__name__.upper()
    return {"TRUNCATETABLE": "TRUNCATE", "ALTERTABLE": "ALTER"}.get(name, name)


def _extract_column(message: str) -> str | None:
    m = re.search(r"Column '([^']+)'", message) or re.search(r"Unknown column: (\w+)", message)
    return m.group(1) if m else None


def _linked_in_where(select: exp.Select, join: exp.Join) -> bool:
    alias = join.this.alias_or_name.lower()
    where = select.args.get("where")
    if not where:
        return False
    for eq in where.find_all(exp.EQ):
        sides = [s for s in (eq.this, eq.expression) if isinstance(s, exp.Column)]
        if len(sides) == 2 and alias in {s.table.lower() for s in sides} and sides[0].table != sides[1].table:
            return True
    return False


def _type_family(t: str) -> str:
    t = t.upper()
    if t in _NUMERIC:
        return "number"
    if t in {"DATE", "DATETIME", "TIMESTAMP"}:
        return "date"
    return "text"
