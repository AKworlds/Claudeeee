"""Generate a deterministic, realistic sample budget workbook.

Usage: python scripts/make_sample_workbook.py [out_path]   (default ../data/sample_budget.xlsx)
Headers match DEFAULT_YAML in app/config.py.
"""
from __future__ import annotations

import calendar
import random
import sys
from datetime import date, timedelta
from pathlib import Path

from openpyxl import Workbook

# (category, description, monthly amount, occurrences per month, jitter fraction)
SPEND = [
    ("Rent", "Apartment rent", 1450, 1, 0), ("Utilities", "Electric & water", 150, 1, 0.25),
    ("Groceries", "Grocery run", 130, 4, 0.25), ("Insurance", "Auto insurance", 140, 1, 0),
    ("Gas", "Fuel", 70, 2, 0.2), ("Car Payment", "Auto loan payment", 420, 1, 0),
    ("Phone", "Mobile plan", 70, 1, 0), ("Internet", "Home internet", 60, 1, 0),
    ("Dining", "Restaurant", 65, 9, 0.4), ("Entertainment", "Movies & games", 80, 3, 0.5),
    ("Shopping", "Online order", 120, 5, 0.6), ("Subscriptions", "Streaming & apps", 45, 1, 0.05),
    ("Roth IRA", "Roth IRA contribution", 500, 1, 0), ("Brokerage", "Brokerage deposit", 400, 1, 0),
    ("Emergency Fund", "HYSA transfer", 300, 1, 0),
]


def _month_start(end: date, months: int) -> date:
    idx = end.year * 12 + end.month - 1 - (months - 1)
    return date(idx // 12, idx % 12 + 1, 1)


def _add_months(d: date, n: int) -> date:
    idx = d.year * 12 + d.month - 1 + n
    return date(idx // 12, idx % 12 + 1, 1)


def _month_end(d: date) -> date:
    return date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])


def build_workbook(path: Path, months: int = 24, end: date | None = None, seed: int = 42) -> Path:
    """Write a sample workbook covering `months` months ending at `end` (default today)."""
    rnd = random.Random(seed)
    end = end or date.today()
    first = _month_start(end, months)
    month_starts = [_add_months(first, i) for i in range(months)]
    wb = Workbook()

    ws = wb.active
    ws.title = "Income"
    ws.append(["Date", "Source", "Type", "Amount", "Note"])
    d = first
    while d <= end:  # biweekly Tesla net paychecks
        ws.append([d, "Tesla", "Tesla Paycheck", round(2600 + rnd.uniform(-40, 40), 2), "Net pay"])
        d += timedelta(days=14)
    div = 40.0
    for ms in month_starts:
        if ms.month in (3, 6, 9, 12) and date(ms.year, ms.month, 15) <= end:
            ws.append([date(ms.year, ms.month, 15), "Brokerage", "Dividend", round(div, 2), "Quarterly dividend"])
            div *= 1.08
        if _month_end(ms) <= end:
            ws.append([_month_end(ms), "HYSA", "Interest", round(25 + rnd.uniform(0, 8), 2), ""])

    ws = wb.create_sheet("Transactions")
    ws.append(["Date", "Description", "Category", "Amount"])
    for ms in month_starts:
        last_day = min(_month_end(ms), end).day
        for cat, desc, amt, n, jit in SPEND:
            for k in range(n):
                day = min(1 + (k * 28) // n + rnd.randint(0, 2), last_day)
                if date(ms.year, ms.month, day) > end:
                    continue
                ws.append([date(ms.year, ms.month, day), desc, cat, round(amt * (1 + rnd.uniform(-jit, jit)), 2)])
        if rnd.random() < 0.25 and ms.month in (6, 7, 8, 11, 12):
            ws.append([date(ms.year, ms.month, min(10, last_day)), "Flights & hotel", "Travel", round(rnd.uniform(400, 900), 2)])
        if rnd.random() < 0.3:
            ws.append([date(ms.year, ms.month, min(12, last_day)), "Doctor / pharmacy", "Healthcare", round(rnd.uniform(30, 150), 2)])

    ws = wb.create_sheet("Net Worth")
    ws.append(["Date", "Account", "Type", "Balance"])
    bal = {"Checking": 4000.0, "HYSA": 8000.0, "401k": 30000.0, "Roth IRA": 12000.0, "Brokerage": 6000.0}
    car, card = 21000.0, 900.0
    for ms in month_starts:
        snap = min(_month_end(ms), end)
        bal["Checking"] = 4000 + rnd.uniform(-600, 900)
        bal["HYSA"] += 300 + rnd.uniform(20, 35)
        bal["401k"] = bal["401k"] * (1 + rnd.uniform(-0.01, 0.025)) + 700
        bal["Roth IRA"] = bal["Roth IRA"] * (1 + rnd.uniform(-0.01, 0.025)) + 500
        bal["Brokerage"] = bal["Brokerage"] * (1 + rnd.uniform(-0.015, 0.025)) + 400
        car = max(car - 420 + 90, 0)  # principal portion of payment
        card = rnd.uniform(500, 1800)
        for acct, v in bal.items():
            ws.append([snap, acct, "Asset", round(v, 2)])
        ws.append([snap, "Car Loan", "Liability", round(car, 2)])
        ws.append([snap, "Credit Card", "Liability", round(card, 2)])

    ws = wb.create_sheet("Side Business")
    ws.append(["Date", "Description", "Revenue", "Expense"])
    for i, ms in enumerate(month_starts):
        d = min(_month_end(ms), end)
        ws.append([d, "Freelance projects", round(300 + i * 18 + rnd.uniform(0, 250), 2), round(rnd.uniform(80, 200), 2)])

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[2] / "data" / "sample_budget.xlsx"
    print(f"wrote {build_workbook(out)}")
