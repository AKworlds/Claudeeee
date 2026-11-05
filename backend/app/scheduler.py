"""Background jobs: workbook re-import on mtime change, periodic career syncs."""
from __future__ import annotations

import asyncio
import logging
import time

from .config import get_settings
from .db import get_conn

log = logging.getLogger(__name__)


def _run_github() -> None:
    from .services.github_sync import sync_github
    with get_conn() as conn:
        log.info("github sync: %s", sync_github(conn))


def _run_email() -> None:
    from .services.email_sync import sync_email
    with get_conn() as conn:
        log.info("email sync: %s", sync_email(conn))


def _run_drive() -> None:
    from .services.drive_sync import sync_drive
    log.info("drive sync: %s", sync_drive())


def _import_workbook(path) -> None:
    from .services.excel_import import import_workbook
    log.info("workbook import: %s", import_workbook(path))


async def _safe(fn, *args) -> None:
    try:
        await asyncio.to_thread(fn, *args)
    except Exception:  # includes ImportError; the scheduler must never die
        log.exception("scheduled job %s failed", getattr(fn, "__name__", fn))


async def tick(state: dict, now: float) -> None:
    """One scheduler iteration. `state` keeps last_mtime and last_career_sync between calls."""
    s = get_settings()
    path = s.workbook_path
    try:
        mtime = path.stat().st_mtime if path.exists() else None
    except OSError:
        mtime = None
    if mtime is not None and mtime != state.get("last_mtime"):
        state["last_mtime"] = mtime
        await _safe(_import_workbook, path)

    poll = float(s.config["scheduler"]["workbook_poll_seconds"])
    if s.google_service_account_file and s.config["workbook"].get("drive_file_id") \
            and now - state.get("last_drive", float("-inf")) >= poll:
        state["last_drive"] = now
        await _safe(_run_drive)

    interval = float(s.config["scheduler"]["career_sync_hours"]) * 3600
    last = state.get("last_career_sync")
    if last is None or now - last >= interval:
        state["last_career_sync"] = now
        await _safe(_run_github)
        if s.imap_host and s.imap_user and s.imap_password:
            await _safe(_run_email)


async def run_forever() -> None:
    """Loop until cancelled."""
    state: dict = {}
    poll = float(get_settings().config["scheduler"]["workbook_poll_seconds"])
    while True:
        try:
            await tick(state, time.time())
        except Exception:
            log.exception("scheduler tick failed")
        await asyncio.sleep(poll)
