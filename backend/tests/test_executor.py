import sqlite3

import pytest

from app.db.executor import QueryTimeout


@pytest.mark.parametrize("sql", [
    "DELETE FROM Orders", "DROP TABLE Orders", "UPDATE Customers SET State = 'XX'",
    "INSERT INTO Categories (Name) VALUES ('x')", "PRAGMA query_only = OFF",
    "ATTACH DATABASE ':memory:' AS other", "CREATE TABLE t (x)",
])
def test_writes_are_impossible_even_without_the_validator(db, sql):
    with pytest.raises(sqlite3.DatabaseError):
        db.execute(sql)
    assert db.execute("SELECT COUNT(*) FROM Orders").rows[0][0] == 1500


def test_row_cap_and_truncation(db):
    result = db.execute("SELECT * FROM Orders", max_rows=10)
    assert result.row_count == 10 and result.truncated
    assert result.columns[0] == "OrderID"


def test_timeout(db):
    with pytest.raises(QueryTimeout):
        db.execute("WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM n) SELECT COUNT(*) FROM n",
                   timeout_s=0.5)


def test_doc_example_returns_rows(db):
    result = db.execute("SELECT * FROM Employees WHERE HireDate >= '2024-01-01'")
    assert result.row_count > 0


def test_blob_values_are_json_safe(db):
    result = db.execute("SELECT randomblob(4) AS b")
    assert result.rows[0][0].startswith("<BLOB 4 bytes")


def test_huge_values_are_capped(db):
    with pytest.raises(sqlite3.DatabaseError):
        db.execute("SELECT zeroblob(1000000000)")
