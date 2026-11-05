from datetime import date, datetime

from openpyxl import Workbook

from app.db import get_conn
from app.services.excel_import import import_workbook, parse_amount, parse_date


def _wb(path, income_rows, header_offset=2, with_others=True):
    wb = Workbook()
    ws = wb.active
    ws.title = "income"  # sheet names are matched case-insensitively
    for i in range(header_offset):
        ws.append([f"junk {i}"])
    ws.append([" date ", "SOURCE", "Type", "Amount"])  # note column missing; messy header case
    for r in income_rows:
        ws.append(r)
    if with_others:
        t = wb.create_sheet("Transactions")
        t.append(["Date", "Description", "Category", "Amount"])
        t.append([date(2025, 1, 3), "Rent", "Rent", -1500])
        t.append([date(2025, 1, 4), "Mystery", "Unmapped", "$20.50"])
        t.append([date(2025, 1, 5), "Move", "Transfer", 100])
        b = wb.create_sheet("Net Worth")
        b.append(["Date", "Account", "Type", "Balance"])
        b.append([date(2025, 1, 31), "Checking", "Asset", 1000])
        b.append([date(2025, 1, 31), "Car Loan", "Liability", "(5,000.00)"])
        b.append([date(2025, 1, 31), "Visa", "", 300])
    wb.save(path)
    return path


def test_parsers():
    assert parse_amount("$1,234.56") == 1234.56
    assert parse_amount("(12.00)") == -12.0
    assert parse_amount("abc") is None and parse_amount(None) is None
    assert parse_date("01/31/2025") == "2025-01-31"
    assert parse_date(datetime(2025, 2, 3, 4)) == "2025-02-03"
    assert parse_date("garbage") is None


def test_import_offset_header_and_mapping(settings_env):
    p = _wb(settings_env / "w.xlsx", [
        [datetime(2025, 1, 10), "Tesla", "Tesla Paycheck", "$2,600.00"],
        ["2025-01-15", "Broker", "Dividend", 40],
        ["01/20/2025", "Gig", "", 100],
        [None, None, None, None],
        ["not a date", "x", "y", 5],
        [date(2025, 1, 21), "x", "y", "n/a"],
    ])
    res = import_workbook(p)
    assert res["status"] == "ok", res
    assert (res["rows_income"], res["rows_transactions"], res["rows_balances"], res["rows_business"]) == (3, 3, 3, 0)
    assert "skipped" in res["message"] and "Side Business" in res["message"]
    with get_conn() as c:
        inc = c.execute("SELECT date, type, amount FROM income ORDER BY date").fetchall()
        assert [(r["date"], r["type"], r["amount"]) for r in inc] == [
            ("2025-01-10", "paycheck", 2600.0), ("2025-01-15", "dividend", 40.0), ("2025-01-20", "other", 100.0)]
        tx = {r["category"]: (r["bucket"], r["amount"]) for r in c.execute("SELECT * FROM transactions")}
        assert tx["Rent"] == ("needs", 1500.0)
        assert tx["Unmapped"] == ("wants", 20.5)
        assert tx["Transfer"][0] == "ignore"
        bal = {r["account"]: (r["kind"], r["balance"]) for r in c.execute("SELECT * FROM balances")}
        assert bal["Car Loan"] == ("liability", 5000.0) and bal["Checking"][0] == "asset"
        assert bal["Visa"][0] == "asset"  # blank Type cell; account name has no liability word


def test_skip_unchanged_then_force(settings_env):
    p = _wb(settings_env / "w.xlsx", [["2025-01-10", "Tesla", "paycheck", 1]])
    assert import_workbook(p)["status"] == "ok"
    assert import_workbook(p)["status"] == "skipped"
    assert import_workbook(p, force=True)["status"] == "ok"
    with get_conn() as c:
        assert c.execute("SELECT COUNT(*) FROM imports").fetchone()[0] == 2  # skip recorded nothing
        assert c.execute("SELECT COUNT(*) FROM income").fetchone()[0] == 1  # replaced, not appended


def test_changed_file_replaces_rows(settings_env):
    p = settings_env / "w.xlsx"
    _wb(p, [["2025-01-10", "Tesla", "paycheck", 1], ["2025-01-11", "Tesla", "paycheck", 2]])
    import_workbook(p)
    _wb(p, [["2025-02-10", "Tesla", "paycheck", 3]])
    assert import_workbook(p)["status"] == "ok"
    with get_conn() as c:
        assert [r[0] for r in c.execute("SELECT amount FROM income")] == [3.0]


def test_error_paths_keep_old_data(settings_env):
    good = _wb(settings_env / "w.xlsx", [["2025-01-10", "Tesla", "paycheck", 1]])
    import_workbook(good)
    bad = settings_env / "bad.xlsx"
    bad.write_text("not a workbook")
    res = import_workbook(bad)
    assert res["status"] == "error" and res["message"]
    missing = import_workbook(settings_env / "nope.xlsx")
    assert missing["status"] == "error"
    nohdr = settings_env / "nohdr.xlsx"
    wb = Workbook()
    wb.active.title = "Income"
    wb.active.append(["Foo", "Bar"])
    wb.save(nohdr)
    assert import_workbook(nohdr)["status"] == "error"
    with get_conn() as c:
        assert c.execute("SELECT COUNT(*) FROM income").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM imports WHERE status='error'").fetchone()[0] == 3
