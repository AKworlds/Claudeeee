"""Side-business tax estimate. Pure functions over a sqlite3 connection; `today` is injectable.

Estimates only (not tax advice). Math is specified in PLAN.md ("Side-business tax tracker (v2)").
Money is rounded to cents on output; per-payout set-asides are rounded UP to whole dollars.
"""
from __future__ import annotations

import json
import math
import sqlite3
from datetime import date
from typing import Any

from ..config import get_settings

SETTINGS_KEY = "tax_settings"
DEFAULT_SETTINGS: dict[str, Any] = {
    "federal_bracket": 0.22, "state_rate": 0.0, "buffer_pct": 0.0, "use_qbi": True,
}
FEDERAL_BRACKETS = (0.10, 0.12, 0.22, 0.24, 0.32, 0.35, 0.37)


def r2(x: float) -> float:
    return round(float(x), 2)


def tables() -> dict:
    return get_settings().config["tax"]


# ---------------------------------------------------------------- settings
def get_settings_tax(conn: sqlite3.Connection) -> dict:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (SETTINGS_KEY,)).fetchone()
    stored = json.loads(row["value"]) if row else {}
    return {**DEFAULT_SETTINGS, **{k: v for k, v in stored.items() if k in DEFAULT_SETTINGS}}


def save_settings_tax(conn: sqlite3.Connection, data: dict) -> dict:
    merged = {**get_settings_tax(conn), **{k: v for k, v in data.items() if k in DEFAULT_SETTINGS}}
    conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (SETTINGS_KEY, json.dumps(merged)))
    return merged


# ---------------------------------------------------------------- math
def total_tax(P: float, settings: dict, tbl: dict | None = None) -> dict:
    """Tax owed on cumulative business profit P for the year (P <= 0 => zero). Unrounded components.

    Business income only: other income is taxed elsewhere (already withheld) and is not modelled.
    SE tax has no Social Security wage-base cap and no additional Medicare tax (a small overestimate
    or underestimate is possible at high incomes). Federal income tax uses the single chosen bracket rate.
    """
    tbl = tbl or tables()
    P = max(0.0, float(P))
    se = (tbl["se_ss_rate"] + tbl["se_medicare_rate"]) * P * tbl["se_net_factor"]
    qbi = tbl["qbi_rate"] * max(0.0, P - se / 2) if settings.get("use_qbi", True) else 0.0
    federal = float(settings.get("federal_bracket") or DEFAULT_SETTINGS["federal_bracket"]) * max(0.0, P - se / 2 - qbi)
    state = (settings.get("state_rate") or 0.0) * P
    return {"se": se, "federal": federal, "state": state, "total": se + federal + state}


def _set_aside(prev_total: float, new_total: float, buffer_pct: float) -> int:
    delta = max(0.0, (new_total - prev_total) * (1 + (buffer_pct or 0) / 100))
    return int(math.ceil(round(delta, 2)))


# ---------------------------------------------------------------- data
def _year_bounds(year: int) -> tuple[str, str]:
    return f"{year}-01-01", f"{year}-12-31"


def entries(conn: sqlite3.Connection, year: int) -> list[dict]:
    """Chronological payouts (app) + workbook business rows (pseudo-payouts) within the year."""
    lo, hi = _year_bounds(year)
    out = []
    for r in conn.execute("SELECT * FROM business_payouts WHERE date BETWEEN ? AND ?", (lo, hi)):
        out.append({"id": r["id"], "date": r["date"], "amount": r["amount"], "expenses": r["expenses"],
                    "profit": r["amount"] - r["expenses"], "saved": r["saved"], "note": r["note"],
                    "source": "app"})
    for r in conn.execute("SELECT * FROM business WHERE date BETWEEN ? AND ?", (lo, hi)):
        out.append({"id": None, "date": r["date"][:10], "amount": r["revenue"], "expenses": r["expense"],
                    "profit": r["revenue"] - r["expense"], "saved": None, "note": r["description"],
                    "source": "workbook"})
    out.sort(key=lambda e: (e["date"], e["source"] != "workbook", e["id"] or 0))
    return out


def sequence(conn: sqlite3.Connection, year: int, settings: dict | None = None, tbl: dict | None = None) -> list[dict]:
    """Entries with computed `set_aside`, accounting for cumulative profit (bracket crossing)."""
    settings = settings or get_settings_tax(conn)
    tbl = tbl or tables()
    cum, prev = 0.0, 0.0
    out = []
    for e in entries(conn, year):
        cum += e["profit"]
        tot = total_tax(max(0.0, cum), settings, tbl)["total"]
        out.append({**e, "set_aside": _set_aside(prev, tot, settings["buffer_pct"])})
        prev = tot
    return out


