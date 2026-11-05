"""/api/finance/* endpoints. Sync handlers (run in FastAPI's threadpool)."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from datetime import date as _date
from typing import Literal, Optional

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel, Field, field_validator, model_validator

from ..config import get_settings
from ..db import get_conn, rows
from ..services import finance as fin
from ..services.errors import SyncError
from ..services.excel_import import import_workbook

router = APIRouter()

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MAX_UPLOAD = 20 * 1024 * 1024
Kind = Literal["emergency_fund", "net_worth", "investing", "fi", "custom"]
Metric = Literal["net_worth", "emergency_months", "invested", "custom"]
Months = Query(12, ge=1, le=120)


class MilestoneIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    kind: Kind = "custom"
    metric: Metric = "net_worth"
    target: float = Field(ge=0)


class MilestoneUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    kind: Optional[Kind] = None
    metric: Optional[Metric] = None
    target: Optional[float] = Field(default=None, ge=0)


IncomeType = Literal["paycheck", "dividend", "interest", "business", "other"]


def _iso_date(v: Optional[str]) -> Optional[str]:
    if v is None:
        return v
    try:
        return _date.fromisoformat(v).isoformat()
    except ValueError:
        raise ValueError("date must be YYYY-MM-DD")


class IncomeEntryIn(BaseModel):
    date: Optional[str] = None
    source: str = Field(min_length=1, max_length=80)
    type: IncomeType = "other"
    amount: float = Field(gt=0, le=10_000_000)
    note: str = Field(default="", max_length=500)

    @field_validator("source", "note", mode="before")
    @classmethod
    def _strip(cls, v):
        return v.strip() if isinstance(v, str) else v

    @field_validator("note", mode="before")
    @classmethod
    def _note_none(cls, v):
        return "" if v is None else v

    @field_validator("date")
    @classmethod
    def _date_ok(cls, v):
        return _iso_date(v)


class IncomeEntryUpdate(BaseModel):
    date: Optional[str] = None
    source: Optional[str] = Field(default=None, min_length=1, max_length=80)
    type: Optional[IncomeType] = None
    amount: Optional[float] = Field(default=None, gt=0, le=10_000_000)
    note: Optional[str] = Field(default=None, max_length=500)

    @field_validator("source", "note", mode="before")
    @classmethod
    def _strip(cls, v):
        return v.strip() if isinstance(v, str) else v

    @field_validator("date")
    @classmethod
    def _date_ok(cls, v):
        return _iso_date(v)


Rate = Field(ge=-0.5, le=0.5)


class ReturnRates(BaseModel):
    conservative: float = Rate
    base: float = Rate
    optimistic: float = Rate


class BudgetTargets(BaseModel):
    needs: float = Field(ge=0, le=100)
    wants: float = Field(ge=0, le=100)
    savings: float = Field(ge=0, le=100)

    @model_validator(mode="after")
    def _sum_100(self):
        if abs(self.needs + self.wants + self.savings - 100) > 0.01:
            raise ValueError("budget targets must sum to 100")
        return self


class AssumptionsIn(BaseModel):
    safe_withdrawal_rate: float = Field(ge=0.01, le=0.1)
    return_rates: ReturnRates
    monthly_contribution_override: Optional[float] = None
    emergency_target_months: float = Field(gt=0, le=60)
    budget_targets: BudgetTargets

    @field_validator("monthly_contribution_override")
    @classmethod
    def _finite(cls, v):
        if v is not None and abs(v) > 1e9:
            raise ValueError("unreasonable contribution")
        return v


@router.get("/summary")
def get_summary() -> dict:
    with get_conn() as conn:
        return fin.summary(conn)


@router.get("/income")
def get_income(months: int = Months) -> dict:
    with get_conn() as conn:
        return fin.income_series(conn, months)


@router.get("/income-entries")
def list_income_entries(limit: int = Query(100, ge=1, le=1000)) -> dict:
    with get_conn() as conn:
        return {"entries": fin.list_income_entries(conn, limit)}


@router.post("/income-entries", status_code=201)
def post_income_entry(body: IncomeEntryIn) -> dict:
    with get_conn() as conn:
        return fin.create_income_entry(conn, body.date or _date.today().isoformat(), body.source, body.type,
                                       body.amount, body.note)


@router.put("/income-entries/{eid}")
def put_income_entry(eid: int, body: IncomeEntryUpdate) -> dict:
    fields = body.model_dump(exclude_none=True)
    if "date" in body.model_fields_set and body.date is None:
        raise HTTPException(422, "date cannot be null")
    with get_conn() as conn:
        out = fin.update_income_entry(conn, eid, fields)
    if out is None:
        raise HTTPException(404, "income entry not found")
    return out


@router.delete("/income-entries/{eid}")
def del_income_entry(eid: int) -> dict:
    with get_conn() as conn:
        if not fin.delete_income_entry(conn, eid):
            raise HTTPException(404, "income entry not found")
    return {"ok": True}


@router.get("/income-sources")
def get_income_sources() -> dict:
    with get_conn() as conn:
        return {"sources": fin.income_sources(conn)}


@router.get("/net-worth")
def get_net_worth(months: int = Query(36, ge=1, le=240)) -> dict:
    with get_conn() as conn:
        return fin.net_worth_series(conn, months)


@router.get("/cashflow")
def get_cashflow(months: int = Months) -> dict:
    with get_conn() as conn:
        return fin.cashflow(conn, months)


@router.get("/budget-rule")
def get_budget_rule(month: Optional[str] = Query(None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$")) -> dict:
    with get_conn() as conn:
        return fin.budget_rule(conn, month)


@router.get("/business")
def get_business(months: int = Months) -> dict:
    with get_conn() as conn:
        return fin.business(conn, months)


@router.get("/milestones")
def get_milestones() -> dict:
    with get_conn() as conn:
        return fin.list_milestones(conn)


@router.post("/milestones")
def post_milestone(body: MilestoneIn) -> dict:
    with get_conn() as conn:
        return fin.create_milestone(conn, body.name, body.kind, body.metric, body.target)


@router.put("/milestones/{mid}")
def put_milestone(mid: int, body: MilestoneUpdate) -> dict:
    with get_conn() as conn:
        out = fin.update_milestone(conn, mid, body.model_dump(exclude_none=True))
    if out is None:
        raise HTTPException(404, "milestone not found")
    return out


@router.delete("/milestones/{mid}")
def del_milestone(mid: int) -> dict:
    with get_conn() as conn:
        if not fin.delete_milestone(conn, mid):
            raise HTTPException(404, "milestone not found")
    return {"ok": True}


@router.get("/forecast")
def get_forecast() -> dict:
    with get_conn() as conn:
        return fin.forecast(conn)


@router.get("/assumptions")
def get_assumptions() -> dict:
    with get_conn() as conn:
        return fin.get_assumptions(conn)


@router.put("/assumptions")
def put_assumptions(body: AssumptionsIn) -> dict:
    with get_conn() as conn:
        return fin.save_assumptions(conn, body.model_dump())


@router.post("/import")
def upload_import(file: UploadFile = File(...)) -> dict:
    if not (file.filename or "").lower().endswith(".xlsx"):
        raise HTTPException(400, "only .xlsx files are accepted")
    dest = get_settings().workbook_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=".upload-", suffix=".xlsx")
    try:
        size, head = 0, b""
        with os.fdopen(fd, "wb") as out:
            while chunk := file.file.read(1 << 20):
                size += len(chunk)
                if size > MAX_UPLOAD:
                    raise HTTPException(413, "file too large (max 20 MB)")
                head = head or chunk[:4]
                out.write(chunk)
        if not head.startswith(b"PK"):
            raise HTTPException(400, "not a valid .xlsx file")
        # Validate by importing the upload itself; only a successful import replaces the stored workbook.
        result = import_workbook(Path(tmp), force=True, name=dest.name)
        if result["status"] == "ok":
            os.replace(tmp, dest)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return result


@router.get("/imports")
def list_imports(limit: int = Query(50, ge=1, le=500)) -> dict:
    with get_conn() as conn:
        return {"imports": rows(conn.execute("SELECT * FROM imports ORDER BY id DESC LIMIT ?", (limit,)))}


@router.get("/drive")
def drive_status() -> dict:
    from ..services import drive_sync
    with get_conn() as conn:
        def setting(k):
            r = conn.execute("SELECT value FROM settings WHERE key=?", (k,)).fetchone()
            return json.loads(r["value"]) if r else None
        return {"configured": drive_sync.configured(), "file_id": get_settings().config["workbook"].get("drive_file_id"),
                "last_sync": setting("last_sync_drive"), "last_modified": setting("drive_last_modified"),
                "service_account_email": drive_sync.service_account_email()}


@router.post("/drive/sync")
def drive_sync_now() -> dict:
    from ..services import drive_sync
    if not drive_sync.configured():
        raise HTTPException(400, "Drive sync not configured: set GOOGLE_SERVICE_ACCOUNT_FILE and workbook.drive_file_id")
    try:
        return drive_sync.sync_drive()
    except SyncError as e:
        raise HTTPException(502, str(e))


@router.get("/template")
def download_template() -> FileResponse:
    from ..services.template import build_template
    fd, tmp = tempfile.mkstemp(suffix=".xlsx")
    os.close(fd)
    build_template(Path(tmp))
    return FileResponse(tmp, filename="budget_template.xlsx", media_type=XLSX_MIME,
                        background=BackgroundTask(os.unlink, tmp))
