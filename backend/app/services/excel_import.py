"""Workbook (.xlsx) -> SQLite import. Replace-by-source: finance tables are rebuilt on every import.

Notes / limitations:
- Spend amounts are stored as abs(); a sheet that uses negatives for spending works, but refunds
  (positive amounts in a negative-spend sheet, or vice versa) cannot be told apart and count as spend.
- Balances are stored as abs(); direction comes from the Type cell (asset/liability).
"""
from __future__ import annotations

import hashlib
import re
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from ..config import get_settings
from ..db import get_conn
from .email_sync import RANK as _RANK
from .email_sync import _external_id as _email_external_id

_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d")
_LIABILITY_WORDS = ("liab", "debt", "loan", "credit")
HEADER_SCAN_ROWS = 10
_IMPORT_LOCK = threading.Lock()  # upload endpoint and scheduler may import concurrently


def _norm(v: Any) -> str:
    return str(v).strip().lower() if v is not None else ""


def parse_date(v: Any) -> str | None:
    """Return ISO date string or None."""
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, str):
        s = v.strip()
        for cand in (s, s[:10]):
            for fmt in _DATE_FORMATS:
                try:
                    return datetime.strptime(cand, fmt).date().isoformat()
                except ValueError:
                    pass
    return None


def parse_amount(v: Any) -> float | None:
    """Parse numbers or strings like '$1,234.56' / '(12.00)' (negative). None if unparseable."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v) if v == v else None
    s = str(v).strip()
    if not s:
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = re.sub(r"[^\d.\-]", "", s)
    try:
        n = float(s)
    except ValueError:
        return None
    return -abs(n) if neg else n


def _text(row: tuple, idx: int | None) -> str:
    if idx is None or idx >= len(row) or row[idx] is None:
        return ""
    return str(row[idx]).strip()


class _Sheet:
    """A located sheet: header-mapped column indexes plus its data rows."""

    def __init__(self, rows: list[tuple], columns: dict[str, str], name: str, key: str = "date"):
        self.name = name
        want = _norm(columns[key])
        hdr = next((i for i, r in enumerate(rows[:HEADER_SCAN_ROWS]) if want in [_norm(c) for c in r]), None)
        if hdr is None:
            raise ValueError(f"sheet '{name}': header row with '{columns[key]}' not found in first {HEADER_SCAN_ROWS} rows")
        header = [_norm(c) for c in rows[hdr]]
        self.idx = {f: (header.index(_norm(h)) if _norm(h) in header else None) for f, h in columns.items()}
        self.rows = rows[hdr + 1:]

    def need(self, *fields: str, any_of: bool = False) -> None:
        missing = [f for f in fields if self.idx.get(f) is None]
        if (any_of and len(missing) == len(fields)) or (not any_of and missing):
            raise ValueError(f"sheet '{self.name}': missing required column(s) {', '.join(missing)}")

    def get(self, row: tuple, field: str) -> Any:
        i = self.idx.get(field)
        return row[i] if i is not None and i < len(row) else None


def _read_sheet(wb, cfg: dict, warnings: list[str] | None, key: str = "date") -> _Sheet | None:
    by_name = {_norm(n): n for n in wb.sheetnames}
    real = by_name.get(_norm(cfg["name"]))
    if real is None:
        if warnings is not None:
            warnings.append(f"sheet '{cfg['name']}' not found")
        return None
    rows = [tuple(r) for r in wb[real].iter_rows(values_only=True)]
    return _Sheet(rows, cfg["columns"], real, key)


def _income_rows(sh: _Sheet, types: dict[str, str]) -> tuple[list[tuple], int]:
    sh.need("date", "amount")
    out, skipped = [], 0
    for r in sh.rows:
        if all(c is None or str(c).strip() == "" for c in r):
            continue
        d, amt = parse_date(sh.get(r, "date")), parse_amount(sh.get(r, "amount"))
        if d is None or amt is None:
            skipped += 1
            continue
        source, typ_cell = _text(r, sh.idx["source"]), _text(r, sh.idx["type"])
        typ = "other"
        for text in (typ_cell.lower(), source.lower()):
            hit = next((v for k, v in types.items() if k.lower() in text and text), None)
            if hit:
                typ = hit
                break
        out.append((d, source, typ, amt, _text(r, sh.idx["note"])))
    return out, skipped


def _tx_rows(sh: _Sheet, buckets: dict[str, str], default: str) -> tuple[list[tuple], int]:
    sh.need("date", "amount")
    bmap = {k.lower(): v for k, v in buckets.items()}
    out, skipped = [], 0
    for r in sh.rows:
        if all(c is None or str(c).strip() == "" for c in r):
            continue
        d, amt = parse_date(sh.get(r, "date")), parse_amount(sh.get(r, "amount"))
        if d is None or amt is None:
            skipped += 1
            continue
        cat = _text(r, sh.idx["category"])
        out.append((d, _text(r, sh.idx["description"]), cat, bmap.get(cat.lower(), default), abs(amt)))
    return out, skipped


def _balance_rows(sh: _Sheet) -> tuple[list[tuple], int]:
    sh.need("date", "balance")
    out, skipped = [], 0
    for r in sh.rows:
        if all(c is None or str(c).strip() == "" for c in r):
            continue
        d, bal = parse_date(sh.get(r, "date")), parse_amount(sh.get(r, "balance"))
        if d is None or bal is None:
            skipped += 1
            continue
        account = _text(r, sh.idx["account"]) or "Account"
        kind_text = (_text(r, sh.idx["kind"]) or account).lower()
        kind = "liability" if any(w in kind_text for w in _LIABILITY_WORDS) else "asset"
        out.append((d, account, kind, abs(bal)))
    return out, skipped


def _business_rows(sh: _Sheet) -> tuple[list[tuple], int]:
    sh.need("date")
    sh.need("revenue", "expense", any_of=True)
    out, skipped = [], 0
    for r in sh.rows:
        if all(c is None or str(c).strip() == "" for c in r):
            continue
        d = parse_date(sh.get(r, "date"))
        rev, exp = parse_amount(sh.get(r, "revenue")), parse_amount(sh.get(r, "expense"))
        if d is None or (rev is None and exp is None):
            skipped += 1
            continue
        out.append((d, _text(r, sh.idx["description"]), rev or 0.0, abs(exp or 0.0)))
    return out, skipped


APP_STATUSES = ("wishlist", "applied", "interview", "offer", "rejected", "withdrawn")


def _application_rows(sh: _Sheet) -> tuple[list[dict], int]:
    """Rows with a company; only non-empty cells are 'provided'. Invalid status -> 'applied'."""
    sh.need("company")
    out, skipped = [], 0
    for r in sh.rows:
        if all(c is None or str(c).strip() == "" for c in r):
            continue
        company = _text(r, sh.idx["company"])
        if not company:
            skipped += 1
            continue
        rec: dict[str, Any] = {"company": company, "role": _text(r, sh.idx["role"])}
        status = _text(r, sh.idx["status"]).lower()
        if status:
            rec["status"] = status if status in APP_STATUSES else "applied"
        for f in ("applied_on", "last_update"):
            if d := parse_date(sh.get(r, f)):
                rec[f] = d
        for f in ("source", "url", "notes"):
            if v := _text(r, sh.idx[f]):
                rec[f] = v
        out.append(rec)
    return out, skipped


def _merged_status(current: str, sheet: str) -> str:
    """Sheet statuses only move an application forward, so re-importing an older sheet never undoes
    progress found by email sync. Explicit rejected/withdrawn/offer in the sheet always win."""
    if sheet in ("rejected", "withdrawn", "offer") or current not in _RANK:
        return sheet
    return sheet if _RANK.get(sheet, 0) > _RANK[current] else current


def _upsert_applications(conn, recs: list[dict]) -> None:
    for rec in recs:
        email_ext = _email_external_id(rec["company"], rec["role"])
        ext = "sheet:" + email_ext.split(":", 1)[1]
        row = conn.execute("SELECT id, status, last_update FROM applications WHERE external_id IN (?, ?) "
                           "ORDER BY external_id = ? DESC LIMIT 1", (ext, email_ext, ext)).fetchone()
        if row:
            rec = dict(rec)
            if "status" in rec:
                rec["status"] = _merged_status(row["status"], rec["status"])
            if rec.get("last_update") and row["last_update"]:
                rec["last_update"] = max(rec["last_update"], row["last_update"])
            sets = ", ".join(f"{k}=?" for k in rec)
            conn.execute(f"UPDATE applications SET {sets} WHERE id=?", [*rec.values(), row["id"]])
        else:
            new = {"status": "applied", "source": "sheet", **rec, "external_id": ext}
            new.setdefault("last_update", new.get("applied_on"))
            conn.execute(f"INSERT INTO applications ({', '.join(new)}) VALUES ({', '.join('?' * len(new))})", list(new.values()))


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _record(conn, **kw) -> dict:
    row = {"imported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "rows_income": 0,
           "rows_transactions": 0, "rows_balances": 0, "rows_business": 0, "message": "", **kw}
    cols = ", ".join(row)
    cur = conn.execute(f"INSERT INTO imports ({cols}) VALUES ({', '.join('?' * len(row))})", tuple(row.values()))
    return {"id": cur.lastrowid, **row}


def import_workbook(path: Path, force: bool = False, name: str | None = None) -> dict:
    """Import the workbook at `path`; returns the imports-row dict (status ok|skipped|error).

    `name` overrides the file name recorded in history (used when importing an upload from a temp file).
    """
    with _IMPORT_LOCK:
        return _import(Path(path), force, name or Path(path).name)


def _import(path: Path, force: bool, fname: str) -> dict:
    cfg = get_settings().config["workbook"]
    sha = ""
    try:
        sha = _sha256(path)
        if not force:
            with get_conn() as conn:
                last = conn.execute("SELECT sha256 FROM imports WHERE status='ok' ORDER BY id DESC LIMIT 1").fetchone()
            if last and last["sha256"] == sha:
                return {"id": None, "imported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        "file": fname, "sha256": sha, "rows_income": 0, "rows_transactions": 0,
                        "rows_balances": 0, "rows_business": 0, "status": "skipped",
                        "message": "unchanged since last import"}

        warnings: list[str] = []
        wb = load_workbook(path, read_only=True, data_only=True)
        try:
            sheets = {k: _read_sheet(wb, cfg["sheets"][k], warnings)
                      for k in ("income", "transactions", "balances", "business")}
            if all(s is None for s in sheets.values()):
                raise ValueError("none of the configured sheets were found: " + "; ".join(warnings))
            data: dict[str, list[tuple]] = {k: [] for k in sheets}
            skipped: dict[str, int] = {}
            if sheets["income"]:
                data["income"], skipped["income"] = _income_rows(sheets["income"], cfg["income_types"])
            if sheets["transactions"]:
                data["transactions"], skipped["transactions"] = _tx_rows(
                    sheets["transactions"], cfg["category_buckets"], cfg["default_bucket"])
            if sheets["balances"]:
                data["balances"], skipped["balances"] = _balance_rows(sheets["balances"])
            if sheets["business"]:
                data["business"], skipped["business"] = _business_rows(sheets["business"])
            apps_sheet = _read_sheet(wb, cfg["sheets"]["applications"], None, "company") if "applications" in cfg["sheets"] else None
            apps, apps_skipped = _application_rows(apps_sheet) if apps_sheet else ([], 0)
            if apps_skipped:
                skipped["applications"] = apps_skipped
        finally:
            wb.close()

        parts = [f"{k}: {len(v)} rows" for k, v in data.items()]
        if apps_sheet:
            parts.append(f"applications: {len(apps)} rows")
        msg = "; ".join(parts)
        if any(skipped.values()):
            msg += "; skipped unparseable rows (" + ", ".join(f"{k} {n}" for k, n in skipped.items() if n) + ")"
        if warnings:
            msg += "; warnings: " + "; ".join(warnings)

        with get_conn() as conn:  # single transaction: replace-by-source + history row
            for t in ("income", "transactions", "balances", "business"):
                conn.execute(f"DELETE FROM {t}")
            conn.executemany("INSERT INTO income (date, source, type, amount, note) VALUES (?,?,?,?,?)", data["income"])
            conn.executemany("INSERT INTO transactions (date, description, category, bucket, amount) VALUES (?,?,?,?,?)", data["transactions"])
            conn.executemany("INSERT INTO balances (date, account, kind, balance) VALUES (?,?,?,?)", data["balances"])
            conn.executemany("INSERT INTO business (date, description, revenue, expense) VALUES (?,?,?,?)", data["business"])
            _upsert_applications(conn, apps)
            return _record(conn, file=fname, sha256=sha, status="ok", message=msg,
                           rows_income=len(data["income"]), rows_transactions=len(data["transactions"]),
                           rows_balances=len(data["balances"]), rows_business=len(data["business"]))
    except Exception as exc:  # noqa: BLE001 - recorded, not raised
        with get_conn() as conn:
            return _record(conn, file=fname, sha256=sha, status="error", message=f"{type(exc).__name__}: {exc}")
