-- Acme Retail sample database (SQLite).
--
-- Comments are part of the schema contract: the first comment inside each
-- CREATE TABLE is the table description and trailing "--" comments describe
-- columns. The agent reads them from sqlite_master to ground SQL generation,
-- so keep one column per line.

PRAGMA foreign_keys = ON;

CREATE TABLE Departments ( -- Business units of the company. Synonyms: department, dept, team, division, unit, budget
    DepartmentID INTEGER PRIMARY KEY,
    Name         TEXT    NOT NULL UNIQUE, -- Department name, e.g. 'Sales', 'Engineering'
    Location     TEXT    NOT NULL,        -- Office city where the department is based
    Budget       REAL    NOT NULL         -- Annual budget in USD
);

CREATE TABLE Employees ( -- Company staff, including sales reps who handle orders. Synonyms: employee, staff, worker, colleague, rep, salesperson, hire, manager
    EmployeeID   INTEGER PRIMARY KEY,
    FirstName    TEXT    NOT NULL,
    LastName     TEXT    NOT NULL,
    Email        TEXT    NOT NULL UNIQUE,
    JobTitle     TEXT    NOT NULL,
    DepartmentID INTEGER NOT NULL REFERENCES Departments(DepartmentID),
    ManagerID    INTEGER REFERENCES Employees(EmployeeID), -- The employee's manager; NULL for department heads
    HireDate     DATE    NOT NULL,        -- ISO date 'YYYY-MM-DD'
    Salary       REAL    NOT NULL,        -- Annual salary in USD
    IsActive     INTEGER NOT NULL DEFAULT 1 -- 1 = currently employed, 0 = has left the company
);

CREATE TABLE Customers ( -- People and businesses who place orders. Synonyms: customer, client, buyer, shopper, account
    CustomerID   INTEGER PRIMARY KEY,
    FirstName    TEXT    NOT NULL,
    LastName     TEXT    NOT NULL,
    Email        TEXT    NOT NULL UNIQUE,
    Phone        TEXT,
    City         TEXT    NOT NULL,
    State        TEXT    NOT NULL,        -- Two-letter US state code, e.g. 'CA' for California, 'NY' for New York
    Country      TEXT    NOT NULL DEFAULT 'USA',
    Segment      TEXT    NOT NULL,        -- Customer segment
    SignupDate   DATE    NOT NULL         -- ISO date the customer registered
);

CREATE TABLE Categories ( -- Product categories. Synonyms: category, type, group, department of products
    CategoryID   INTEGER PRIMARY KEY,
    Name         TEXT    NOT NULL UNIQUE,
    Description  TEXT
);

CREATE TABLE Products ( -- Items the company sells. Synonyms: product, item, sku, goods, inventory, stock, catalog
    ProductID    INTEGER PRIMARY KEY,
    Name         TEXT    NOT NULL,
    CategoryID   INTEGER NOT NULL REFERENCES Categories(CategoryID),
    UnitPrice    REAL    NOT NULL,        -- Current list price in USD
    UnitsInStock INTEGER NOT NULL,        -- Units currently in the warehouse
    Discontinued INTEGER NOT NULL DEFAULT 0 -- 1 = no longer sold
);

CREATE TABLE Orders ( -- Customer orders (one row per order). Synonyms: order, sale, purchase, transaction, shipment
    OrderID      INTEGER PRIMARY KEY,
    CustomerID   INTEGER NOT NULL REFERENCES Customers(CustomerID),
    EmployeeID   INTEGER NOT NULL REFERENCES Employees(EmployeeID), -- Sales rep who handled the order
    OrderDate    DATE    NOT NULL,        -- ISO date the order was placed
    ShippedDate  DATE,                    -- NULL until the order ships
    Status       TEXT    NOT NULL,        -- Order lifecycle status
    ShipCity     TEXT    NOT NULL,
    ShipState    TEXT    NOT NULL         -- Two-letter US state code of the shipping address
);

CREATE TABLE OrderItems ( -- Line items of each order; revenue = Quantity * UnitPrice * (1 - Discount). Synonyms: line item, item sold, units sold, revenue, sales amount
    OrderItemID  INTEGER PRIMARY KEY,
    OrderID      INTEGER NOT NULL REFERENCES Orders(OrderID),
    ProductID    INTEGER NOT NULL REFERENCES Products(ProductID),
    Quantity     INTEGER NOT NULL,
    UnitPrice    REAL    NOT NULL,        -- Price per unit at the time of sale
    Discount     REAL    NOT NULL DEFAULT 0 -- Fractional discount between 0 and 0.3
);

CREATE TABLE Payments ( -- Payments received against orders. Synonyms: payment, paid, amount, charge, refund, billing
    PaymentID    INTEGER PRIMARY KEY,
    OrderID      INTEGER NOT NULL REFERENCES Orders(OrderID),
    PaymentDate  DATE    NOT NULL,
    Amount       REAL    NOT NULL,        -- Amount paid in USD
    Method       TEXT    NOT NULL         -- Payment method
);

-- Only some foreign keys are indexed on purpose, so the optimizer has
-- realistic index recommendations to make (e.g. Orders.OrderDate,
-- Customers.State, OrderItems.ProductID).
CREATE INDEX idx_employees_department ON Employees(DepartmentID);
CREATE INDEX idx_orders_customer      ON Orders(CustomerID);
CREATE INDEX idx_orderitems_order     ON OrderItems(OrderID);
CREATE INDEX idx_payments_order       ON Payments(OrderID);
