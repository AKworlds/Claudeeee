from datetime import date

import pytest

from app.db import get_conn
from app.services import finance as fin

TODAY = date(2025, 4, 15)


def seed(c):
    # Jan-Mar: income 5000 (paycheck 4900 + dividend 100); needs 2000, wants 1500
    for m in ("2025-01", "2025-02", "2025-03"):
        c.execute("INSERT INTO income (date, source, type, amount) VALUES (?, 'T', 'paycheck', 4900)", (m + "-05",))
        c.execute("INSERT INTO income (date, source, type, amount) VALUES (?, 'B', 'dividend', 100)", (m + "-20",))
        c.execute("INSERT INTO transactions (date, category, bucket, amount) VALUES (?, 'Rent', 'needs', 2000)", (m + "-01",))
        c.execute("INSERT INTO transactions (date, category, bucket, amount) VALUES (?, 'Dining', 'wants', 1500)", (m + "-09",))
        c.execute("INSERT INTO transactions (date, category, bucket, amount) VALUES (?, 'X', 'ignore', 999)", (m + "-09",))
    # April (current, partial)
    c.execute("INSERT INTO income (date, source, type, amount) VALUES ('2025-04-05', 'T', 'paycheck', 100)")


def test_empty_db(settings_env):
    with get_conn() as c:
        s = fin.summary(c, TODAY)
        assert s["net_worth"] == 0 and s["fi_number"] == 0 and s["fi_progress"] == 0 and s["last_import"] is None
        assert fin.net_worth_series(c, 12, TODAY) == {"points": []}
        assert fin.budget_rule(c, None, TODAY)["month"] == "2025-04"
        f = fin.forecast(c, TODAY)
        assert f["scenarios"][0]["at"]["10y"] == 0 and len(f["scenarios"][0]["series"]) == 121
        assert len(fin.income_series(c, 6, TODAY)["months"]) == 6
        assert fin.business(c, 3, TODAY)["ytd_profit"] == 0


def test_budget_rule_default_month_and_math(settings_env):
    with get_conn() as c:
        seed(c)
        r = fin.budget_rule(c, None, TODAY)
        assert r["month"] == "2025-03"  # April is current, not complete
        assert r["net_income"] == 5000
        b = {x["bucket"]: x for x in r["buckets"]}
        assert (b["needs"]["actual_pct"], b["needs"]["target_pct"], b["needs"]["delta_pct"]) == (40.0, 50, -10.0)
        assert b["wants"]["actual_pct"] == 30.0 and b["wants"]["delta_pct"] == 0.0
        assert b["savings"]["actual"] == 1500 and b["savings"]["actual_pct"] == 30.0 and b["savings"]["delta_pct"] == 10.0
        assert fin.budget_rule(c, "2025-02", TODAY)["month"] == "2025-02"
        assert fin.budget_rule(c, "2024-01", TODAY)["net_income"] == 0  # no data: no ZeroDivision


def test_cashflow_and_income_series(settings_env):
    with get_conn() as c:
        seed(c)
        after = date(2025, 5, 2)  # April complete
        cf = fin.cashflow(c, 3, after)["points"]
        assert [p["month"] for p in cf] == ["2025-02", "2025-03", "2025-04"]
        # while April is in progress it is left out of flow charts (partial month)
        assert fin.cashflow(c, 3, TODAY)["points"][-1]["month"] == "2025-03"
        assert cf[0]["savings"] == 1500 and cf[0]["savings_rate"] == 0.3
        inc = fin.income_series(c, 2, after)
        assert inc["series"]["dividend"] == [100, 0] and inc["total"] == [5000, 100]


def test_net_worth_carry_forward(settings_env):
    with get_conn() as c:
        ins = "INSERT INTO balances (date, account, kind, balance) VALUES (?,?,?,?)"
        c.execute(ins, ("2025-01-31", "Checking", "asset", 1000))
        c.execute(ins, ("2025-01-31", "Loan", "liability", 400))
        c.execute(ins, ("2025-03-31", "Checking", "asset", 1500))  # Loan has no Feb/Mar snapshot
        c.execute(ins, ("2025-03-31", "Brokerage", "asset", 200))
        pts = fin.net_worth_series(c, 36, date(2025, 3, 31))["points"]
        assert [(p["month"], p["assets"], p["liabilities"], p["net_worth"]) for p in pts] == [
            ("2025-01", 1000, 400, 600), ("2025-02", 1000, 400, 600), ("2025-03", 1700, 400, 1300)]
        assert fin.net_worth_series(c, 2, date(2025, 3, 31))["points"][0]["month"] == "2025-02"
        assert fin.net_worth_now(c) == 1300
        assert fin.net_worth_change_30d(c) == 700  # vs as-of 2025-03-01 snapshot (600)


