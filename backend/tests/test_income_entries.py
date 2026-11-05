import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from make_sample_workbook import build_workbook  # noqa: E402

URL = "/api/finance/income-entries"


def _add(client, **kw):
    body = {"date": "2025-03-15", "source": "Paycheck", "type": "paycheck", "amount": 1234.56, **kw}
    return client.post(URL, json=body)


def test_crud(client):
    assert client.get(URL).json() == {"entries": []}
    r = _add(client, note="march")
    assert r.status_code == 201
    e = r.json()
    assert e["amount"] == 1234.56 and e["note"] == "march" and e["created_at"]
    _add(client, date="2025-04-01", source="Dividends", type="dividend", amount=50)
    assert [x["date"] for x in client.get(URL).json()["entries"]] == ["2025-04-01", "2025-03-15"]
    assert len(client.get(URL + "?limit=1").json()["entries"]) == 1
    r = client.put(f"{URL}/{e['id']}", json={"amount": 2000, "type": "other"})
    assert r.status_code == 200 and r.json()["amount"] == 2000 and r.json()["source"] == "Paycheck"
    assert client.delete(f"{URL}/{e['id']}").json() == {"ok": True}
    assert client.delete(f"{URL}/{e['id']}").status_code == 404
    assert client.put(f"{URL}/{e['id']}", json={"amount": 5}).status_code == 404


def test_default_date_today(client):
    r = client.post(URL, json={"source": "X", "type": "other", "amount": 5})
    assert r.status_code == 201 and r.json()["date"] == date.today().isoformat()


def test_validation(client):
    assert _add(client, amount=0).status_code == 422
    assert _add(client, amount=-5).status_code == 422
    assert _add(client, amount=10_000_001).status_code == 422
    assert _add(client, type="lottery").status_code == 422
    assert _add(client, source="x" * 81).status_code == 422
    assert _add(client, source="  ").status_code == 422
    assert _add(client, date="03/15/2025").status_code == 422
    assert client.post(URL, json={"type": "other", "amount": 5}).status_code == 422
    assert client.put(f"{URL}/1", json={"type": "nope"}).status_code == 422
    assert client.get(URL).json()["entries"] == []


def test_entries_counted_in_metrics(client):
    _add(client, date="2025-03-15", amount=3000)
    _add(client, date="2025-03-20", source="Div", type="dividend", amount=200)
    inc = client.get("/api/finance/income?months=3").json()
    assert inc["months"][-1] == "2025-03"
    assert inc["series"]["paycheck"][-1] == 3000 and inc["series"]["dividend"][-1] == 200
    assert inc["total"][-1] == 3200
    s = client.get("/api/finance/summary").json()
    assert s["monthly_income"] == 3200
    cf = client.get("/api/finance/cashflow?months=3").json()["points"][-1]
    assert cf["month"] == "2025-03" and cf["income"] == 3200 and cf["savings"] == 3200
    br = client.get("/api/finance/budget-rule?month=2025-03").json()
    assert br["net_income"] == 3200


def test_entries_survive_reimport_and_add_to_workbook(client, tmp_path):
    p = build_workbook(tmp_path / "up.xlsx", months=14, end=date.today())
    up = lambda: client.post("/api/finance/import", files={"file": ("budget.xlsx", p.read_bytes())})
    assert up().status_code == 200
    _add(client, date="2025-01-10", amount=777)
    e = client.get(URL).json()["entries"]
    assert len(e) == 1
    r = up()
    assert r.status_code == 200 and r.json()["status"] == "ok"
    assert client.get(URL).json()["entries"] == e


def test_entry_adds_to_workbook_month(client, tmp_path):
    p = build_workbook(tmp_path / "up.xlsx", months=14, end=date.today())
    client.post("/api/finance/import", files={"file": ("budget.xlsx", p.read_bytes())})
    month = client.get("/api/finance/income?months=3").json()["months"][-1]
    before = client.get("/api/finance/income?months=3").json()["total"][-1]
    _add(client, date=month + "-02", amount=500)
    assert client.get("/api/finance/income?months=3").json()["total"][-1] == round(before + 500, 2)


def test_income_sources_merges_tables(client, tmp_path):
    assert client.get("/api/finance/income-sources").json() == {"sources": []}
    p = build_workbook(tmp_path / "up.xlsx", months=14, end=date.today())
    client.post("/api/finance/import", files={"file": ("budget.xlsx", p.read_bytes())})
    wb = client.get("/api/finance/income-sources").json()["sources"]
    assert wb and len(wb) <= 20
    _add(client, date=date.today().isoformat(), source="Freelance Zed", type="business", amount=10)
    src = client.get("/api/finance/income-sources").json()["sources"]
    assert src[0] == "Freelance Zed" and len(src) == len(set(src)) <= 20
    assert set(wb) - set(src) == set() or len(src) == 20


def test_summary_shows_month_to_date_income(client):
    from datetime import date
    today = date.today().isoformat()
    prev = date.today().replace(day=1).toordinal() - 1
    last_month = date.fromordinal(prev).isoformat()
    client.post("/api/finance/income-entries", json={"date": last_month, "source": "Paycheck", "type": "paycheck", "amount": 2000})
    client.post("/api/finance/income-entries", json={"date": today, "source": "Paycheck", "type": "paycheck", "amount": 1500})
    s = client.get("/api/finance/summary").json()
    assert s["monthly_income"] == 2000 and s["income_month"] == last_month[:7]
    assert s["month_to_date_income"] == 1500
