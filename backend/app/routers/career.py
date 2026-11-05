"""/api/career/* endpoints: applications, certifications, projects, funnel, summary, sync."""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

from ..config import get_settings
from ..db import get_conn, rows
from ..services.errors import SyncError

router = APIRouter()

AppStatus = Literal["wishlist", "applied", "interview", "offer", "rejected", "withdrawn"]
CertStatus = Literal["planned", "in_progress", "earned", "expired"]
ProjStatus = Literal["idea", "building", "done"]
STAGES = ["wishlist", "applied", "interview", "offer", "rejected", "withdrawn"]


def _check_date(v: Optional[str]) -> Optional[str]:
    if v in (None, ""):
        return None
    try:
        date.fromisoformat(v)
    except ValueError:
        raise ValueError("must be an ISO date (YYYY-MM-DD)")
    return v


class _Dated(BaseModel):
    @field_validator("applied_on", "earned_on", "expires_on", "target_date", mode="before", check_fields=False)
    @classmethod
    def _dates(cls, v):
        return _check_date(v)


class ApplicationIn(_Dated):
    company: Optional[str] = None
    role: Optional[str] = None
    status: Optional[AppStatus] = None
    applied_on: Optional[str] = None
    source: Optional[str] = None
    url: Optional[str] = None
    notes: Optional[str] = None


class CertificationIn(_Dated):
    name: Optional[str] = None
    issuer: Optional[str] = None
    status: Optional[CertStatus] = None
    earned_on: Optional[str] = None
    expires_on: Optional[str] = None
    target_date: Optional[str] = None
    url: Optional[str] = None


