from datetime import date

from app.services import tax
from app.config import get_settings

S = {"filing_status": "single", "state_rate": 0.0, "buffer_pct": 0.0, "use_qbi": True}


def add(c, d, amount, expenses=0.0):
    c.execute("INSERT INTO business_payouts (date, amount, expenses, note, created_at) VALUES (?,?,?,?,?)",
              (d, amount, expenses, "", "x"))


def setup(c, **kw):
    tax.save_settings_tax(c, {**S, **kw})


def test_hand_verified_22(settings_env):
    from app.db import get_conn
    tbl = get_settings().config["tax"]
    t = tax.total_tax(1000, S, tbl)
    assert abs(t["se"] - 141.2955) < 1e-9             # 1000*.9235*.153
    # QBI = 185.87045; taxable = 743.4818; federal = 10% = 74.34818
    assert abs(t["federal"] - 74.34818) < 1e-4
    assert round(t["total"], 4) == 215.6437
    with get_conn() as c:
        setup(c)
        add(c, "2026-03-01", 1000)
        p = tax.payouts(c, 2026)[0]
        assert p["set_aside"] == 216
        assert tax.payout(c, p["id"])["rate"] == 0.216


def test_breakdown_components(settings_env):
    from app.db import get_conn
    tbl = get_settings().config["tax"]
    t = tax.total_tax(1000, S, tbl)
    assert abs(t["social_security"] - 114.514) < 1e-9 and abs(t["medicare"] - 26.7815) < 1e-9
    assert abs(t["se"] - (t["social_security"] + t["medicare"])) < 1e-12
    with get_conn() as c:
        setup(c)
        add(c, "2026-03-01", 1000)
        p = tax.payouts(c, 2026)[0]
        assert p["breakdown"] == {"social_security": 114.51, "medicare": 26.78, "federal": 74.35, "state": 0,
                                  "federal_rate": 0.10}
        assert p["set_aside"] == 216
    with get_conn() as c:
        c.execute("DELETE FROM business_payouts")
        setup(c, state_rate=0.05)
        add(c, "2026-03-01", 1000)
        p = tax.payouts(c, 2026)[0]
        assert p["breakdown"]["state"] == 50.0 and p["set_aside"] == 266


def test_progressive_brackets(settings_env):
    from app.db import get_conn
    tbl = get_settings().config["tax"]
    P = 20000.0
    se = 0.153 * 0.9235 * P
    base = (P - se / 2) * 0.8
    expect = 0.10 * 12400 + 0.12 * (base - 12400)
    assert base > 12400
    assert abs(tax.total_tax(P, S, tbl)["federal"] - expect) < 1e-6
    mj = tax.total_tax(P, {**S, "filing_status": "married_joint"}, tbl)["federal"]
    assert abs(mj - 0.10 * base) < 1e-6          # still inside the 24,800 bracket
    hoh = tax.total_tax(P, {**S, "filing_status": "head_of_household"}, tbl)["federal"]
    assert base < 17700 and abs(hoh - 0.10 * base) < 1e-6
    with get_conn() as c:
        setup(c)
        add(c, "2026-01-01", 10000)
        add(c, "2026-02-01", 10000)
        ps = {p["date"]: p for p in tax.payouts(c, 2026)}
        a, b = ps["2026-01-01"], ps["2026-02-01"]
        assert a["breakdown"]["federal_rate"] == 0.10 and b["breakdown"]["federal_rate"] == 0.12
        assert b["set_aside"] > a["set_aside"]
        s = tax.summary(c, 2026, date(2026, 6, 1))
        assert s["rates"]["federal_marginal"] == 0.12 and "federal_bracket" not in s["rates"]
        assert abs(s["rates"]["federal_effective"] - round(expect / P, 4)) < 1e-4
    with get_conn() as c:
        c.execute("DELETE FROM business_payouts")
        setup(c, filing_status="married_joint")
        add(c, "2026-01-01", 10000)
        add(c, "2026-02-01", 10000)
        ps = {p["date"]: p for p in tax.payouts(c, 2026)}
        assert ps["2026-02-01"]["breakdown"]["federal_rate"] == 0.10


def test_no_qbi(settings_env):
    tbl = get_settings().config["tax"]
    t = tax.total_tax(1000, {**S, "use_qbi": False}, tbl)
    assert abs(t["federal"] - 0.10 * (1000 - 70.64775)) < 1e-6


def test_buffer(settings_env):
    from app.db import get_conn
    with get_conn() as c:
        setup(c, buffer_pct=10)
        add(c, "2026-03-01", 1000)
        assert tax.payouts(c, 2026)[0]["set_aside"] == 238   # ceil(215.64*1.1=237.20)


