from datetime import date

from app.services import tax
from app.config import get_settings

S = {"filing_status": "single", "w2_income": 60000, "state_rate": 0.0, "buffer_pct": 0.0, "use_qbi": True}


def add(c, d, amount, expenses=0.0):
    c.execute("INSERT INTO business_payouts (date, amount, expenses, note, created_at) VALUES (?,?,?,?,?)",
              (d, amount, expenses, "", "x"))


def setup(c, **kw):
    tax.save_settings_tax(c, {**S, **kw})


def test_hand_verified_single_60k(settings_env):
    from app.db import get_conn
    tbl = get_settings().config["tax"]
    t = tax.total_tax(1000, S, tbl)
    assert round(t["se"], 2) == 141.30            # 1000*.9235*.153
    # SE/2=70.64775; QBI=.2*(1000-70.64775)=185.87045; TI before QBI = 60000+1000-70.64775-16100 = 44829.35225
    # taxable after QBI = 44643.4818; base taxable 43900 -> extra 743.4818 all in 12% = 89.2178
    assert round(t["federal"], 2) == 89.22
    assert round(t["total"], 2) == 230.51
    with get_conn() as c:
        setup(c)
        add(c, "2026-03-01", 1000)
        p = tax.payouts(c, 2026)[0]
        assert p["set_aside"] == 231              # ceil(230.51)
        assert tax.payout(c, p["id"])["rate"] == 0.231


def test_bracket_crossing(settings_env):
    from app.db import get_conn
    with get_conn() as c:
        setup(c, w2_income=120000)               # baseline taxable 103,900; 22% bracket ends at 105,700
        for i in range(1, 8):
            add(c, f"2026-0{i}-01", 2000)
        sa = [p["set_aside"] for p in reversed(tax.payouts(c, 2026))]
        assert sa == sorted(sa) and sa[-1] > sa[0]
        assert sa[0] == 611 or 600 <= sa[0] <= 625  # ~22% fed * .8*.929 + 14.13% SE
        assert sa[-1] >= sa[0] + 20                 # 24% bracket


def test_ss_wage_base(settings_env):
    tbl = get_settings().config["tax"]
    hi = {**S, "w2_income": 190000}
    assert round(tax.total_tax(1000, hi, tbl)["se"], 2) == 26.78      # 923.5*.029 only
    part = {**S, "w2_income": 184000}
    assert abs(tax.total_tax(10000, part, tbl)["se"] - 329.815) < 1e-6  # 500*.124 + 9235*.029


def test_needs_setup_fallback(settings_env):
    from app.db import get_conn
    with get_conn() as c:
        add(c, "2026-03-01", 1000)
        assert tax.payouts(c, 2026)[0]["set_aside"] == 300
        s = tax.summary(c, 2026, date(2026, 6, 1))
        assert s["needs_setup"] and s["tax"]["total"] == 300
        assert any("30%" in n for n in s["notes"])


def test_buffer(settings_env):
    from app.db import get_conn
    with get_conn() as c:
        setup(c, buffer_pct=10)
        add(c, "2026-03-01", 1000)
        assert tax.payouts(c, 2026)[0]["set_aside"] == 254   # ceil(230.5133*1.1=253.56)


def test_loss_payout(settings_env):
    from app.db import get_conn
    with get_conn() as c:
        setup(c)
        add(c, "2026-02-01", 100, 500)           # profit -400
        add(c, "2026-03-01", 1000)               # cumulative 600
        ps = {p["date"]: p for p in tax.payouts(c, 2026)}
        assert ps["2026-02-01"]["set_aside"] == 0 and ps["2026-02-01"]["profit"] == -400
        assert 0 < ps["2026-03-01"]["set_aside"] < 231   # taxed on 600 only
        s = tax.summary(c, 2026, date(2026, 6, 1))
        assert s["profit_ytd"] == 600


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
        assert tax.payouts(c, 2026)[0]["set_aside"] in (230, 231, 232)  # marginal on top of the workbook $1000
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
        assert q["2026-Q4"]["amount"] > 231 and q["2026-Q2"]["amount"] == 0
        assert q["2026-Q1"]["amount"] == 231
        assert tax.summary(c, 2026, date(2026, 12, 20))["quarters"][3]["status"] == "due_soon"
        c.execute("INSERT INTO tax_payments (date, amount, period) VALUES ('2026-04-01', 231, '2026-Q1')")
        q = tax.summary(c, 2026, date(2026, 11, 5))["quarters"]
        assert q[0]["status"] == "paid" and q[0]["paid"] == 231


def test_projection_and_rates(settings_env):
    from app.db import get_conn
    with get_conn() as c:
        setup(c)
        add(c, "2026-03-01", 6000)
        s = tax.summary(c, 2026, date(2026, 6, 15))
        assert s["projection"]["annual_profit"] == 12000
        assert s["marginal_rate"] > 0.2 and 0 < s["effective_rate"] < 0.4
