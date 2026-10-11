"""Schema catalog: the single source of truth about tables, columns and relationships.

Everything is introspected from the live SQLite database (PRAGMA calls plus the
comments stored in sqlite_master), so the agent can never drift from the real schema.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

LOW_CARDINALITY_LIMIT = 8  # list every value for TEXT columns with at most this many distinct values


@dataclass
class Column:
    name: str
    type: str
    nullable: bool
    primary_key: bool
    description: str = ""
    references: tuple[str, str] | None = None  # (table, column)
    values: list[str] = field(default_factory=list)  # all distinct values (low cardinality)
    examples: list[str] = field(default_factory=list)  # a few sample values
    value_range: tuple[str, str] | None = None  # min/max for dates


@dataclass
class ForeignKey:
    table: str
    column: str
    ref_table: str
    ref_column: str

    def matches(self, a: tuple[str, str], b: tuple[str, str]) -> bool:
        pair = {(self.table.lower(), self.column.lower()), (self.ref_table.lower(), self.ref_column.lower())}
        return {(a[0].lower(), a[1].lower()), (b[0].lower(), b[1].lower())} == pair


@dataclass
class Table:
    name: str
    description: str
    columns: list[Column]
    row_count: int
    indexes: dict[str, list[str]]  # index name -> columns

    def column(self, name: str) -> Column | None:
        return next((c for c in self.columns if c.name.lower() == name.lower()), None)

    def indexed_leading_columns(self) -> set[str]:
        lead = {cols[0].lower() for cols in self.indexes.values() if cols}
        lead |= {c.name.lower() for c in self.columns if c.primary_key}
        return lead


class SchemaCatalog:
    def __init__(self, tables: list[Table], foreign_keys: list[ForeignKey]):
        self.tables = {t.name.lower(): t for t in tables}
        self.foreign_keys = foreign_keys

    # ------------------------------------------------------------------ build
    @classmethod
    def from_sqlite(cls, path: Path | str) -> "SchemaCatalog":
        conn = sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True)
        try:
            return cls._introspect(conn)
        finally:
            conn.close()

    @classmethod
    def _introspect(cls, conn: sqlite3.Connection) -> "SchemaCatalog":
        tables: list[Table] = []
        fks: list[ForeignKey] = []
        rows = conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY rowid"
        ).fetchall()
        for name, ddl in rows:
            table_desc, col_desc = _parse_comments(ddl or "")
            refs = {}
            for fk in conn.execute(f'PRAGMA foreign_key_list("{name}")'):
                ref_col = fk[4] or "rowid"
                refs[fk[3]] = (fk[2], ref_col)
                fks.append(ForeignKey(name, fk[3], fk[2], ref_col))
            columns = []
            for cid, cname, ctype, notnull, _default, pk in conn.execute(f'PRAGMA table_info("{name}")'):
                col = Column(
                    name=cname, type=(ctype or "TEXT").upper(), nullable=not notnull and not pk,
                    primary_key=bool(pk), description=col_desc.get(cname.lower(), ""),
                    references=refs.get(cname),
                )
                _profile_column(conn, name, col)
                columns.append(col)
            indexes = {}
            for idx in conn.execute(f'PRAGMA index_list("{name}")'):
                idx_cols = [r[2] for r in conn.execute(f'PRAGMA index_info("{idx[1]}")')]
                indexes[idx[1]] = idx_cols
            count = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
            tables.append(Table(name, table_desc, columns, count, indexes))
        return cls(tables, fks)

    # ---------------------------------------------------------------- lookups
    def table(self, name: str) -> Table | None:
        return self.tables.get(name.lower())

    @property
    def table_names(self) -> list[str]:
        return [t.name for t in self.tables.values()]

    def is_foreign_key_pair(self, a: tuple[str, str], b: tuple[str, str]) -> bool:
        return any(fk.matches(a, b) for fk in self.foreign_keys)

    def neighbors(self, table: str) -> set[str]:
        t = table.lower()
        out = set()
        for fk in self.foreign_keys:
            if fk.table.lower() == t:
                out.add(fk.ref_table)
            elif fk.ref_table.lower() == t:
                out.add(fk.table)
        return {self.tables[n.lower()].name for n in out if n.lower() in self.tables} - {self.tables[t].name}

    def sqlglot_schema(self) -> dict[str, dict[str, str]]:
        return {t.name: {c.name: c.type for c in t.columns} for t in self.tables.values()}

    # -------------------------------------------------------------- rendering
    def render(self, tables: list[str] | None = None) -> str:
        """Compact, LLM-friendly description of (a subset of) the schema."""
        selected = [self.tables[n.lower()] for n in tables] if tables else list(self.tables.values())
        blocks = []
        for t in selected:
            lines = [f"TABLE {t.name}  -- {t.description} ({t.row_count} rows)"]
            for c in t.columns:
                parts = [f"  {c.name} {c.type}"]
                if c.primary_key:
                    parts.append("PRIMARY KEY")
                if c.references:
                    parts.append(f"REFERENCES {c.references[0]}({c.references[1]})")
                if not c.nullable and not c.primary_key:
                    parts.append("NOT NULL")
                notes = []
                if c.description:
                    notes.append(c.description)
                if c.values:
                    notes.append("values: " + ", ".join(repr(v) for v in c.values))
                elif c.value_range:
                    notes.append(f"range {c.value_range[0]} .. {c.value_range[1]}")
                elif c.examples:
                    notes.append("e.g. " + ", ".join(repr(v) for v in c.examples))
                line = " ".join(parts)
                if notes:
                    line += "  -- " + "; ".join(notes)
                lines.append(line)
            if t.indexes:
                lines.append("  INDEXES: " + ", ".join(f"{n}({', '.join(cols)})" for n, cols in t.indexes.items()))
            blocks.append("\n".join(lines))
        return "\n\n".join(blocks)

    def render_summary(self, exclude: list[str] | None = None) -> str:
        """One line per table – lets the LLM know what else exists without the full detail."""
        skip = {n.lower() for n in exclude or []}
        lines = []
        for t in self.tables.values():
            if t.name.lower() in skip:
                continue
            cols = ", ".join(
                f"{c.name}->{c.references[0]}" if c.references else c.name for c in t.columns
            )
            lines.append(f"{t.name}({cols})  -- {t.description.split('. Synonyms')[0]}")
        return "\n".join(lines)

    def render_relationships(self, tables: list[str] | None = None) -> str:
        wanted = {n.lower() for n in tables} if tables else None
        lines = [
            f"{fk.table}.{fk.column} -> {fk.ref_table}.{fk.ref_column}"
            for fk in self.foreign_keys
            if wanted is None or (fk.table.lower() in wanted and fk.ref_table.lower() in wanted)
        ]
        return "\n".join(lines) or "(none)"

    def to_dict(self) -> dict:
        return {
            "tables": [
                {
                    "name": t.name,
                    "description": t.description,
                    "row_count": t.row_count,
                    "columns": [
                        {
                            "name": c.name, "type": c.type, "primary_key": c.primary_key,
                            "nullable": c.nullable, "description": c.description,
                            "references": f"{c.references[0]}.{c.references[1]}" if c.references else None,
                        }
                        for c in t.columns
                    ],
                    "indexes": t.indexes,
                }
                for t in self.tables.values()
            ],
            "relationships": [
                {"from": f"{fk.table}.{fk.column}", "to": f"{fk.ref_table}.{fk.ref_column}"}
                for fk in self.foreign_keys
            ],
        }


def _parse_comments(ddl: str) -> tuple[str, dict[str, str]]:
    """Extract the table description and per-column comments from CREATE TABLE text."""
    table_desc = ""
    head = re.search(r"\(\s*--\s*(.+)$", ddl, re.MULTILINE)
    if head:
        table_desc = head.group(1).strip()
    columns = {}
    for m in re.finditer(r"^\s*([A-Za-z_]\w*)\s+[A-Za-z]+.*?--\s*(.+)$", ddl, re.MULTILINE):
        columns[m.group(1).lower()] = m.group(2).strip()
    return table_desc, columns


def _profile_column(conn: sqlite3.Connection, table: str, col: Column) -> None:
    """Attach sample values so the LLM uses real literals (e.g. 'CA', not 'California')."""
    if col.primary_key or col.references:
        return
    q = f'"{col.name}"'
    if col.type == "DATE":
        lo, hi = conn.execute(f'SELECT MIN({q}), MAX({q}) FROM "{table}"').fetchone()
        if lo:
            col.value_range = (lo, hi)
        return
    if col.type != "TEXT" or col.name.lower() in {"email", "phone", "firstname", "lastname"}:
        return
    distinct = conn.execute(f'SELECT COUNT(DISTINCT {q}) FROM "{table}"').fetchone()[0]
    if distinct <= LOW_CARDINALITY_LIMIT:
        col.values = [r[0] for r in conn.execute(
            f'SELECT DISTINCT {q} FROM "{table}" WHERE {q} IS NOT NULL ORDER BY 1')]
    else:
        col.examples = [r[0] for r in conn.execute(
            f'SELECT {q} FROM "{table}" WHERE {q} IS NOT NULL GROUP BY {q} ORDER BY COUNT(*) DESC LIMIT 4')]
