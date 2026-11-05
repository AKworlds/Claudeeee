"""SQLite connection helpers and schema. Single source of truth for the data model."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .config import get_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS income (
    id INTEGER PRIMARY KEY, date TEXT NOT NULL, source TEXT NOT NULL DEFAULT '',
    type TEXT NOT NULL DEFAULT 'other', amount REAL NOT NULL, note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_income_date ON income(date);

CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY, date TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT '', bucket TEXT NOT NULL DEFAULT 'wants', amount REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_tx_date ON transactions(date);

CREATE TABLE IF NOT EXISTS balances (
    id INTEGER PRIMARY KEY, date TEXT NOT NULL, account TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('asset','liability')), balance REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_bal_date ON balances(date);

CREATE TABLE IF NOT EXISTS business (
    id INTEGER PRIMARY KEY, date TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
    revenue REAL NOT NULL DEFAULT 0, expense REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS imports (
    id INTEGER PRIMARY KEY, imported_at TEXT NOT NULL, file TEXT NOT NULL, sha256 TEXT NOT NULL,
    rows_income INTEGER NOT NULL DEFAULT 0, rows_transactions INTEGER NOT NULL DEFAULT 0,
    rows_balances INTEGER NOT NULL DEFAULT 0, rows_business INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL, message TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS milestones (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'custom',
    target REAL NOT NULL, metric TEXT NOT NULL DEFAULT 'net_worth',
    created_at TEXT NOT NULL, achieved_at TEXT
);

CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY, company TEXT NOT NULL, role TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'applied', applied_on TEXT, source TEXT NOT NULL DEFAULT 'manual',
    url TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '', last_update TEXT,
    external_id TEXT UNIQUE
);

CREATE TABLE IF NOT EXISTS certifications (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, issuer TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'planned', earned_on TEXT, expires_on TEXT,
    target_date TEXT, url TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '', stars INTEGER NOT NULL DEFAULT 0, language TEXT NOT NULL DEFAULT '',
    last_push TEXT, status TEXT NOT NULL DEFAULT 'building', featured INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'manual', external_id TEXT UNIQUE
);

CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);

-- Side-business payouts logged from the app (phone). Not touched by workbook imports.
CREATE TABLE IF NOT EXISTS business_payouts (
    id INTEGER PRIMARY KEY, date TEXT NOT NULL, amount REAL NOT NULL, expenses REAL NOT NULL DEFAULT 0,
    note TEXT NOT NULL DEFAULT '', saved REAL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_payout_date ON business_payouts(date);

-- Income added from the app (button on Overview/Money). Not touched by workbook imports;
-- counted together with workbook income everywhere.
CREATE TABLE IF NOT EXISTS income_entries (
    id INTEGER PRIMARY KEY, date TEXT NOT NULL, source TEXT NOT NULL DEFAULT '',
    type TEXT NOT NULL DEFAULT 'other', amount REAL NOT NULL, note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_income_entries_date ON income_entries(date);

-- Estimated tax payments actually made (IRS / state).
CREATE TABLE IF NOT EXISTS tax_payments (
    id INTEGER PRIMARY KEY, date TEXT NOT NULL, amount REAL NOT NULL, period TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT ''
);
"""


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    """Yield a connection; commits on success, rolls back on error."""
    conn = _connect(get_settings().db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA)


def rows(cur: sqlite3.Cursor) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]
