def test_index_suggestion_for_unindexed_filter(analyzer):
    analysis = analyzer.analyze("SELECT * FROM Orders WHERE Status = 'Pending' AND OrderDate >= '2026-01-01'")
    statements = [s.statement for s in analysis.index_suggestions]
    # equality column first, then the range column
    assert statements == ["CREATE INDEX idx_orders_status_orderdate ON Orders(Status, OrderDate);"]


def test_automatic_index_triggers_suggestion(analyzer):
    sql = "SELECT p.Name, (SELECT SUM(Quantity) FROM OrderItems oi WHERE oi.ProductID = p.ProductID) FROM Products p"
    assert any("OrderItems(ProductID)" in s.statement for s in analyzer.analyze(sql).index_suggestions)


def test_primary_key_lookup_is_cheap_and_needs_no_index(analyzer):
    analysis = analyzer.analyze("SELECT * FROM Employees WHERE EmployeeID = 5")
    assert analysis.cost.level == "low"
    assert not analysis.index_suggestions


def test_join_cost_uses_index_statistics(analyzer):
    analysis = analyzer.analyze(
        "SELECT c.State, COUNT(*) FROM Customers c JOIN Orders o ON o.CustomerID = c.CustomerID GROUP BY c.State")
    assert analysis.cost.estimated_rows_examined < 5000
    assert analysis.cost.full_scans and analysis.cost.index_lookups


def test_formatting_and_dialects(analyzer):
    analysis = analyzer.analyze("select firstname||' '||lastname as n from employees where salary>100000")
    assert analysis.formatted_sql.startswith("SELECT\n")
    assert "CONCAT" in analysis.dialects["mysql"]
    assert set(analysis.dialects) == {"sqlite", "postgres", "mysql"}


def test_sqlite_only_functions_are_flagged(analyzer):
    analysis = analyzer.analyze("SELECT julianday(ShippedDate) - julianday(OrderDate) FROM Orders")
    assert any("JULIANDAY" in n for n in analysis.dialect_notes["postgres"])
