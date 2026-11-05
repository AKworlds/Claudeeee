import json

import httpx
import pytest
from openpyxl import Workbook

from app.config import get_settings
from app.db import get_conn
from app.services import drive_sync
from app.services.drive_sync import SHEET_MIME, XLSX_MIME, SyncError, sync_drive


def xlsx_bytes(path):
    wb = Workbook()
    ws = wb.active
    ws.title = "Income"
    ws.append(["Date", "Source", "Type", "Amount", "Note"])
    ws.append(["2026-01-15", "Employer", "Paycheck", 100, ""])
    wb.save(path)
    return path.read_bytes()


@pytest.fixture()
def drive_env(settings_env, monkeypatch):
    sa = settings_env / "sa.json"
    sa.write_text(json.dumps({"client_email": "svc@proj.iam.gserviceaccount.com", "private_key": "x"}))
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_FILE", str(sa))
    get_settings.cache_clear()
    cfg = get_settings().config["workbook"]
    cfg["drive_file_id"] = "FID"
    cfg["path"] = str(settings_env / "budget.xlsx")
    return settings_env


def client(meta, body, calls):
    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        assert req.headers["authorization"] == "Bearer tok"
        if req.url.path.endswith("/export"):
            assert req.url.params["mimeType"] == XLSX_MIME
            return httpx.Response(200, content=body)
        if req.url.params.get("alt") == "media":
            return httpx.Response(200, content=body)
        return httpx.Response(200, json=meta)
    return httpx.Client(transport=httpx.MockTransport(handler))


def run(c):
    return sync_drive(client=c, token_provider=lambda: "tok")


def test_download_then_skip(drive_env):
    body, calls = xlsx_bytes(drive_env / "src.xlsx"), []
    meta = {"id": "FID", "name": "Budget.xlsx", "mimeType": XLSX_MIME, "modifiedTime": "2026-11-01T00:00:00Z"}
    r = run(client(meta, body, calls))
    assert r["status"] == "ok" and r["rows_income"] == 1 and r["file"] == "Budget.xlsx"
    assert (drive_env / "budget.xlsx").read_bytes() == body
    assert not list(drive_env.glob(".drive-*"))
    n = len(calls)
    assert run(client(meta, body, calls))["status"] == "skipped"
    assert len(calls) == n + 1  # metadata only
    with get_conn() as conn:
        assert conn.execute("SELECT value FROM settings WHERE key='drive_last_modified'").fetchone()


def test_google_sheet_export(drive_env):
    meta = {"id": "FID", "name": "Budget", "mimeType": SHEET_MIME, "modifiedTime": "t1"}
    assert run(client(meta, xlsx_bytes(drive_env / "s.xlsx"), []))["status"] == "ok"


def test_errors(drive_env):
    c = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    with pytest.raises(SyncError, match="svc@proj"):
        run(c)
    meta = {"id": "FID", "name": "x", "mimeType": XLSX_MIME, "modifiedTime": "t2"}
    with pytest.raises(SyncError, match="not a valid"):
        run(client(meta, b"nope", []))




def test_endpoint_unconfigured(client, monkeypatch):
    monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_FILE", raising=False)
    get_settings.cache_clear()
    assert client.post("/api/finance/drive/sync").status_code == 400
    assert client.get("/api/finance/drive").json()["configured"] is False


def test_endpoint_status_and_502(drive_env, client, monkeypatch):
    j = client.get("/api/finance/drive").json()
    assert j["configured"] and j["service_account_email"] == "svc@proj.iam.gserviceaccount.com" and "key" not in json.dumps(j)
    monkeypatch.setattr(drive_sync, "_default_token", lambda: (_ for _ in ()).throw(SyncError("boom")))
    assert client.post("/api/finance/drive/sync").status_code == 502


def test_template_endpoint(client):
    r = client.get("/api/finance/template")
    assert r.status_code == 200 and r.content[:2] == b"PK"


def test_broken_drive_file_not_reimported_every_poll(drive_env):
    from openpyxl import Workbook

    from app.db import get_conn
    from app.services.drive_sync import sync_drive
    import io
    wb = Workbook(); wb.active.title = "Unrelated"
    buf = io.BytesIO(); wb.save(buf)
    calls = {"download": 0}

    def handler(req):
        if req.url.params.get("alt") == "media":
            calls["download"] += 1
            return httpx.Response(200, content=buf.getvalue())
        return httpx.Response(200, json={"id": "F", "name": "budget.xlsx", "mimeType": "x", "modifiedTime": "T1"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert sync_drive(client, lambda: "tok")["status"] == "error"
    assert sync_drive(client, lambda: "tok")["status"] == "skipped"
    assert calls["download"] == 1
    with get_conn() as c:
        assert c.execute("SELECT COUNT(*) FROM imports WHERE status='error'").fetchone()[0] == 1
