import io
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from make_sample_workbook import build_workbook  # noqa: E402

ENDPOINTS = ["summary", "income", "net-worth", "cashflow", "budget-rule", "business", "milestones",
             "forecast", "assumptions", "imports"]


def test_empty_db_endpoints_ok(client):
    for e in ENDPOINTS:
        r = client.get(f"/api/finance/{e}")
        assert r.status_code == 200, (e, r.text)
    assert client.get("/api/finance/summary").json()["last_import"] is None
    assert client.get("/api/finance/forecast").json()["scenarios"][1]["name"] == "base"


def _upload(client, tmp_path, months=14):
    p = build_workbook(tmp_path / "up.xlsx", months=months, end=date.today())
    return client.post("/api/finance/import", files={"file": ("budget.xlsx", p.read_bytes())})


def test_upload_and_metrics(client, tmp_path):
    r = _upload(client, tmp_path)
    assert r.status_code == 200 and r.json()["status"] == "ok", r.text
    assert r.json()["rows_income"] > 20 and r.json()["rows_business"] == 14
    s = client.get("/api/finance/summary").json()
    assert s["net_worth"] > 0 and s["invested"] > 0 and s["fi_number"] > 0 and s["emergency_months"] > 0
    assert s["last_import"]["status"] == "ok"
    inc = client.get("/api/finance/income?months=6").json()
    assert len(inc["months"]) == 6 and len(inc["total"]) == 6 and sum(inc["series"]["paycheck"]) > 0
    assert len(client.get("/api/finance/net-worth?months=12").json()["points"]) == 12
    br = client.get("/api/finance/budget-rule").json()
    assert [b["bucket"] for b in br["buckets"]] == ["needs", "wants", "savings"] and br["net_income"] > 0
    assert client.get("/api/finance/business").json()["ytd_profit"] > 0
    fc = client.get("/api/finance/forecast").json()
    assert fc["scenarios"][0]["at"]["10y"] < fc["scenarios"][2]["at"]["10y"]
    assert len(client.get("/api/finance/imports").json()["imports"]) == 1


def test_upload_validation(client):
    r = client.post("/api/finance/import", files={"file": ("x.csv", b"a,b")})
    assert r.status_code == 400
    r = client.post("/api/finance/import", files={"file": ("x.xlsx", b"not a zip")})
    assert r.status_code == 400
    big = io.BytesIO(b"PK" + b"0" * (20 * 1024 * 1024 + 10))
    assert client.post("/api/finance/import", files={"file": ("x.xlsx", big)}).status_code == 413


def test_milestone_crud(client):
    ms = client.get("/api/finance/milestones").json()["milestones"]
    assert len(ms) == 5
    r = client.post("/api/finance/milestones", json={"name": "Car fund", "kind": "custom", "metric": "invested", "target": 1000})
    assert r.status_code == 200 and r.json()["name"] == "Car fund"
    mid = r.json()["id"]
    r = client.put(f"/api/finance/milestones/{mid}", json={"target": 2000})
    assert r.json()["target"] == 2000
    assert client.put("/api/finance/milestones/9999", json={"target": 1}).status_code == 404
    assert client.post("/api/finance/milestones", json={"name": "x", "kind": "bogus", "metric": "net_worth", "target": 1}).status_code == 422
    assert client.delete(f"/api/finance/milestones/{mid}").json() == {"ok": True}
    assert client.delete(f"/api/finance/milestones/{mid}").status_code == 404


def test_assumptions_validation(client):
    good = client.get("/api/finance/assumptions").json()
    assert good["budget_targets"] == {"needs": 50, "wants": 30, "savings": 20}
    good["return_rates"]["base"] = 0.08
    good["monthly_contribution_override"] = 1000
    r = client.put("/api/finance/assumptions", json=good)
    assert r.status_code == 200 and r.json()["return_rates"]["base"] == 0.08
    assert client.get("/api/finance/forecast").json()["monthly_contribution"] == 1000
    for mutate in (lambda a: a["return_rates"].update(base=0.9),
                   lambda a: a.update(safe_withdrawal_rate=0.2),
                   lambda a: a["budget_targets"].update(needs=60)):
        bad = client.get("/api/finance/assumptions").json()
        mutate(bad)
        assert client.put("/api/finance/assumptions", json=bad).status_code == 422


def test_budget_rule_month_param_validation(client):
    assert client.get("/api/finance/budget-rule?month=2025-02").json()["month"] == "2025-02"
    assert client.get("/api/finance/budget-rule?month=bogus").status_code == 422


def test_failed_upload_keeps_previous_workbook(client, tmp_path):
    from openpyxl import Workbook

    from app.config import get_settings
    assert _upload(client, tmp_path).json()["status"] == "ok"
    stored = get_settings().workbook_path
    good = stored.read_bytes()
    bad = tmp_path / "bad.xlsx"
    wb = Workbook()
    wb.active.title = "Unrelated"
    wb.save(bad)
    r = client.post("/api/finance/import", files={"file": ("bad.xlsx", bad.read_bytes())}).json()
    assert r["status"] == "error"
    assert stored.read_bytes() == good
    assert not list(stored.parent.glob(".upload-*"))
