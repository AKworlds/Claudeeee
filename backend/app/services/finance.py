"""Finance metrics. Pure functions over a sqlite3 connection; `today` is injectable for tests.

Conventions:
- savings (monthly) = total income - (needs + wants); savings_rate = savings / income.
- "Complete" months are those before today's month (falls back to all months if none).
- Series windows end at the latest month that has any data (or today's month on an empty DB).
- Progress values are fractions (1.0 == 100%). All money is rounded to 2 decimals on output.
"""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from copy import deepcopy
from datetime import date, timedelta
from typing import Any, Callable

from ..config import _deep_merge, get_settings

INCOME_TYPES = ("paycheck", "dividend", "interest", "business", "other")
SCENARIOS = ("conservative", "base", "optimistic")
DEFAULT_ASSUMPTIONS: dict[str, Any] = {
    "safe_withdrawal_rate": 0.04,
    "return_rates": {"conservative": 0.04, "base": 0.07, "optimistic": 0.10},
    "monthly_contribution_override": None,
    "emergency_target_months": 6,
    "budget_targets": {"needs": 50, "wants": 30, "savings": 20},
}


def r2(x: float) -> float:
    return round(float(x), 2)


# ---------------------------------------------------------------- months
def month_of(d: date) -> str:
    return d.strftime("%Y-%m")


def month_add(m: str, n: int) -> str:
    y, mo = int(m[:4]), int(m[5:7])
    idx = y * 12 + (mo - 1) + n
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


def month_window(end: str, n: int) -> list[str]:
    return [month_add(end, -i) for i in range(n - 1, -1, -1)]


def end_month(conn: sqlite3.Connection, today: date) -> str:
    """Latest month with any data across the finance tables, else today's month."""
    best = ""
    for t in ("income", "transactions", "balances", "business", "business_payouts"):
        v = conn.execute(f"SELECT MAX(substr(date,1,7)) FROM {t}").fetchone()[0]
        best = max(best, v or "")
    return best or month_of(today)


def series_end(conn: sqlite3.Connection, today: date) -> str:
    """End month for monthly flow charts: excludes the in-progress current month (a partial month
    reads as a sudden income drop / negative savings) unless it is the only data there is."""
    end, cur = end_month(conn, today), month_of(today)
    if end >= cur:
        prev = month_add(cur, -1)
        has_prev = any(conn.execute(f"SELECT 1 FROM {t} WHERE date < ? LIMIT 1", (cur + "-01",)).fetchone()
                       for t in ("income", "transactions", "business", "business_payouts"))
        return prev if has_prev else end
    return end


# ---------------------------------------------------------------- assumptions
def get_assumptions(conn: sqlite3.Connection) -> dict:
    row = conn.execute("SELECT value FROM settings WHERE key='assumptions'").fetchone()
    stored = json.loads(row["value"]) if row else {}
    return _deep_merge(deepcopy(DEFAULT_ASSUMPTIONS), stored)


