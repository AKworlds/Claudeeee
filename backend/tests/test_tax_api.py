def test_settings_validation(client):
    s = client.get("/api/tax/settings").json()
    assert s == {"federal_bracket": 0.22, "state_rate": 0.0, "buffer_pct": 0.0, "use_qbi": True}
    r = client.put("/api/tax/settings", json={"federal_bracket": 0.12, "buffer_pct": 5, "w2_income": 60000})
    assert r.json()["federal_bracket"] == 0.12 and r.json()["buffer_pct"] == 5 and "w2_income" not in r.json()
    for bad in ({"state_rate": 0.5}, {"buffer_pct": 60}, {"federal_bracket": 0.15}, {"federal_bracket": 22}):
        assert client.put("/api/tax/settings", json=bad).status_code == 422
    assert client.get("/api/tax/settings").json()["federal_bracket"] == 0.12


def test_payout_crud(client):
    r = client.post("/api/tax/payouts", json={"date": "2026-03-01", "amount": 1000})
    assert r.status_code == 201
    p = r.json()
    assert p["set_aside"] == 305 and p["rate"] == 0.305 and p["profit"] == 1000
    r = client.put(f"/api/tax/payouts/{p['id']}", json={"saved": 412})
    assert r.json()["saved"] == 412 and r.json()["set_aside"] == 305
    lst = client.get("/api/tax/payouts?year=2026").json()["payouts"]
    assert len(lst) == 1 and lst[0]["saved"] == 412
    assert client.delete(f"/api/tax/payouts/{p['id']}").json() == {"ok": True}
    assert client.delete(f"/api/tax/payouts/{p['id']}").status_code == 404
    assert client.put("/api/tax/payouts/999", json={"saved": 1}).status_code == 404


def test_payout_validation(client):
    for bad in ({"amount": 0}, {"amount": -5}, {}, {"amount": 5, "expenses": -1}, {"amount": 5, "date": "nope"}):
        assert client.post("/api/tax/payouts", json=bad).status_code == 422
    assert client.post("/api/tax/payouts", json={"amount": 5}).status_code == 201  # date defaults to today


def test_summary_shape_and_payments(client):
    client.post("/api/tax/payouts", json={"date": "2026-03-01", "amount": 1000, "saved": 100})
    s = client.get("/api/tax/summary?year=2026").json()
    for k in ("year", "profit_ytd", "tax", "effective_rate", "marginal_rate", "set_aside_total",
              "saved_total", "shortfall", "quarters", "projection", "notes"):
        assert k in s
    assert set(s["tax"]) == {"se", "federal", "state", "total"} and len(s["quarters"]) == 4
    assert s["set_aside_total"] == 305 and s["shortfall"] == 205
    r = client.post("/api/tax/payments", json={"date": "2026-04-01", "amount": 305, "period": "2026-Q1"})
    assert r.status_code == 201
    assert client.get("/api/tax/summary?year=2026").json()["quarters"][0]["paid"] == 305
    assert len(client.get("/api/tax/payments").json()["payments"]) == 1
    assert client.post("/api/tax/payments", json={"amount": 0, "period": "x"}).status_code == 422
    assert client.delete(f"/api/tax/payments/{r.json()['id']}").json() == {"ok": True}
    assert client.delete("/api/tax/payments/99").status_code == 404


def test_business_chart_includes_payouts(client):
    from datetime import date
    d = date.today().isoformat()
    client.post("/api/tax/payouts", json={"date": d, "amount": 500, "expenses": 100})
    b = client.get("/api/finance/business").json()
    assert b["ytd_profit"] == 400
    assert sum(p["profit"] for p in b["points"]) == 400
