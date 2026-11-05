"""Pull the budget workbook from Google Drive (service account, read-only) and import it."""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from typing import Callable

import httpx

from ..config import get_settings
from ..db import get_conn
from .errors import SyncError
from .excel_import import import_workbook

API = "https://www.googleapis.com/drive/v3/files"
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
SHEET_MIME = "application/vnd.google-apps.spreadsheet"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
TOKEN_TYPE = Callable[[], str]


def configured() -> bool:
    s = get_settings()
    return bool(s.google_service_account_file and s.config["workbook"].get("drive_file_id"))


def service_account_email() -> str | None:
    f = get_settings().google_service_account_file
    try:
        return json.loads(open(f).read()).get("client_email") if f else None
    except (OSError, ValueError):
        return None


def _default_token() -> str:
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account
    try:
        creds = service_account.Credentials.from_service_account_file(get_settings().google_service_account_file, scopes=SCOPES)
        creds.refresh(Request())
    except Exception as e:  # noqa: BLE001 - file/format/network problems all end up user-facing
        raise SyncError(f"Google authentication failed: {e}") from e
    return creds.token


def _get_setting(conn, key: str):
    r = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return json.loads(r["value"]) if r else None


def _set_setting(conn, key: str, value) -> None:
    conn.execute("INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)", (key, json.dumps(value)))


def _check(r: httpx.Response, what: str) -> httpx.Response:
    if r.status_code in (403, 404):
        raise SyncError(f"Google Drive {what} failed ({r.status_code}): file not found or not shared with the service account "
                        f"{service_account_email() or ''}".strip())
    if r.status_code >= 400:
        raise SyncError(f"Google Drive {what} failed (HTTP {r.status_code})")
    return r


def sync_drive(client: httpx.Client | None = None, token_provider: TOKEN_TYPE | None = None) -> dict:
    """Returns {status: ok|skipped|error, message, modified_time, ...}. Raises SyncError when it cannot run."""
    if not configured():
        raise SyncError("Drive sync not configured: set GOOGLE_SERVICE_ACCOUNT_FILE and workbook.drive_file_id")
    s = get_settings()
    file_id = s.config["workbook"]["drive_file_id"]
    headers = {"Authorization": f"Bearer {(token_provider or _default_token)()}"}
    own = client is None
    client = client or httpx.Client(timeout=60, follow_redirects=True)
    tmp = None
    try:
        try:
            meta = _check(client.get(f"{API}/{file_id}", headers=headers, params={
                "fields": "id,name,mimeType,modifiedTime,md5Checksum", "supportsAllDrives": "true"}), "lookup").json()
            with get_conn() as conn:
                if meta.get("modifiedTime") and meta["modifiedTime"] == _get_setting(conn, "drive_last_modified"):
                    return {"status": "skipped", "message": "unchanged since last Drive sync", "modified_time": meta["modifiedTime"]}
            if meta.get("mimeType") == SHEET_MIME:
                r = client.get(f"{API}/{file_id}/export", headers=headers, params={"mimeType": XLSX_MIME})
            else:
                r = client.get(f"{API}/{file_id}", headers=headers, params={"alt": "media", "supportsAllDrives": "true"})
            data = _check(r, "download").content
        except httpx.HTTPError as e:
            raise SyncError(f"Google Drive request failed: {e}") from e
        if not data.startswith(b"PK"):
            raise SyncError("Downloaded Drive file is not a valid .xlsx")
        s.workbook_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=s.workbook_path.parent, prefix=".drive-", suffix=".xlsx")
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        result = import_workbook(tmp, force=False, name=meta.get("name") or s.workbook_path.name)
        if result["status"] in ("ok", "skipped"):
            os.replace(tmp, s.workbook_path)  # keep a local copy
            tmp = None
        with get_conn() as conn:
            # Remembered even on error: a broken file is retried only after it changes in Drive,
            # instead of logging a failed import every poll.
            _set_setting(conn, "drive_last_modified", meta.get("modifiedTime"))
            _set_setting(conn, "last_sync_drive", datetime.now(timezone.utc).isoformat())
        return {**result, "modified_time": meta.get("modifiedTime")}
    finally:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)
        if own:
            client.close()
