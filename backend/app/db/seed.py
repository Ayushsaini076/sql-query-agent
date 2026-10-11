"""Build the sample SQLite database from schema.sql with deterministic fake data.

Usage:  python -m app.db.seed [--path data/acme.db] [--force]
"""

from __future__ import annotations

import argparse
import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name("schema.sql")

FIRST_NAMES = [
    "James", "Mary", "Robert", "Patricia", "John", "Jennifer", "Michael", "Linda", "David",
    "Elizabeth", "William", "Barbara", "Richard", "Susan", "Joseph", "Jessica", "Thomas",
    "Sarah", "Carlos", "Karen", "Daniel", "Lisa", "Matthew", "Nancy", "Anthony", "Priya",
    "Mark", "Sandra", "Wei", "Ashley", "Steven", "Aisha", "Paul", "Emily", "Andrew", "Mei",
    "Kenji", "Sofia", "Omar", "Olivia", "Diego", "Fatima", "Ethan", "Hannah", "Noah", "Ava",
]
LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis", "Rodriguez",
    "Martinez", "Hernandez", "Lopez", "Gonzalez", "Wilson", "Anderson", "Thomas", "Taylor",
    "Moore", "Jackson", "Martin", "Lee", "Perez", "Thompson", "White", "Harris", "Sanchez",
    "Clark", "Ramirez", "Lewis", "Robinson", "Walker", "Young", "Allen", "King", "Wright",
    "Nguyen", "Patel", "Chen", "Kim", "Singh", "Tanaka", "Okafor", "Silva", "Novak",
]
# (city, state, weight) – California is deliberately the largest market.
CITIES = [
    ("Los Angeles", "CA", 9), ("San Francisco", "CA", 7), ("San Diego", "CA", 5),
    ("Sacramento", "CA", 3), ("New York", "NY", 8), ("Buffalo", "NY", 2), ("Austin", "TX", 5),
    ("Houston", "TX", 5), ("Dallas", "TX", 4), ("Seattle", "WA", 5), ("Spokane", "WA", 1),
    ("Chicago", "IL", 5), ("Miami", "FL", 4), ("Orlando", "FL", 3), ("Boston", "MA", 4),
    ("Denver", "CO", 3), ("Phoenix", "AZ", 3), ("Atlanta", "GA", 3), ("Portland", "OR", 3),
    ("Columbus", "OH", 2),
]
DEPARTMENTS = [
    ("Sales", "New York", 2_400_000), ("Engineering", "San Francisco", 5_200_000),
    ("Marketing", "Los Angeles", 1_600_000), ("Finance", "New York", 1_100_000),
    ("Human Resources", "Chicago", 700_000), ("Customer Support", "Austin", 900_000),
    ("Operations", "Seattle", 1_800_000), ("Legal", "Boston", 650_000),
]
TITLES = {
    "Sales": ["Head of Sales", "Account Executive", "Sales Representative", "Sales Representative"],
    "Engineering": ["VP of Engineering", "Senior Software Engineer", "Software Engineer", "QA Engineer"],
    "Marketing": ["Marketing Director", "Marketing Manager", "Content Strategist", "SEO Specialist"],
    "Finance": ["CFO", "Financial Analyst", "Accountant", "Payroll Specialist"],
    "Human Resources": ["HR Director", "Recruiter", "HR Generalist", "HR Coordinator"],
    "Customer Support": ["Support Manager", "Support Specialist", "Support Specialist", "Support Agent"],
    "Operations": ["COO", "Operations Manager", "Logistics Coordinator", "Warehouse Supervisor"],
    "Legal": ["General Counsel", "Corporate Lawyer", "Paralegal", "Compliance Officer"],
}
SALARY_BANDS = {0: (180_000, 260_000), 1: (110_000, 170_000), 2: (75_000, 120_000), 3: (55_000, 90_000)}
CATEGORIES = {
    "Electronics": ("Phones, laptops and accessories", [
        ("Wireless Earbuds", 79.99), ("Bluetooth Speaker", 59.0), ("4K Monitor", 329.0),
        ("Mechanical Keyboard", 119.0), ("USB-C Hub", 39.5), ("Noise-Cancelling Headphones", 249.0),
        ("Smartwatch", 199.0), ("Laptop Stand", 45.0)]),
    "Home & Kitchen": ("Cookware and home goods", [
        ("Chef's Knife", 89.0), ("Cast Iron Skillet", 42.0), ("Espresso Machine", 449.0),
        ("Air Fryer", 129.0), ("Blender", 99.0), ("Coffee Grinder", 54.0)]),
    "Office Supplies": ("Stationery and office furniture", [
        ("Ergonomic Chair", 389.0), ("Standing Desk", 549.0), ("Notebook 3-Pack", 14.99),
        ("Gel Pens 12-Pack", 11.5), ("Desk Lamp", 36.0), ("Whiteboard", 64.0)]),
    "Sports & Outdoors": ("Fitness and outdoor gear", [
        ("Yoga Mat", 29.0), ("Adjustable Dumbbells", 299.0), ("Camping Tent", 179.0),
        ("Hiking Backpack", 89.0), ("Water Bottle", 24.0), ("Running Shoes", 129.0)]),
    "Books": ("Print and e-books", [
        ("SQL for Data Analysis", 49.0), ("The Pragmatic Programmer", 44.0),
        ("Designing Data-Intensive Applications", 54.0), ("Clean Code", 39.0)]),
    "Toys & Games": ("Games and puzzles", [
        ("Strategy Board Game", 49.0), ("1000-Piece Puzzle", 22.0), ("Building Blocks Set", 79.0),
        ("Remote Control Car", 64.0)]),
}
SEGMENTS = [("Consumer", 6), ("Small Business", 3), ("Corporate", 2)]
PAYMENT_METHODS = [("Credit Card", 6), ("PayPal", 3), ("Bank Transfer", 1), ("Gift Card", 1)]

