"""Read-only SQLite access.

Defense in depth: even if a write statement slipped past the SQL validator, it
could not run here, because
  1. the file is opened with mode=ro,
  2. PRAGMA query_only is enabled, and
  3. an authorizer callback denies every action except reading.
Queries are also bounded by a wall-clock timeout and a row cap.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import asdict, dataclass
from pathlib import Path

_ALLOWED_ACTIONS = {
    sqlite3.SQLITE_SELECT,
    sqlite3.SQLITE_READ,
    sqlite3.SQLITE_FUNCTION,
    getattr(sqlite3, "SQLITE_RECURSIVE", 33),
}


def _authorizer(action: int, *_args) -> int:
    return sqlite3.SQLITE_OK if action in _ALLOWED_ACTIONS else sqlite3.SQLITE_DENY


MAX_VALUE_BYTES = 1_000_000


def read_only_uri(path: Path | str) -> str:
    return f"{Path(path).resolve().as_uri()}?mode=ro"


def _json_safe(value):
    if isinstance(value, (bytes, bytearray, memoryview)):
        data = bytes(value)
        return f"<BLOB {len(data)} bytes: {data[:16].hex()}{'…' if len(data) > 16 else ''}>"
    return value


class QueryTimeout(Exception):
    pass


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[list]
    row_count: int
    truncated: bool
    elapsed_ms: float

    def to_dict(self) -> dict:
        return asdict(self)


class ReadOnlyDatabase:
    def __init__(self, path: Path | str, *, timeout_s: float = 5.0):
        self.path = Path(path)
        self.timeout_s = timeout_s

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(read_only_uri(self.path), uri=True, check_same_thread=False)
        conn.execute("PRAGMA query_only = ON")
        if hasattr(conn, "setconfig"):  # Python 3.12+
            # Unknown "double-quoted" names must be errors, not silently become string literals.
            conn.setconfig(sqlite3.SQLITE_DBCONFIG_DQS_DML, False)
            conn.setconfig(sqlite3.SQLITE_DBCONFIG_DQS_DDL, False)
        conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_VALUE_BYTES)  # e.g. zeroblob(1e9)
        conn.set_authorizer(_authorizer)
        return conn

    def _with_deadline(self, conn: sqlite3.Connection, timeout_s: float) -> None:
        deadline = time.monotonic() + timeout_s
        conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)

    def explain_plan(self, sql: str) -> list[dict]:
        """Compile the query without running it; raises sqlite3.Error if SQLite rejects it."""
        conn = self._connect()
        try:
            rows = conn.execute(f"EXPLAIN QUERY PLAN {sql}").fetchall()
            return [{"id": r[0], "parent": r[1], "detail": r[3]} for r in rows]
        finally:
            conn.close()

    def execute(self, sql: str, *, max_rows: int = 200, timeout_s: float | None = None) -> QueryResult:
        conn = self._connect()
        self._with_deadline(conn, timeout_s or self.timeout_s)
        start = time.perf_counter()
        try:
            cur = conn.execute(sql)
            fetched = cur.fetchmany(max_rows + 1)
        except sqlite3.OperationalError as exc:
            if "interrupted" in str(exc):
                raise QueryTimeout(f"Query exceeded the {timeout_s or self.timeout_s:.0f}s time limit") from exc
            raise
        finally:
            elapsed = (time.perf_counter() - start) * 1000
            conn.close()
        columns = [d[0] for d in cur.description or []]
        truncated = len(fetched) > max_rows
        rows = [[_json_safe(v) for v in r] for r in fetched[:max_rows]]
        return QueryResult(columns, rows, len(rows), truncated, round(elapsed, 2))
