from datetime import date
from email.message import EmailMessage

import pytest

from app.db import get_conn
from app.services.email_sync import SyncError, extract_company, extract_role, sync_email

TODAY = date(2026, 11, 5)


def mail(sender, subject, body, day, html=False):
    m = EmailMessage()
    m["From"], m["Subject"], m["Date"] = sender, subject, f"{day} Nov 2026 10:00:00 +0000"
    if html:
        m.set_content(f"<html><body><p>{body}</p></body></html>", subtype="html")
    else:
        m.set_content(body)
    return m.as_bytes()


class FakeIMAP:
    def __init__(self, messages):
        self.messages, self.logged_out, self.readonly, self.criteria = messages, False, None, None

    def login(self, u, p):
        return "OK", []

    def select(self, folder, readonly=False):
        self.readonly = readonly
        return "OK", [b"1"]

    def search(self, charset, *criteria):
        self.criteria = criteria
        return "OK", [b" ".join(str(i + 1).encode() for i in range(len(self.messages)))]

    def fetch(self, nums, spec):
        self.fetch_calls = getattr(self, "fetch_calls", 0) + 1
        out = []
        for n in nums.split(b","):
            out += [(n + b" (BODY[] {10}", self.messages[int(n) - 1]), b")"]
        return "OK", out

    def logout(self):
        self.logged_out = True


@pytest.fixture()
def imap_env(settings_env, monkeypatch):
    monkeypatch.setenv("IMAP_HOST", "imap.test")
    monkeypatch.setenv("IMAP_USER", "me@test")
    monkeypatch.setenv("IMAP_PASSWORD", "pw")
    from app.config import get_settings
    get_settings.cache_clear()


def run(msgs):
    fake = FakeIMAP(msgs)
    with get_conn() as conn:
        conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES ('last_sync_email', '\"x\"')")  # not a first sync
        res = sync_email(conn, lambda: fake, TODAY)
        apps = [dict(r) for r in conn.execute("SELECT * FROM applications ORDER BY id")]
    return fake, res, apps


def test_extractors():
    assert extract_company("Greenhouse <no-reply@us.greenhouse.io>", "Thank you for applying to Acme Corp") == "Acme Corp"
    assert extract_company("Lever <jobs@lever.co>", "Your Initech application") == "Initech"
    assert extract_company("Globex Recruiting <hr@globex.com>", "x") == "Globex"
    assert extract_company("<jobs@umbrella.com>", "x") == "Umbrella"
    assert extract_role("Application for the Cloud Engineer position at Acme") == "Cloud Engineer"
    assert extract_role("Your application for Site Reliability Engineer - Acme") == "Site Reliability Engineer"
    assert extract_role("Hello") == ""


def test_sync_flow(imap_env):
    msgs = [
        mail("Greenhouse <no-reply@greenhouse.io>", "Thank you for applying to Acme", "We received your application.", 1),
        mail("Globex Recruiting <hr@globex.com>", "Application for the Cloud Engineer role", "<b>Thanks for applying</b>", 2, html=True),
        mail("Newsletter <news@spam.com>", "Weekly digest", "nothing relevant", 2),
        mail("Globex Recruiting <hr@globex.com>", "Interview for the Cloud Engineer role", "Let's schedule a call", 4),
    ]
    fake, res, apps = run(msgs)
    assert fake.logged_out and fake.readonly and fake.criteria == ("SINCE", "06-Oct-2026")
    assert (res["added"], res["updated"]) == (2, 1)
    by = {a["company"]: a for a in apps}
    assert by["Acme"]["status"] == "applied" and by["Acme"]["source"] == "email" and by["Acme"]["applied_on"] == "2026-11-01"
    assert by["Globex"]["status"] == "interview" and by["Globex"]["role"] == "Cloud Engineer"
    with get_conn() as conn:
        assert conn.execute("SELECT 1 FROM settings WHERE key='last_sync_email'").fetchone()


def test_status_rules(imap_env):
    sender = "Globex <hr@globex.com>"
    run([mail(sender, "Application for the Dev role", "thank you for applying", 1)])
    # rejection overrides, later "applied" cannot downgrade
    _, res, apps = run([mail(sender, "Application for the Dev role", "Unfortunately we are not moving forward", 2),
                        mail(sender, "Application for the Dev role", "thank you for applying", 3)])
    assert apps[0]["status"] == "rejected"
    # manual withdrawn is untouched
    with get_conn() as conn:
        conn.execute("UPDATE applications SET status='withdrawn'")
    _, _, apps = run([mail(sender, "Application for the Dev role", "interview", 4)])
    assert apps[0]["status"] == "withdrawn"


def test_connection_error_and_unconfigured(settings_env, monkeypatch):
    for k in ("IMAP_HOST", "IMAP_USER", "IMAP_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    from app.config import get_settings
    get_settings.cache_clear()
    with get_conn() as conn, pytest.raises(SyncError, match="not configured"):
        sync_email(conn)


def test_confirmation_mentioning_interview_stays_applied(imap_env):
    body = "Thanks for applying! If your background is a fit, a recruiter will reach out about an interview and next steps."
    fake, res, apps = run([mail("Initech Careers <jobs@initech.com>", "Thank you for applying", body, 3)])
    assert [a["status"] for a in apps] == ["applied"]
    assert fake.fetch_calls == 1


def test_full_lookback(imap_env):
    from datetime import timedelta
    from app.config import get_settings
    cfg = get_settings().config["career"]["email"]
    seen = []

    def run(**kw):
        f = FakeIMAP([])
        with get_conn() as conn:
            sync_email(conn, imap_factory=lambda: f, today=TODAY, **kw)
        seen.append(f.criteria[1])

    fmt = lambda d: f"{d.day:02d}-{['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][d.month - 1]}-{d.year}"  # noqa: E731
    run()  # first sync -> initial lookback
    run()  # later -> normal
    run(full=True)
    assert seen == [fmt(TODAY - timedelta(days=cfg["initial_lookback_days"])), fmt(TODAY - timedelta(days=cfg["lookback_days"])),
                    fmt(TODAY - timedelta(days=cfg["initial_lookback_days"]))]
