import asyncio
import os

import pytest

from app import scheduler


def test_tick(settings_env, monkeypatch):
    calls = []
    monkeypatch.setattr(scheduler, "_run_github", lambda: calls.append("gh"))
    monkeypatch.setattr(scheduler, "_run_email", lambda: calls.append("email"))
    monkeypatch.setattr(scheduler, "_import_workbook", lambda p: calls.append("wb"))
    for k in ("IMAP_HOST", "IMAP_USER", "IMAP_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    from app.config import get_settings
    get_settings.cache_clear()
    wb = get_settings().workbook_path
    monkeypatch.setitem(get_settings().config["workbook"], "path", str(settings_env / "b.xlsx"))
    wb = settings_env / "b.xlsx"
    state: dict = {}

    asyncio.run(scheduler.tick(state, 1000.0))
    assert calls == ["gh"]  # no workbook, no IMAP

    wb.write_bytes(b"x")
    asyncio.run(scheduler.tick(state, 1010.0))
    assert calls == ["gh", "wb"]
    asyncio.run(scheduler.tick(state, 1020.0))
    assert calls == ["gh", "wb"]  # mtime unchanged, sync interval not elapsed
    os.utime(wb, (1, 1))
    asyncio.run(scheduler.tick(state, 1030.0))
    assert calls == ["gh", "wb", "wb"]

    monkeypatch.setenv("IMAP_HOST", "h"); monkeypatch.setenv("IMAP_USER", "u"); monkeypatch.setenv("IMAP_PASSWORD", "p")
    get_settings.cache_clear()
    monkeypatch.setitem(get_settings().config["workbook"], "path", str(wb))
    asyncio.run(scheduler.tick(state, 1000.0 + 7 * 3600))
    assert calls[-2:] == ["gh", "email"]


def test_job_failure_is_logged(settings_env, monkeypatch):
    def boom():
        raise RuntimeError("x")
    monkeypatch.setattr(scheduler, "_run_github", boom)
    asyncio.run(scheduler.tick({}, 1.0))  # must not raise
