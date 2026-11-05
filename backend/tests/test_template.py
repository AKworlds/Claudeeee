from datetime import date

from openpyxl import load_workbook

from app.db import get_conn
from app.services.excel_import import import_workbook
from app.services.template import build_template

SHEETS = ["Start Here", "Income", "Transactions", "Net Worth", "Side Business", "Job Applications", "Lists", "Summary"]


def test_structure(tmp_path):
    wb = load_workbook(build_template(tmp_path / "t.xlsx"))
    assert wb.sheetnames == SHEETS
    assert wb["Lists"].sheet_state == "hidden"
    assert [c.value for c in wb["Income"][1]] == ["Date", "Source", "Type", "Amount", "Note"]
    assert [c.value for c in wb["Transactions"][1]][:4] == ["Date", "Description", "Category", "Amount"]
    assert [c.value for c in wb["Job Applications"][1]] == ["Company", "Role", "Status", "Applied On", "Last Update", "Source", "URL", "Notes"]
    for n in ("Income", "Transactions", "Net Worth", "Side Business", "Job Applications"):
        assert wb[n].tables and wb[n].freeze_panes == "A2"
    assert len(wb["Income"].data_validations.dataValidation) == 1
    cat = wb["Transactions"].data_validations.dataValidation[0]
    assert cat.showErrorMessage is False
    assert wb["Net Worth"].data_validations.dataValidation and wb["Job Applications"].data_validations.dataValidation
    assert wb["Summary"]["B2"].value.startswith("=SUMIFS")


def test_import_empty_and_filled(settings_env, tmp_path):
    p = build_template(tmp_path / "t.xlsx")
    r = import_workbook(p)
    assert r["status"] == "ok" and r["rows_income"] == 0 and "applications: 0 rows" in r["message"]
    wb = load_workbook(p)
    wb["Income"].append([date(2026, 1, 15), "Employer", "Paycheck", 2500, ""])
    # appended rows land after the pre-sized table; write into the first blank rows instead
    ws = wb["Income"]
    ws.delete_rows(ws.max_row)
    ws["A2"], ws["B2"], ws["C2"], ws["D2"] = date(2026, 1, 15), "Employer", "Paycheck", 2500
    wb["Transactions"]["A2"], wb["Transactions"]["C2"], wb["Transactions"]["D2"] = date(2026, 1, 3), "Groceries", 84.2
    wb.save(tmp_path / "f.xlsx")
    r = import_workbook(tmp_path / "f.xlsx")
    assert (r["status"], r["rows_income"], r["rows_transactions"]) == ("ok", 1, 1)


def test_applications_prefill(settings_env, tmp_path):
    apps = [{"company": "Acme", "role": "SRE", "status": "interview", "applied_on": "2026-10-01", "url": "https://x.test"},
            {"company": "Globex", "role": "", "status": "bogus"}]
    ws = load_workbook(build_template(tmp_path / "t.xlsx", apps))["Job Applications"]
    assert ws["D2"].value.date() == date(2026, 10, 1)
    r = import_workbook(tmp_path / "t.xlsx")
    assert r["status"] == "ok" and "applications: 2 rows" in r["message"]
    with get_conn() as conn:
        got = {x["company"]: dict(x) for x in conn.execute("SELECT * FROM applications")}
    assert got["Acme"]["status"] == "interview" and got["Acme"]["external_id"].startswith("sheet:")
    assert got["Globex"]["status"] == "applied"