DATA_START = date(2024, 1, 1)
DATA_END = date(2026, 9, 30)


def _weighted(rng: random.Random, items: list[tuple]) -> tuple:
    return rng.choices(items, weights=[i[-1] for i in items], k=1)[0]


def _rand_date(rng: random.Random, start: date, end: date) -> date:
    return start + timedelta(days=rng.randint(0, (end - start).days))


def build_database(path: Path, *, force: bool = False, seed: int = 42) -> Path:
    path = Path(path)
    if path.exists():
        if not force:
            return path
        path.unlink()
    path.parent.mkdir(parents=True, exist_ok=True)

    rng = random.Random(seed)
    conn = sqlite3.connect(path)
    try:
        conn.executescript(SCHEMA_PATH.read_text())
        _seed(conn, rng)
        conn.commit()
        conn.execute("ANALYZE")  # gives the query planner real statistics
        conn.commit()
    finally:
        conn.close()
    return path


def _seed(conn: sqlite3.Connection, rng: random.Random) -> None:
    used_emails: set[str] = set()

    def email(first: str, last: str, domain: str) -> str:
        base = f"{first}.{last}".lower().replace("'", "")
        candidate, n = f"{base}@{domain}", 1
        while candidate in used_emails:
            n += 1
            candidate = f"{base}{n}@{domain}"
        used_emails.add(candidate)
        return candidate

    # Departments
    conn.executemany(
        "INSERT INTO Departments (DepartmentID, Name, Location, Budget) VALUES (?, ?, ?, ?)",
        [(i, n, loc, b) for i, (n, loc, b) in enumerate(DEPARTMENTS, start=1)],
    )

    # Employees: a head per department, then staff reporting to the head.
    employees = []
    emp_id = 0
    sales_reps: list[int] = []
    for dept_id, (dept, _, _) in enumerate(DEPARTMENTS, start=1):
        size = 14 if dept == "Sales" else rng.randint(5, 9)
        head_id = None
        for i in range(size):
            emp_id += 1
            level = 0 if i == 0 else min(3, 1 + (i % 3) + (1 if i > 6 else 0))
            first, last = rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)
            hire = _rand_date(rng, date(2015, 1, 1), date(2026, 8, 31))
            if i == 0:
                hire = _rand_date(rng, date(2014, 1, 1), date(2019, 12, 31))
            lo, hi = SALARY_BANDS[level]
            active = 0 if rng.random() < 0.08 else 1
            employees.append((
                emp_id, first, last, email(first, last, "acme.com"), TITLES[dept][level],
                dept_id, head_id, hire.isoformat(), round(rng.uniform(lo, hi), -2), active,
            ))
            if i == 0:
                head_id = emp_id
            elif dept == "Sales" and active:
                sales_reps.append(emp_id)
    conn.executemany("INSERT INTO Employees VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", employees)

    # Customers
    customers = []
    for cid in range(1, 301):
        first, last = rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)
        city, state, _ = _weighted(rng, CITIES)
        customers.append((
            cid, first, last, email(first, last, rng.choice(["gmail.com", "outlook.com", "yahoo.com", "proton.me"])),
            f"({rng.randint(200, 989)}) 555-{rng.randint(1000, 9999)}" if rng.random() > 0.1 else None,
            city, state, "USA", _weighted(rng, SEGMENTS)[0],
            _rand_date(rng, date(2022, 1, 1), date(2026, 6, 30)).isoformat(),
        ))
    conn.executemany("INSERT INTO Customers VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", customers)

    # Categories and products
    products = []
    pid = 0
    for cat_id, (cat, (desc, items)) in enumerate(CATEGORIES.items(), start=1):
        conn.execute("INSERT INTO Categories VALUES (?, ?, ?)", (cat_id, cat, desc))
        for name, price in items:
            pid += 1
            discontinued = 1 if rng.random() < 0.1 else 0
            products.append((pid, name, cat_id, price, 0 if discontinued else rng.randint(0, 400), discontinued))
    conn.executemany("INSERT INTO Products VALUES (?, ?, ?, ?, ?, ?)", products)
    sellable = [p for p in products if not p[5]] or products

    # Orders, items and payments
    order_rows, item_rows, payment_rows = [], [], []
    item_id = pay_id = 0
    for oid in range(1, 1501):
        cust = rng.choice(customers)
        signup = date.fromisoformat(cust[9])
        order_date = _rand_date(rng, max(signup, DATA_START), DATA_END)
        age = (DATA_END - order_date).days
        if age < 5:
            status = rng.choice(["Pending", "Pending", "Shipped"])
        elif age < 15:
            status = rng.choice(["Shipped", "Delivered", "Pending"])
        else:
            status = _weighted(rng, [("Delivered", 16), ("Cancelled", 2), ("Returned", 1), ("Shipped", 1)])[0]
        shipped = None
        if status in ("Shipped", "Delivered", "Returned"):
            shipped = (order_date + timedelta(days=rng.randint(1, 6))).isoformat()
        order_rows.append((oid, cust[0], rng.choice(sales_reps), order_date.isoformat(), shipped, status, cust[5], cust[6]))

        total = 0.0
        for product in rng.sample(sellable, k=rng.choices([1, 2, 3, 4], weights=[5, 4, 2, 1])[0]):
            item_id += 1
            qty = rng.choices([1, 2, 3, 5], weights=[8, 4, 2, 1])[0]
            discount = rng.choice([0, 0, 0, 0.05, 0.1, 0.15, 0.2])
            item_rows.append((item_id, oid, product[0], qty, product[3], discount))
            total += qty * product[3] * (1 - discount)

        if status != "Cancelled":
            pay_id += 1
            method = _weighted(rng, PAYMENT_METHODS)[0]
            payment_rows.append((pay_id, oid, (order_date + timedelta(days=rng.randint(0, 2))).isoformat(), round(total, 2), method))
            if status == "Returned":
                pay_id += 1
                payment_rows.append((pay_id, oid, (order_date + timedelta(days=rng.randint(10, 30))).isoformat(), round(-total, 2), method))

    conn.executemany("INSERT INTO Orders VALUES (?, ?, ?, ?, ?, ?, ?, ?)", order_rows)
    conn.executemany("INSERT INTO OrderItems VALUES (?, ?, ?, ?, ?, ?)", item_rows)
    conn.executemany("INSERT INTO Payments VALUES (?, ?, ?, ?, ?)", payment_rows)


def main() -> None:
    from app.config import get_settings

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", type=Path, default=None)
    parser.add_argument("--force", action="store_true", help="rebuild even if the file exists")
    args = parser.parse_args()
    path = build_database(args.path or get_settings().database_path, force=args.force)
    print(f"Sample database ready at {path}")


if __name__ == "__main__":
    main()