def test_state_rate(settings_env):
    from app.db import get_conn
    with get_conn() as c:
        setup(c, state_rate=0.05)
        add(c, "2026-03-01", 1000)
        assert tax.payouts(c, 2026)[0]["set_aside"] == 266   # ceil(215.64+50)
        s = tax.summary(c, 2026, date(2026, 6, 1))
        assert s["tax"]["state"] == 50
        assert not any("State income tax is not included" in n for n in s["notes"])


def test_loss_payout(settings_env):
    from app.db import get_conn
    with get_conn() as c:
        setup(c)
        add(c, "2026-02-01", 100, 500)           # profit -400
        add(c, "2026-03-01", 1000)               # cumulative 600
        ps = {p["date"]: p for p in tax.payouts(c, 2026)}
        assert ps["2026-02-01"]["set_aside"] == 0 and ps["2026-02-01"]["profit"] == -400
        assert 0 < ps["2026-03-01"]["set_aside"] < 216   # taxed on 600 only
        assert tax.summary(c, 2026, date(2026, 6, 1))["profit_ytd"] == 600


def test_year_boundary_and_workbook(settings_env):
    from app.db import get_conn
    with get_conn() as c:
        setup(c)
        add(c, "2025-12-31", 5000)
        add(c, "2026-01-02", 1000)
        c.execute("INSERT INTO business (date, description, revenue, expense) VALUES ('2026-01-01','wb',1000,0)")
        s = tax.summary(c, 2026, date(2026, 6, 1))
        assert s["profit_ytd"] == 2000 and s["counts"] == {"app": 1, "workbook": 1}
        assert len(tax.payouts(c, 2026)) == 1                 # workbook rows are not listed
        assert tax.payouts(c, 2026)[0]["set_aside"] == 216    # still inside the 10% bracket: same marginal as the first $1000
        assert round(s["tax"]["total"]) == round(tax.total_tax(2000, S, get_settings().config["tax"])["total"])


def test_quarters(settings_env):
    from app.db import get_conn
    with get_conn() as c:
        setup(c)
        add(c, "2026-01-10", 1000)
        add(c, "2026-10-01", 1000)
        add(c, "2026-12-31", 1000)
        s = tax.summary(c, 2026, date(2026, 11, 5))
        q = {x["label"]: x for x in s["quarters"]}
        assert q["2026-Q1"]["due"] == "2026-04-15" and q["2026-Q1"]["status"] == "past_due"
        assert q["2026-Q4"]["due"] == "2027-01-15" and q["2026-Q4"]["status"] == "upcoming"
        assert q["2026-Q4"]["amount"] == 432 and q["2026-Q2"]["amount"] == 0
        assert q["2026-Q1"]["amount"] == 216
        assert tax.summary(c, 2026, date(2026, 12, 20))["quarters"][3]["status"] == "due_soon"
        c.execute("INSERT INTO tax_payments (date, amount, period) VALUES ('2026-04-01', 216, '2026-Q1')")
        q = tax.summary(c, 2026, date(2026, 11, 5))["quarters"]
        assert q[0]["status"] == "paid" and q[0]["paid"] == 216


def test_projection_rates_and_notes(settings_env):
    from app.db import get_conn
    with get_conn() as c:
        setup(c)
        add(c, "2026-03-01", 6000)
        s = tax.summary(c, 2026, date(2026, 6, 15))
        assert s["projection"]["annual_profit"] == 12000
        assert abs(s["marginal_rate"] - 0.2156) < 1e-3 and 0 < s["effective_rate"] < 0.4
        assert "needs_setup" not in s
        text = " ".join(s["notes"]).lower()
        assert "not tax advice" in text and "standard deduction not applied" in text
        for word in ("w-2", "w2", "wages", "w-4", "employer", "tesla"):
            assert word not in text
    with get_conn() as c:
        c.execute("DELETE FROM business_payouts")
        add(c, "2026-03-01", 100)
        assert any("under $1,000" in n for n in tax.summary(c, 2026, date(2026, 6, 15))["notes"])


def test_legacy_keys_dropped(settings_env):
    import json
    from app.db import get_conn
    with get_conn() as c:
        c.execute("INSERT INTO settings (key, value) VALUES (?, ?)",
                  (tax.SETTINGS_KEY, json.dumps({"w2_income": 90000, "federal_bracket": 0.32, "filing_status": "married_joint", "buffer_pct": 5})))
        s = tax.get_settings_tax(c)
        assert "w2_income" not in s and "federal_bracket" not in s and s["buffer_pct"] == 5
        assert s["filing_status"] == "married_joint"
        saved = tax.save_settings_tax(c, {"w2_income": 1, "federal_bracket": 0.1, "state_rate": 0.01})
        assert set(saved) == set(tax.DEFAULT_SETTINGS) and saved["state_rate"] == 0.01
        raw = json.loads(c.execute("SELECT value FROM settings WHERE key=?", (tax.SETTINGS_KEY,)).fetchone()["value"])
        assert "w2_income" not in raw and "federal_bracket" not in raw