def _public(e: dict) -> dict:
    p = e["profit"]
    return {"id": e["id"], "date": e["date"], "amount": r2(e["amount"]), "expenses": r2(e["expenses"]),
            "profit": r2(p), "set_aside": e["set_aside"], "saved": e["saved"], "note": e["note"]}


def payouts(conn: sqlite3.Connection, year: int | None = None, today: date | None = None) -> list[dict]:
    year = year or (today or date.today()).year
    app = [_public(e) for e in sequence(conn, year) if e["source"] == "app"]
    return sorted(app, key=lambda p: (p["date"], p["id"]), reverse=True)


def payout(conn: sqlite3.Connection, pid: int) -> dict | None:
    """One app payout with set_aside and rate (set_aside / profit)."""
    row = conn.execute("SELECT date FROM business_payouts WHERE id=?", (pid,)).fetchone()
    if not row:
        return None
    for e in sequence(conn, int(row["date"][:4])):
        if e["source"] == "app" and e["id"] == pid:
            out = _public(e)
            out["rate"] = round(e["set_aside"] / e["profit"], 4) if e["profit"] > 0 else 0.0
            return out
    return None


# ---------------------------------------------------------------- quarters
def quarters(conn: sqlite3.Connection, year: int, seq: list[dict], today: date) -> list[dict]:
    out = []
    for i, (s, e, d, nxt) in enumerate(tables()["quarters"], 1):
        label = f"{year}-Q{i}"
        start, end = f"{year}-{s}", f"{year}-{e}"
        due = f"{year + (1 if nxt else 0)}-{d}"
        amount = sum(x["set_aside"] for x in seq if start <= x["date"] <= end)
        paid = conn.execute("SELECT COALESCE(SUM(amount),0) FROM tax_payments WHERE period=?", (label,)).fetchone()[0]
        days = (date.fromisoformat(due) - today).days
        if amount > 0 and paid >= amount:
            status = "paid"
        elif amount <= 0 and paid <= 0:
            status = "upcoming" if days >= 0 else "nothing_due"  # nothing owed for a past period
        elif days < 0:
            status = "past_due"
        elif days <= 30:
            status = "due_soon"
        else:
            status = "upcoming"
        out.append({"label": label, "start": start, "end": end, "due": due, "amount": amount,
                    "paid": r2(paid), "status": status})
    return out


# ---------------------------------------------------------------- summary
def summary(conn: sqlite3.Connection, year: int | None = None, today: date | None = None) -> dict:
    today = today or date.today()
    year = year or today.year
    settings, tbl = get_settings_tax(conn), tables()
    seq = sequence(conn, year, settings, tbl)
    profit = sum(e["profit"] for e in seq)
    P = max(0.0, profit)
    tax = total_tax(P, settings, tbl)
    set_aside_total = sum(e["set_aside"] for e in seq)
    saved_total = sum(e["saved"] or 0 for e in seq)

    t0 = tax["total"]
    # exact (unrounded) tax on the next $100; per-payout set-asides round up, the rate must not
    marginal = round((total_tax(P + 100, settings, tbl)["total"] - t0) * (1 + (settings["buffer_pct"] or 0) / 100) / 100, 4)
    months = today.month if year == today.year else (12 if year < today.year else 1)
    annual = max(0.0, profit) / months * 12 if months else 0.0
    annual_tax = total_tax(annual, settings, tbl)["total"]

    notes = ["Estimate only, not tax advice. Rates come from config `tax:` (update each January). "
             "Self-employment tax ignores the Social Security wage-base cap and additional Medicare tax."]
    notes.append(f"Federal income tax uses your {settings['federal_bracket']:.0%} bracket; it should be the bracket "
                 "your total taxable income falls in.")
    if tax["total"] < 1000:
        notes.append("Total estimated tax for the year is under $1,000, so quarterly estimated payments are "
                     "generally not required.")
    if 0 < P * tbl["se_net_factor"] < 400:
        notes.append("Net self-employment earnings under $400 for the year owe no SE tax; it is accrued here "
                     "in case you cross $400.")
    if not settings["state_rate"]:
        notes.append("State income tax is not included (state rate is 0).")

    return {
        "year": year, "profit_ytd": r2(profit),
        "tax": {k: r2(v) for k, v in tax.items()},
        "effective_rate": round(tax["total"] / P, 4) if P > 0 else 0.0,
        "marginal_rate": marginal,
        "set_aside_total": set_aside_total, "saved_total": r2(saved_total),
        "shortfall": r2(max(0.0, set_aside_total - saved_total)),
        "quarters": quarters(conn, year, seq, today),
        "projection": {"annual_profit": r2(annual), "annual_tax": r2(annual_tax)},
        "counts": {"app": sum(e["source"] == "app" for e in seq), "workbook": sum(e["source"] == "workbook" for e in seq)},
        "notes": notes,
    }
