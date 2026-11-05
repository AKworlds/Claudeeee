from datetime import date

from openpyxl import Workbook

from app.db import get_conn
from app.services.email_sync import _external_id
from app.services.excel_import import import_workbook

HDR = ["Company", "Role", "Status", "Applied On", "Last Update", "Source", "URL", "Notes"]


def make(path, rows):
    wb = Workbook()
    wb.active.title = "Income"
    wb.active.append(["Date", "Amount"])
    ws = wb.create_sheet("Job Applications")
    ws.append(HDR)
    for r in rows:
        ws.append(r)
    wb.save(path)
    return path


def apps():
    with get_conn() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM applications ORDER BY id")]


def test_upsert_and_merge(settings_env):
    p = settings_env
    with get_conn() as conn:  # a manual row and an email row must survive imports
        conn.execute("INSERT INTO applications(company, source) VALUES ('Manual Co', 'manual')")
        conn.execute("INSERT INTO applications(company, role, status, source, external_id) VALUES ('Acme', 'SRE', 'applied', 'email', ?)",
                     (_external_id("Acme", "SRE"),))
    r = import_workbook(make(p / "a.xlsx", [["acme ", "sre", "interview", date(2026, 10, 1), None, None, None, "n1"],
                                            ["Initech", "Dev", "nonsense", None, None, "Referral", "http://i", ""]]))
    assert r["status"] == "ok" and "applications: 2 rows" in r["message"]
    got = apps()
    assert [a["company"] for a in got] == ["Manual Co", "acme", "Initech"]  # merged, no duplicate
    acme = got[1]
    assert (acme["status"], acme["applied_on"], acme["notes"], acme["source"]) == ("interview", "2026-10-01", "n1", "email")
    assert got[2]["status"] == "applied" and got[2]["external_id"].startswith("sheet:") and got[2]["source"] == "Referral"

    # re-import with changes: updates in place, never deletes rows absent from the sheet
    import_workbook(make(p / "b.xlsx", [["Initech", "Dev", "offer", None, None, None, None, "better"]]))
    got = apps()
    assert len(got) == 3
    assert (got[2]["status"], got[2]["notes"], got[2]["url"]) == ("offer", "better", "http://i")


def test_reimporting_sheet_never_downgrades_email_progress(settings_env, tmp_path):
    from app.db import get_conn
    from app.services.excel_import import import_workbook
    from app.services.template import build_template

    p = build_template(tmp_path / "wb.xlsx", [{"company": "Globex", "role": "Cloud Engineer", "status": "applied",
                                                "applied_on": "2026-10-01"}])
    assert import_workbook(p)["status"] == "ok"
    with get_conn() as c:  # email sync later finds an interview invite
        c.execute("UPDATE applications SET status='interview' WHERE company='Globex'")
    assert import_workbook(p, force=True)["status"] == "ok"
    with get_conn() as c:
        assert c.execute("SELECT status FROM applications WHERE company='Globex'").fetchone()[0] == "interview"