def save_assumptions(conn: sqlite3.Connection, data: dict) -> dict:
    conn.execute("INSERT INTO settings (key, value) VALUES ('assumptions', ?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(data),))
    return get_assumptions(conn)


# ---------------------------------------------------------------- aggregation
def _blank() -> dict:
    return {"income": {t: 0.0 for t in INCOME_TYPES}, "needs": 0.0, "wants": 0.0, "savings_bucket": 0.0}


def monthly_data(conn: sqlite3.Connection) -> dict[str, dict]:
    """month -> {income: {type: amt}, needs, wants, savings_bucket} for months with income/spend."""
    out: dict[str, dict] = defaultdict(_blank)
    for r in conn.execute("SELECT substr(date,1,7) m, type, SUM(amount) s FROM income GROUP BY m, type"):
        out[r["m"]]["income"][r["type"] if r["type"] in INCOME_TYPES else "other"] += r["s"]
    for r in conn.execute("SELECT substr(date,1,7) m, bucket, SUM(amount) s FROM transactions GROUP BY m, bucket"):
        if r["bucket"] in ("needs", "wants"):
            out[r["m"]][r["bucket"]] += r["s"]
        elif r["bucket"] == "savings":
            out[r["m"]]["savings_bucket"] += r["s"]
        else:
            out[r["m"]]  # touch: month has data
    return dict(out)


def _income(v: dict) -> float:
    return sum(v["income"].values())


def _recent(data: dict[str, dict], today: date, n: int, active: Callable[[dict], bool]) -> list[dict]:
    """Last n complete active months (all active months if none are complete)."""
    months = sorted(m for m, v in data.items() if active(v))
    done = [m for m in months if m < month_of(today)] or months
    return [data[m] for m in done[-n:]]


def avg_monthly_expenses(conn: sqlite3.Connection, today: date, n: int = 6) -> float:
    rows = _recent(monthly_data(conn), today, n, lambda v: v["needs"] + v["wants"] > 0)
    return sum(v["needs"] + v["wants"] for v in rows) / len(rows) if rows else 0.0


def avg_monthly_savings(conn: sqlite3.Connection, today: date, n: int = 6) -> float:
    rows = _recent(monthly_data(conn), today, n, lambda v: _income(v) + v["needs"] + v["wants"] > 0)
    return sum(_income(v) - v["needs"] - v["wants"] for v in rows) / len(rows) if rows else 0.0


def default_month(conn: sqlite3.Connection, today: date) -> str:
    """Latest complete month with income, else latest month with data, else today's month."""
    data, cur = monthly_data(conn), month_of(today)
    with_income = [m for m, v in data.items() if m < cur and _income(v) > 0]
    if with_income:
        return max(with_income)
    return max(data) if data else cur


# ---------------------------------------------------------------- series
def income_series(conn: sqlite3.Connection, months: int = 12, today: date | None = None) -> dict:
    today = today or date.today()
    data, ms = monthly_data(conn), month_window(series_end(conn, today), months)
    series = {t: [r2(data.get(m, _blank())["income"][t]) for m in ms] for t in INCOME_TYPES}
    return {"months": ms, "series": series, "total": [r2(sum(series[t][i] for t in INCOME_TYPES)) for i in range(len(ms))]}


def cashflow(conn: sqlite3.Connection, months: int = 12, today: date | None = None) -> dict:
    today = today or date.today()
    data, pts = monthly_data(conn), []
    for m in month_window(series_end(conn, today), months):
        v = data.get(m, _blank())
        inc = _income(v)
        sav = inc - v["needs"] - v["wants"]
        pts.append({"month": m, "income": r2(inc), "needs": r2(v["needs"]), "wants": r2(v["wants"]),
                    "savings": r2(sav), "savings_rate": round(sav / inc, 4) if inc else 0.0})
    return {"points": pts}


def budget_rule(conn: sqlite3.Connection, month: str | None = None, today: date | None = None) -> dict:
    today = today or date.today()
    month = month or default_month(conn, today)
    v = monthly_data(conn).get(month, _blank())
    net = _income(v)
    targets = get_assumptions(conn)["budget_targets"]
    actual = {"needs": v["needs"], "wants": v["wants"], "savings": net - v["needs"] - v["wants"]}
    buckets = []
    for b in ("needs", "wants", "savings"):
        pct = actual[b] / net * 100 if net else 0.0
        buckets.append({"bucket": b, "actual": r2(actual[b]), "actual_pct": r2(pct),
                        "target_pct": targets[b], "delta_pct": r2(pct - targets[b])})
    return {"month": month, "net_income": r2(net), "buckets": buckets}


# ---------------------------------------------------------------- balances / net worth
def _balances_upto(conn: sqlite3.Connection, as_of: str | None = None) -> dict[str, tuple[str, float]]:
    """account -> (kind, latest balance) with date <= as_of (ISO date or month-end)."""
    q, args = "SELECT account, kind, balance FROM balances", ()
    if as_of:
        q, args = q + " WHERE date <= ?", (as_of,)
    return {r["account"]: (r["kind"], r["balance"]) for r in conn.execute(q + " ORDER BY date, id", args)}


def _nw(latest: dict[str, tuple[str, float]]) -> tuple[float, float]:
    a = sum(b for k, b in latest.values() if k == "asset")
    return a, sum(b for k, b in latest.values() if k == "liability")


def net_worth_series(conn: sqlite3.Connection, months: int = 36, today: date | None = None) -> dict:
    """Latest balance per account per month, carried forward through months without a snapshot."""
    today = today or date.today()
    rows = conn.execute("SELECT date, account, kind, balance FROM balances ORDER BY date, id").fetchall()
    if not rows:
        return {"points": []}
    first, last = rows[0]["date"][:7], max(end_month(conn, today), rows[-1]["date"][:7])
    span = month_window(last, (int(last[:4]) - int(first[:4])) * 12 + int(last[5:7]) - int(first[5:7]) + 1)
    latest: dict[str, tuple[str, float]] = {}
    i, pts = 0, []
    for m in span:
        while i < len(rows) and rows[i]["date"][:7] <= m:
            latest[rows[i]["account"]] = (rows[i]["kind"], rows[i]["balance"])
            i += 1
        a, l = _nw(latest)
        pts.append({"month": m, "assets": r2(a), "liabilities": r2(l), "net_worth": r2(a - l)})
    return {"points": pts[-months:]}


def net_worth_now(conn: sqlite3.Connection) -> float:
    a, l = _nw(_balances_upto(conn))
    return a - l


def net_worth_change_30d(conn: sqlite3.Connection) -> float:
    last = conn.execute("SELECT MAX(date) FROM balances").fetchone()[0]
    if not last:
        return 0.0
    prior = (date.fromisoformat(last) - timedelta(days=30)).isoformat()
    old = _balances_upto(conn, prior)
    if not old:
        return 0.0
    a, l = _nw(old)
    return net_worth_now(conn) - (a - l)


def _matching(latest: dict[str, tuple[str, float]], patterns: list[str]) -> float:
    pats = [p.lower() for p in patterns]
    return sum(b for acct, (k, b) in latest.items() if k == "asset" and any(p in acct.lower() for p in pats))


def invested_total(conn: sqlite3.Connection) -> float:
    return _matching(_balances_upto(conn), get_settings().config["finance"]["invested_accounts"])


def liquid_total(conn: sqlite3.Connection) -> float:
    return _matching(_balances_upto(conn), get_settings().config["finance"]["liquid_accounts"])


def emergency_months(conn: sqlite3.Connection, today: date | None = None) -> float:
    exp = avg_monthly_expenses(conn, today or date.today(), 6)
    return liquid_total(conn) / exp if exp else 0.0


def fi_number(conn: sqlite3.Connection, today: date | None = None) -> float:
    swr = get_assumptions(conn)["safe_withdrawal_rate"]
    return avg_monthly_expenses(conn, today or date.today(), 12) * 12 / swr if swr else 0.0


# ---------------------------------------------------------------- forecast
def forecast(conn: sqlite3.Connection, today: date | None = None) -> dict:
    """NW_{t+1} = NW_t * (1 + r/12) + contribution, for t = 0..120."""
    today = today or date.today()
    a = get_assumptions(conn)
    start = net_worth_now(conn)
    contrib = a["monthly_contribution_override"]
    if contrib is None:
        contrib = avg_monthly_savings(conn, today, 6)
    scenarios = []
    for name in SCENARIOS:
        rate, v, series = a["return_rates"][name], start, []
        for t in range(121):
            series.append({"month_index": t, "value": r2(v)})
            v = v * (1 + rate / 12) + contrib
        scenarios.append({"name": name, "rate": rate,
                          "at": {"1y": series[12]["value"], "5y": series[60]["value"], "10y": series[120]["value"]},
                          "series": series})
    return {"start_net_worth": r2(start), "monthly_contribution": r2(contrib), "scenarios": scenarios}


# ---------------------------------------------------------------- business
def business(conn: sqlite3.Connection, months: int = 12, today: date | None = None) -> dict:
    today = today or date.today()
    agg = {r["m"]: (r["rev"], r["exp"]) for r in conn.execute(
        "SELECT m, SUM(rev) rev, SUM(exp) exp FROM ("
        " SELECT substr(date,1,7) m, revenue rev, expense exp FROM business"
        " UNION ALL SELECT substr(date,1,7), amount, expenses FROM business_payouts) GROUP BY m")}
    pts = []
    for m in month_window(series_end(conn, today), months):
        rev, exp = agg.get(m, (0.0, 0.0))
        pts.append({"month": m, "revenue": r2(rev), "expense": r2(exp), "profit": r2(rev - exp)})
    return {"points": pts, "ytd_profit": business_profit_ytd(conn, today)}


def business_profit_ytd(conn: sqlite3.Connection, today: date | None = None) -> float:
    today = today or date.today()
    y = str(today.year)
    v = conn.execute("SELECT COALESCE(SUM(revenue - expense), 0) FROM business WHERE substr(date,1,4)=?", (y,)).fetchone()[0]
    v += conn.execute("SELECT COALESCE(SUM(amount - expenses), 0) FROM business_payouts WHERE substr(date,1,4)=?",
                      (y,)).fetchone()[0]
    return r2(v)


# ---------------------------------------------------------------- milestones
def _metrics(conn: sqlite3.Connection, today: date) -> dict[str, float]:
    return {"net_worth": net_worth_now(conn), "emergency_months": emergency_months(conn, today),
            "invested": invested_total(conn), "custom": 0.0}


def seed_milestones(conn: sqlite3.Connection, today: date | None = None) -> None:
    """Insert default milestones once (only when the table is empty and never seeded before)."""
    today = today or date.today()
    if conn.execute("SELECT 1 FROM settings WHERE key='milestones_seeded'").fetchone():
        return
    conn.execute("INSERT INTO settings (key, value) VALUES ('milestones_seeded', '1')")
    if conn.execute("SELECT COUNT(*) FROM milestones").fetchone()[0]:
        return
    em = get_assumptions(conn)["emergency_target_months"]
    defaults = [(f"Emergency fund ({em:g} months)", "emergency_fund", "emergency_months", em),
                ("Net worth $50k", "net_worth", "net_worth", 50_000),
                ("Net worth $100k", "net_worth", "net_worth", 100_000),
                ("Net worth $250k", "net_worth", "net_worth", 250_000),
                ("Financial freedom", "fi", "invested", fi_number(conn, today))]
    conn.executemany("INSERT INTO milestones (name, kind, metric, target, created_at) VALUES (?,?,?,?,?)",
                     [(n, k, m, t, today.isoformat()) for n, k, m, t in defaults])


def list_milestones(conn: sqlite3.Connection, today: date | None = None) -> dict:
    """Milestones with live progress; stamps achieved_at the first time progress reaches 1."""
    today = today or date.today()
    seed_milestones(conn, today)
    cur, fi, out = _metrics(conn, today), fi_number(conn, today), []
    for r in conn.execute("SELECT * FROM milestones ORDER BY id").fetchall():
        target = fi if r["kind"] == "fi" else r["target"]
        current = cur.get(r["metric"], 0.0)
        progress = min(current / target, 1.0) if target > 0 else 0.0
        achieved = r["achieved_at"]
        if achieved is None and target > 0 and current >= target:
            achieved = today.isoformat()
            conn.execute("UPDATE milestones SET achieved_at=? WHERE id=?", (achieved, r["id"]))
        out.append({"id": r["id"], "name": r["name"], "kind": r["kind"], "metric": r["metric"],
                    "target": r2(target), "current": r2(current), "progress": round(progress, 4),
                    "achieved_at": achieved})
    return {"milestones": out}


def get_milestone(conn: sqlite3.Connection, mid: int, today: date | None = None) -> dict | None:
    return next((m for m in list_milestones(conn, today)["milestones"] if m["id"] == mid), None)


def create_milestone(conn: sqlite3.Connection, name: str, kind: str, metric: str, target: float,
                     today: date | None = None) -> dict:
    today = today or date.today()
    seed_milestones(conn, today)
    cur = conn.execute("INSERT INTO milestones (name, kind, metric, target, created_at) VALUES (?,?,?,?,?)",
                       (name, kind, metric, target, today.isoformat()))
    return get_milestone(conn, cur.lastrowid, today)  # type: ignore[return-value]


def update_milestone(conn: sqlite3.Connection, mid: int, fields: dict, today: date | None = None) -> dict | None:
    """Partial update; changing target/metric/kind clears achieved_at so it re-stamps if still met."""
    if not conn.execute("SELECT 1 FROM milestones WHERE id=?", (mid,)).fetchone():
        return None
    if fields:
        sets = ", ".join(f"{k}=?" for k in fields)
        reset = ", achieved_at=NULL" if {"target", "metric", "kind"} & fields.keys() else ""
        conn.execute(f"UPDATE milestones SET {sets}{reset} WHERE id=?", (*fields.values(), mid))
    return get_milestone(conn, mid, today)


def delete_milestone(conn: sqlite3.Connection, mid: int) -> bool:
    return conn.execute("DELETE FROM milestones WHERE id=?", (mid,)).rowcount > 0


# ---------------------------------------------------------------- summary
def last_import(conn: sqlite3.Connection) -> dict | None:
    r = conn.execute("SELECT * FROM imports ORDER BY id DESC LIMIT 1").fetchone()
    return dict(r) if r else None


def summary(conn: sqlite3.Connection, today: date | None = None) -> dict:
    today = today or date.today()
    v = monthly_data(conn).get(default_month(conn, today), _blank())
    inc, spend = _income(v), v["needs"] + v["wants"]
    fi, invested = fi_number(conn, today), invested_total(conn)
    return {"net_worth": r2(net_worth_now(conn)), "net_worth_change_30d": r2(net_worth_change_30d(conn)),
            "monthly_income": r2(inc), "monthly_spend": r2(spend),
            "savings_rate": round((inc - spend) / inc, 4) if inc else 0.0,
            "emergency_months": r2(emergency_months(conn, today)), "fi_number": r2(fi),
            "fi_progress": round(invested / fi, 4) if fi else 0.0, "invested": r2(invested),
            "business_profit_ytd": business_profit_ytd(conn, today), "last_import": last_import(conn)}