def test_emergency_fi_and_summary(settings_env):
    with get_conn() as c:
        seed(c)
        ins = "INSERT INTO balances (date, account, kind, balance) VALUES (?,?,?,?)"
        c.execute(ins, ("2025-03-31", "Chase Checking", "asset", 7000))
        c.execute(ins, ("2025-03-31", "Ally HYSA", "asset", 7000))
        c.execute(ins, ("2025-03-31", "Fidelity 401k", "asset", 60000))
        c.execute(ins, ("2025-03-31", "Car Loan", "liability", 10000))
        assert fin.avg_monthly_expenses(c, TODAY, 6) == 3500
        assert fin.emergency_months(c, TODAY) == 4.0
        assert fin.fi_number(c, TODAY) == pytest.approx(3500 * 12 / 0.04)  # 1,050,000
        s = fin.summary(c, TODAY)
        assert s["invested"] == 60000 and s["fi_progress"] == round(60000 / 1_050_000, 4)
        assert s["net_worth"] == 64000 and s["monthly_income"] == 5000 and s["savings_rate"] == 0.3


def test_forecast_hand_computed(settings_env):
    with get_conn() as c:
        c.execute("INSERT INTO balances (date, account, kind, balance) VALUES ('2025-01-31','Checking','asset',10000)")
        fin.save_assumptions(c, {"return_rates": {"base": 0.12}, "monthly_contribution_override": 500})
        f = fin.forecast(c, TODAY)
        base = next(s for s in f["scenarios"] if s["name"] == "base")
        assert f["start_net_worth"] == 10000 and f["monthly_contribution"] == 500
        assert base["series"][1]["value"] == 10600.0  # 10000*1.01 + 500
        assert base["at"]["1y"] == 17609.50  # 10000*1.01^12 + 500*(1.01^12-1)/0.01
        g = 1.01 ** 120
        assert base["at"]["10y"] == pytest.approx(10000 * g + 500 * (g - 1) / 0.01, abs=0.01)


def test_forecast_default_contribution_is_avg_savings(settings_env):
    with get_conn() as c:
        seed(c)
        assert fin.forecast(c, TODAY)["monthly_contribution"] == 1500


def test_assumptions_merge(settings_env):
    with get_conn() as c:
        assert fin.get_assumptions(c)["return_rates"]["base"] == 0.07
        fin.save_assumptions(c, {"return_rates": {"base": 0.09}})
        a = fin.get_assumptions(c)
        assert a["return_rates"] == {"conservative": 0.04, "base": 0.09, "optimistic": 0.10}
        assert a["budget_targets"]["needs"] == 50


def test_business(settings_env):
    with get_conn() as c:
        c.execute("INSERT INTO business (date, revenue, expense) VALUES ('2025-03-31', 500, 100)")
        c.execute("INSERT INTO business (date, revenue, expense) VALUES ('2025-03-15', 100, 50)")
        c.execute("INSERT INTO business (date, revenue, expense) VALUES ('2024-12-31', 900, 0)")
        b = fin.business(c, 2, TODAY)
        assert b["points"][-1] == {"month": "2025-03", "revenue": 600, "expense": 150, "profit": 450}
        assert b["ytd_profit"] == 450


def test_milestones_seed_and_achievement(settings_env):
    with get_conn() as c:
        seed(c)
        c.execute("INSERT INTO balances (date, account, kind, balance) VALUES ('2025-03-31','Checking','asset',60000)")
        ms = fin.list_milestones(c, TODAY)["milestones"]
        by = {m["name"]: m for m in ms}
        assert len(ms) == 5
        nw50 = by["Net worth $50k"]
        assert nw50["achieved_at"] == "2025-04-15" and nw50["progress"] == 1.0
        assert by["Net worth $100k"]["achieved_at"] is None and by["Net worth $100k"]["progress"] == 0.6
        fi = next(m for m in ms if m["kind"] == "fi")
        assert fi["target"] == 1_050_000  # live FI number
        em = next(m for m in ms if m["kind"] == "emergency_fund")
        assert em["target"] == 6 and em["metric"] == "emergency_months"
        # stamp is sticky; deleting all milestones does not re-seed
        later = fin.list_milestones(c, date(2025, 6, 1))["milestones"]
        assert next(m for m in later if m["name"] == "Net worth $50k")["achieved_at"] == "2025-04-15"
        for m in later:
            fin.delete_milestone(c, m["id"])
        assert fin.list_milestones(c, TODAY)["milestones"] == []


def test_milestone_update_resets_achievement(settings_env):
    with get_conn() as c:
        c.execute("INSERT INTO balances (date, account, kind, balance) VALUES ('2025-03-31','Checking','asset',1000)")
        m = fin.create_milestone(c, "Grand", "custom", "net_worth", 500, TODAY)
        assert m["achieved_at"] == "2025-04-15"
        m = fin.update_milestone(c, m["id"], {"target": 5000}, TODAY)
        assert m["achieved_at"] is None and m["progress"] == 0.2
        assert fin.update_milestone(c, 9999, {"name": "x"}) is None