class ProjectIn(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    url: Optional[str] = None
    stars: Optional[int] = None
    language: Optional[str] = None
    last_push: Optional[str] = None
    status: Optional[ProjStatus] = None
    featured: Optional[bool] = None
    source: Optional[Literal["manual", "github"]] = None


def _today() -> str:
    return date.today().isoformat()


def _fields(model: BaseModel) -> dict[str, Any]:
    d = model.model_dump(exclude_unset=True)
    return {k: (int(v) if isinstance(v, bool) else v) for k, v in d.items()}


def _create(conn: sqlite3.Connection, table: str, data: dict, required: str, defaults: dict) -> dict:
    if not data.get(required):
        raise HTTPException(422, f"{required} is required")
    data = {**defaults, **{k: v for k, v in data.items() if v is not None}}  # null -> default (e.g. applied_on=today)
    cols = ", ".join(data)
    cur = conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({', '.join('?' * len(data))})", list(data.values()))
    return _get(conn, table, cur.lastrowid)


def _get(conn: sqlite3.Connection, table: str, id_: int) -> dict:
    r = conn.execute(f"SELECT * FROM {table} WHERE id=?", (id_,)).fetchone()
    if not r:
        raise HTTPException(404, f"{table[:-1]} {id_} not found")
    return _out(table, dict(r))


def _out(table: str, r: dict) -> dict:
    if table == "projects":
        r["featured"] = bool(r["featured"])
    return r


def _update(conn: sqlite3.Connection, table: str, id_: int, data: dict) -> dict:
    _get(conn, table, id_)
    if table == "applications" and "status" in data:
        data["last_update"] = _today()
    for k in ("company", "name"):
        if k in data and not data[k]:
            raise HTTPException(422, f"{k} cannot be empty")
    if data:
        sets = ", ".join(f"{k}=?" for k in data)
        conn.execute(f"UPDATE {table} SET {sets} WHERE id=?", [*data.values(), id_])
    return _get(conn, table, id_)


def _delete(conn: sqlite3.Connection, table: str, id_: int) -> dict:
    _get(conn, table, id_)
    conn.execute(f"DELETE FROM {table} WHERE id=?", (id_,))
    return {"ok": True}


# ---- settings helpers --------------------------------------------------

def _setting(conn: sqlite3.Connection, key: str) -> Any:
    r = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return json.loads(r["value"]) if r else None


def _set_setting(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute("INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)", (key, json.dumps(value)))


# ---- applications ------------------------------------------------------

@router.get("/applications")
def list_applications() -> list[dict]:
    with get_conn() as conn:
        return rows(conn.execute("SELECT * FROM applications ORDER BY COALESCE(applied_on, '') DESC, id DESC"))


@router.get("/applications/funnel")
def funnel() -> dict:
    with get_conn() as conn:
        counts = {r["status"]: r["n"] for r in conn.execute("SELECT status, COUNT(*) n FROM applications GROUP BY status")}
        applied_on = [r["applied_on"] for r in conn.execute("SELECT applied_on FROM applications WHERE applied_on IS NOT NULL")]
    today = date.today()
    this_monday = today - timedelta(days=today.weekday())
    weeks = {(this_monday - timedelta(weeks=i)).isoformat(): 0 for i in range(11, -1, -1)}
    for d in applied_on:
        try:
            dd = date.fromisoformat(d[:10])
        except ValueError:
            continue
        key = (dd - timedelta(days=dd.weekday())).isoformat()
        if key in weeks:
            weeks[key] += 1
    return {
        "stages": [{"status": s, "count": counts.get(s, 0)} for s in STAGES],
        "weekly": [{"week": w, "applied": n} for w, n in weeks.items()],
    }


@router.post("/applications", status_code=201)
def create_application(body: ApplicationIn) -> dict:
    data = _fields(body)
    with get_conn() as conn:
        return _create(conn, "applications", data, "company",
                       {"status": "applied", "applied_on": _today(), "last_update": _today()})


@router.put("/applications/{id_}")
def update_application(id_: int, body: ApplicationIn) -> dict:
    with get_conn() as conn:
        return _update(conn, "applications", id_, _fields(body))


@router.delete("/applications/{id_}")
def delete_application(id_: int) -> dict:
    with get_conn() as conn:
        return _delete(conn, "applications", id_)


# ---- certifications ----------------------------------------------------

SEED_CERTS = [
    ("AWS Certified Solutions Architect – Associate", "Amazon Web Services"),
    ("AWS Certified SysOps Administrator – Associate", "Amazon Web Services"),
    ("AWS Certified Cloud Practitioner", "Amazon Web Services"),
    ("CompTIA Security+", "CompTIA"),
    ("CompTIA Network+", "CompTIA"),
    ("CompTIA A+", "CompTIA"),
]


def seed_certifications(conn: sqlite3.Connection) -> None:
    """Insert the already-earned certs once, if the table is empty and never seeded."""
    if _setting(conn, "career_seeded") is not None:
        return
    if conn.execute("SELECT COUNT(*) FROM certifications").fetchone()[0] == 0:
        conn.executemany("INSERT INTO certifications(name, issuer, status) VALUES (?, ?, 'earned')", SEED_CERTS)
        _set_setting(conn, "career_seeded", True)


@router.get("/certifications")
def list_certifications() -> list[dict]:
    with get_conn() as conn:
        seed_certifications(conn)
        return rows(conn.execute("SELECT * FROM certifications ORDER BY id"))


@router.post("/certifications", status_code=201)
def create_certification(body: CertificationIn) -> dict:
    with get_conn() as conn:
        return _create(conn, "certifications", _fields(body), "name", {"status": "planned"})


@router.put("/certifications/{id_}")
def update_certification(id_: int, body: CertificationIn) -> dict:
    with get_conn() as conn:
        return _update(conn, "certifications", id_, _fields(body))


@router.delete("/certifications/{id_}")
def delete_certification(id_: int) -> dict:
    with get_conn() as conn:
        return _delete(conn, "certifications", id_)


# ---- projects ----------------------------------------------------------

@router.get("/projects")
def list_projects() -> list[dict]:
    with get_conn() as conn:
        return [_out("projects", r) for r in rows(conn.execute(
            "SELECT * FROM projects ORDER BY featured DESC, COALESCE(last_push, '') DESC, id DESC"))]


@router.post("/projects", status_code=201)
def create_project(body: ProjectIn) -> dict:
    with get_conn() as conn:
        return _create(conn, "projects", _fields(body), "name", {"status": "idea"})


@router.put("/projects/{id_}")
def update_project(id_: int, body: ProjectIn) -> dict:
    with get_conn() as conn:
        return _update(conn, "projects", id_, _fields(body))


@router.delete("/projects/{id_}")
def delete_project(id_: int) -> dict:
    with get_conn() as conn:
        return _delete(conn, "projects", id_)


# ---- summary & sync ----------------------------------------------------

@router.get("/summary")
def summary() -> dict:
    with get_conn() as conn:
        seed_certifications(conn)
        c = {r["status"]: r["n"] for r in conn.execute("SELECT status, COUNT(*) n FROM applications GROUP BY status")}
        certs = {r["status"]: r["n"] for r in conn.execute("SELECT status, COUNT(*) n FROM certifications GROUP BY status")}
        projects = conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
        total = sum(c.values())
        sent = total - c.get("wishlist", 0)
        responded = sum(c.get(s, 0) for s in ("interview", "offer", "rejected"))
        return {
            "applications_total": total,
            "active": c.get("applied", 0) + c.get("interview", 0),
            "interviews": c.get("interview", 0),
            "offers": c.get("offer", 0),
            "response_rate": round(responded / sent, 4) if sent else 0,
            "certs_earned": certs.get("earned", 0),
            "certs_in_progress": certs.get("in_progress", 0),
            "projects_total": projects,
            "last_sync": {"github": _setting(conn, "last_sync_github"), "email": _setting(conn, "last_sync_email")},
        }


@router.post("/sync/github")
def sync_github_endpoint() -> dict:
    from ..services.github_sync import sync_github
    with get_conn() as conn:
        try:
            return sync_github(conn)
        except SyncError as e:
            raise HTTPException(502, str(e))


@router.post("/sync/email")
def sync_email_endpoint(full: bool = False) -> dict:
    s = get_settings()
    if not (s.imap_host and s.imap_user and s.imap_password):
        raise HTTPException(400, "Email sync not configured: set IMAP_HOST, IMAP_USER and IMAP_PASSWORD")
    from ..services.email_sync import sync_email
    with get_conn() as conn:
        try:
            return sync_email(conn, full=full)
        except SyncError as e:
            raise HTTPException(502, str(e))
