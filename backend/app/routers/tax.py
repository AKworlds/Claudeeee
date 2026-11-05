"""/api/tax/* — side-business tax tracker: settings, payouts, summary, estimated-payment log."""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from ..db import get_conn, rows
from ..services import tax as svc

router = APIRouter()


def _iso(v):
    if v in (None, ""):
        return None
    try:
        date.fromisoformat(v)
    except (ValueError, TypeError):
        raise ValueError("must be an ISO date (YYYY-MM-DD)")
    return v


class SettingsIn(BaseModel):
    filing_status: Optional[Literal["single", "married_joint", "head_of_household"]] = None
    w2_income: Optional[float] = Field(None, ge=0)
    state_rate: Optional[float] = Field(None, ge=0, le=0.15)
    buffer_pct: Optional[float] = Field(None, ge=0, le=50)
    use_qbi: Optional[bool] = None


class PayoutIn(BaseModel):
    date: Optional[str] = None
    amount: Optional[float] = Field(None, gt=0)
    expenses: Optional[float] = Field(None, ge=0)
    note: Optional[str] = None
    saved: Optional[float] = Field(None, ge=0)

    _d = field_validator("date", mode="before")(lambda cls, v: _iso(v))


class PaymentIn(BaseModel):
    date: Optional[str] = None
    amount: float = Field(gt=0)
    period: str = Field(min_length=1)
    note: Optional[str] = None

    _d = field_validator("date", mode="before")(lambda cls, v: _iso(v))


def _year(year: Optional[int]) -> int:
    if year is None:
        return date.today().year
    if not 1990 <= year <= 2200:
        raise HTTPException(422, "invalid year")
    return year


# ---- settings ----------------------------------------------------------

@router.get("/settings")
def get_settings_() -> dict:
    with get_conn() as conn:
        return svc.get_settings_tax(conn)


@router.put("/settings")
def put_settings(body: SettingsIn) -> dict:
    data = body.model_dump(exclude_unset=True)  # explicit null w2_income clears it
    data = {k: v for k, v in data.items() if v is not None or k == "w2_income"}
    with get_conn() as conn:
        return svc.save_settings_tax(conn, data)


# ---- payouts -----------------------------------------------------------

@router.get("/payouts")
def list_payouts(year: Optional[int] = None) -> dict:
    with get_conn() as conn:
        return {"payouts": svc.payouts(conn, _year(year))}


@router.post("/payouts", status_code=201)
def create_payout(body: PayoutIn) -> dict:
    if body.amount is None:
        raise HTTPException(422, "amount is required")
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO business_payouts (date, amount, expenses, note, saved, created_at) VALUES (?,?,?,?,?,?)",
            (body.date or date.today().isoformat(), body.amount, body.expenses or 0, body.note or "", body.saved,
             datetime.now(timezone.utc).isoformat(timespec="seconds")))
        return svc.payout(conn, cur.lastrowid)  # type: ignore[return-value]


@router.put("/payouts/{id_}")
def update_payout(id_: int, body: PayoutIn) -> dict:
    data = body.model_dump(exclude_unset=True)
    with get_conn() as conn:
        if not conn.execute("SELECT 1 FROM business_payouts WHERE id=?", (id_,)).fetchone():
            raise HTTPException(404, f"payout {id_} not found")
        for k in ("date", "amount"):
            if k in data and data[k] is None:
                raise HTTPException(422, f"{k} cannot be null")
        if "expenses" in data and data["expenses"] is None:
            data["expenses"] = 0
        if "note" in data and data["note"] is None:
            data["note"] = ""
        if data:
            conn.execute(f"UPDATE business_payouts SET {', '.join(f'{k}=?' for k in data)} WHERE id=?",
                         [*data.values(), id_])
        return svc.payout(conn, id_)  # type: ignore[return-value]


@router.delete("/payouts/{id_}")
def delete_payout(id_: int) -> dict:
    with get_conn() as conn:
        if not conn.execute("DELETE FROM business_payouts WHERE id=?", (id_,)).rowcount:
            raise HTTPException(404, f"payout {id_} not found")
        return {"ok": True}


# ---- summary -----------------------------------------------------------

@router.get("/summary")
def summary(year: Optional[int] = None) -> dict:
    with get_conn() as conn:
        return svc.summary(conn, _year(year), date.today())


# ---- payments ----------------------------------------------------------

@router.get("/payments")
def list_payments() -> dict:
    with get_conn() as conn:
        return {"payments": rows(conn.execute("SELECT * FROM tax_payments ORDER BY date DESC, id DESC"))}


@router.post("/payments", status_code=201)
def create_payment(body: PaymentIn) -> dict:
    with get_conn() as conn:
        cur = conn.execute("INSERT INTO tax_payments (date, amount, period, note) VALUES (?,?,?,?)",
                           (body.date or date.today().isoformat(), body.amount, body.period, body.note or ""))
        return dict(conn.execute("SELECT * FROM tax_payments WHERE id=?", (cur.lastrowid,)).fetchone())


@router.delete("/payments/{id_}")
def delete_payment(id_: int) -> dict:
    with get_conn() as conn:
        if not conn.execute("DELETE FROM tax_payments WHERE id=?", (id_,)).rowcount:
            raise HTTPException(404, f"payment {id_} not found")
        return {"ok": True}
