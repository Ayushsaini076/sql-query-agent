"""Schema retrieval: choose the tables relevant to a request.

Lexical scoring against table names, descriptions (which carry synonyms) and column
names, followed by foreign-key path completion so that every selected pair of tables can
be joined. Falls back to the full schema when nothing matches. This keeps prompts small
and focused for large schemas while staying fully deterministic.
"""

from __future__ import annotations

import math
import re
from collections import deque

import sqlglot
from sqlglot import exp

from app.db.catalog import SchemaCatalog

STOPWORDS = {
    "the", "a", "an", "of", "for", "and", "or", "to", "in", "on", "by", "with", "from", "all", "show",
    "list", "me", "what", "which", "who", "how", "many", "much", "is", "are", "was", "were", "each",
    "per", "their", "that", "those", "these", "this", "give", "get", "find", "display", "only", "top",
    "most", "least", "than", "more", "less", "after", "before", "between", "id", "name", "date", "e",
    "g", "i", "us", "usd", "has", "have", "had", "not", "no", "do", "does", "be", "it", "its", "into",
}


def _stem(word: str) -> str:
    word = word.lower()
    for suffix, repl in (("ies", "y"), ("sses", "ss"), ("ses", "s"), ("s", "")):
        if word.endswith(suffix) and len(word) > len(suffix) + 2:
            return word[: -len(suffix)] + repl
    return word


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[A-Za-z]+", re.sub(r"([a-z])([A-Z])", r"\1 \2", text))
    return {_stem(w) for w in words if w.lower() not in STOPWORDS and len(w) > 1}


class SchemaRetriever:
    def __init__(self, catalog: SchemaCatalog, *, relative_cutoff: float = 0.4):
        self.catalog = catalog
        self.relative_cutoff = relative_cutoff
        self._fields: dict[str, list[tuple[set[str], int]]] = {}
        for t in catalog.tables.values():
            # Key columns (EmployeeID on Orders, ...) describe relationships, not topics.
            cols = set().union(*(
                _tokens(c.description) | (set() if c.primary_key or c.references else _tokens(c.name))
                for c in t.columns
            ))
            self._fields[t.name] = [(_tokens(t.name), 3), (_tokens(t.description), 2), (cols, 1)]
        df: dict[str, int] = {}
        for fields in self._fields.values():
            for tok in set().union(*(toks for toks, _ in fields)):
                df[tok] = df.get(tok, 0) + 1
        n = len(catalog.tables)
        self._idf = {tok: math.log((n + 1) / count) for tok, count in df.items()}

    def score(self, question: str) -> dict[str, float]:
        q = _tokens(question)
        scores = {}
        for name, fields in self._fields.items():
            total = 0.0
            for tok in q:
                weight = max((w for toks, w in fields if tok in toks), default=0)
                total += weight * self._idf.get(tok, 0)
            if total > 0:
                scores[name] = total
        return scores

    def retrieve(self, question: str, *, previous_sql: str | None = None, extra_sql: str | None = None) -> list[str]:
        scores = self.score(question)
        best = max(scores.values(), default=0)
        selected = {name for name, s in scores.items() if s >= best * self.relative_cutoff}
        for sql in (previous_sql, extra_sql):
            selected |= set(tables_in_sql(sql, self.catalog)) if sql else set()
        if not selected:
            return self.catalog.table_names
        selected |= self._connectors(selected)
        return [n for n in self.catalog.table_names if n in selected]

    def _connectors(self, tables: set[str]) -> set[str]:
        """Tables on the shortest FK path between each pair of selected tables."""
        out: set[str] = set()
        ordered = sorted(tables)
        for i, a in enumerate(ordered):
            for b in ordered[i + 1:]:
                out |= set(self._path(a, b))
        return out

    def _path(self, start: str, goal: str) -> list[str]:
        queue, seen = deque([[start]]), {start}
        while queue:
            path = queue.popleft()
            if path[-1] == goal:
                return path
            for nxt in sorted(self.catalog.neighbors(path[-1])):
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append(path + [nxt])
        return []


def tables_in_sql(sql: str, catalog: SchemaCatalog) -> list[str]:
    """Known tables referenced by a (possibly broken) SQL string."""
    found = []
    try:
        for t in sqlglot.parse_one(sql, read="sqlite").find_all(exp.Table):
            table = catalog.table(t.name)
            if table and table.name not in found:
                found.append(table.name)
    except Exception:
        for word in re.findall(r"[A-Za-z_]\w*", sql):
            table = catalog.table(word)
            if table and table.name not in found:
                found.append(table.name)
    return found
