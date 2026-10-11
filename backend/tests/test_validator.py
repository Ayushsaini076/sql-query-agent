import pytest


@pytest.mark.parametrize("sql", [
    "SELECT * FROM Employees WHERE HireDate >= '2024-01-01';",
    "select firstname, lastname from employees where salary > 100000",
    "SELECT c.State, COUNT(*) AS n FROM Customers c JOIN Orders o ON o.CustomerID = c.CustomerID GROUP BY c.State",
    "WITH t AS (SELECT CustomerID, COUNT(*) AS cnt FROM Orders GROUP BY CustomerID) "
    "SELECT c.FirstName, t.cnt FROM t JOIN Customers c ON c.CustomerID = t.CustomerID",
    "SELECT e.FirstName, m.FirstName AS Manager FROM Employees e LEFT JOIN Employees m ON e.ManagerID = m.EmployeeID",
    "SELECT strftime('%Y', OrderDate) AS yr, COUNT(*) FROM Orders GROUP BY yr",
    "SELECT REPLACE(Name, 'a', 'b') FROM Products",
    "SELECT * FROM Orders WHERE Status = 'Deleted' -- harmless literal and comment: DROP TABLE",
    "```sql\nSELECT 1\n```",
])
def test_valid_queries(validator, sql):
    report = validator.validate(sql)
    assert report.valid, report.errors


@pytest.mark.parametrize("sql,op", [
    ("DELETE FROM Orders", "DELETE"),
    ("UPDATE Customers SET State = 'CA'", "UPDATE"),
    ("INSERT INTO Categories (Name) VALUES ('x')", "INSERT"),
    ("DROP TABLE Orders", "DROP"),
    ("ALTER TABLE Orders ADD COLUMN x TEXT", "ALTER"),
    ("TRUNCATE TABLE Orders", "TRUNCATE"),
    ("SELECT 1; DROP TABLE Orders", "DROP"),
    ("WITH x AS (DELETE FROM Orders RETURNING *) SELECT * FROM x", "DELETE"),
])
def test_destructive_queries_are_rejected(validator, sql, op):
    report = validator.validate(sql)
    assert not report.valid
    assert report.destructive
    assert any(op in e for e in report.errors)


@pytest.mark.parametrize("sql", ["PRAGMA writable_schema = 1", "ATTACH DATABASE 'x.db' AS x", "VACUUM"])
def test_admin_statements_are_rejected(validator, sql):
    report = validator.validate(sql)
    assert not report.valid
    assert any("read-only" in e for e in report.errors)


def test_unknown_table_suggests_fix(validator):
    report = validator.validate("SELECT * FROM Employee")
    assert not report.valid
    assert "Did you mean Employees?" in report.errors[0]


def test_unknown_column_suggests_fix(validator):
    report = validator.validate("SELECT FirstNam FROM Employees")
    assert not report.valid
    assert "'FirstNam'" in report.errors[0] and "Employees.FirstName" in report.errors[0]


def test_double_quoted_unknown_column_is_not_treated_as_a_literal(validator):
    # SQLite would silently accept "Foo" as a string literal.
    assert not validator.validate('SELECT "Foo" FROM Customers').valid


def test_syntax_error_uses_engine_message(validator):
    report = validator.validate("SELEC * FROM Orders")
    assert not report.valid
    assert "syntax error" in report.errors[0].lower()


def test_unknown_function_caught_by_sqlite(validator):
    report = validator.validate("SELECT DATE_TRUNC('month', OrderDate) FROM Orders")
    assert not report.valid
    assert "no such function" in report.errors[0]


def test_incompatible_join_is_error_and_non_fk_join_is_warning(validator):
    bad = validator.validate("SELECT 1 FROM Customers c JOIN Orders o ON o.Status = c.CustomerID")
    assert not bad.valid and "incompatible types" in bad.errors[0]
    odd = validator.validate("SELECT 1 FROM Customers c JOIN Orders o ON o.OrderID = c.CustomerID")
    assert odd.valid and "not a declared foreign-key" in odd.warnings[0]


def test_cartesian_product_warning(validator):
    report = validator.validate("SELECT * FROM Customers, Orders")
    assert report.valid
    assert any("Cartesian" in w for w in report.warnings)
    linked = validator.validate("SELECT * FROM Customers c, Orders o WHERE o.CustomerID = c.CustomerID")
    assert not linked.warnings


def test_select_alias_in_where_is_accepted(validator):
    # sqlglot cannot resolve this, but SQLite accepts it – the engine is authoritative.
    report = validator.validate("SELECT State AS st FROM Customers WHERE st = 'CA'")
    assert report.valid


def test_empty_and_multiple_statements(validator):
    assert not validator.validate("").valid
    assert not validator.validate("SELECT 1; SELECT 2").valid


# --- regressions from code review --------------------------------------------------------
@pytest.mark.parametrize("sql", [
    "SELECT * FROM pragma_table_info('Orders')",
    "SELECT load_extension('/tmp/x')",
    "SELECT name FROM sqlite_master",
    "SELECT writefile('/tmp/x', 'y')",
])
def test_system_functions_and_tables_rejected(validator, sql):
    assert not validator.validate(sql).valid


def test_json_table_function_allowed(validator):
    assert validator.validate("SELECT value FROM json_each('[1, 2]')").valid


def test_quoted_alias_with_keyword_is_not_destructive(validator):
    report = validator.validate('SELECT MAX(OrderDate) AS "Last Update" FROM Orders')
    assert report.valid and not report.destructive


def test_unknown_double_quoted_column_rejected_even_when_static_check_is_inconclusive(validator):
    assert not validator.validate('SELECT rowid, "Revenue" FROM Customers').valid
