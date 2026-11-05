from datetime import date, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture()
def api(settings_env):
    from app.routers import career
    app = FastAPI()
    app.include_router(career.router, prefix="/api/career")
    with TestClient(app) as c:
        yield c


def test_application_crud_and_validation(api):
    r = api.post("/api/career/applications", json={"company": "Acme", "role": "SRE"})
    assert r.status_code == 201
    app = r.json()
    assert app["status"] == "applied" and app["applied_on"] == date.today().isoformat()
    assert api.post("/api/career/applications", json={"company": "X", "status": "bogus"}).status_code == 422
    assert api.post("/api/career/applications", json={"company": "X", "applied_on": "nope"}).status_code == 422
    assert api.post("/api/career/applications", json={"role": "no company"}).status_code == 422
    u = api.put(f"/api/career/applications/{app['id']}", json={"status": "interview"}).json()
    assert u["status"] == "interview" and u["role"] == "SRE" and u["last_update"] == date.today().isoformat()
    assert len(api.get("/api/career/applications").json()) == 1
    assert api.delete(f"/api/career/applications/{app['id']}").status_code == 200
    assert api.put("/api/career/applications/999", json={}).status_code == 404
    assert api.delete("/api/career/applications/999").status_code == 404


def test_funnel_and_summary(api):
    today = date.today()
    for status, days in [("applied", 0), ("interview", 1), ("rejected", 2), ("wishlist", 0), ("offer", 100)]:
        api.post("/api/career/applications", json={
            "company": status, "status": status, "applied_on": (today - timedelta(days=days)).isoformat()})
    f = api.get("/api/career/applications/funnel").json()
    assert [s["status"] for s in f["stages"]] == ["wishlist", "applied", "interview", "offer", "rejected", "withdrawn"]
    assert {s["status"]: s["count"] for s in f["stages"]}["rejected"] == 1
    assert len(f["weekly"]) == 12
    assert sum(w["applied"] for w in f["weekly"]) == 4  # the 100-day-old one is out of range
    assert all(date.fromisoformat(w["week"]).weekday() == 0 for w in f["weekly"])
    s = api.get("/api/career/summary").json()
    assert s["applications_total"] == 5 and s["active"] == 2 and s["interviews"] == 1 and s["offers"] == 1
    assert s["response_rate"] == 0.75
    assert s["last_sync"] == {"github": None, "email": None}


def test_summary_empty(api):
    s = api.get("/api/career/summary").json()
    assert s["response_rate"] == 0 and s["applications_total"] == 0


def test_certifications_seed_once(api):
    certs = api.get("/api/career/certifications").json()
    assert len(certs) == 6 and all(c["status"] == "earned" for c in certs)
    for c in certs:
        api.delete(f"/api/career/certifications/{c['id']}")
    assert api.get("/api/career/certifications").json() == []  # not re-seeded
    r = api.post("/api/career/certifications", json={"name": "CKA", "status": "in_progress", "target_date": "2027-01-01"})
    assert r.status_code == 201
    assert api.post("/api/career/certifications", json={"name": "x", "status": "nope"}).status_code == 422
    assert api.put(f"/api/career/certifications/{r.json()['id']}", json={"status": "earned"}).json()["status"] == "earned"
    assert api.get("/api/career/summary").json()["certs_earned"] == 1


def test_projects_crud(api):
    p = api.post("/api/career/projects", json={"name": "dash", "featured": True}).json()
    assert p["featured"] is True and p["status"] == "idea"
    u = api.put(f"/api/career/projects/{p['id']}", json={"status": "done", "stars": 3}).json()
    assert u["status"] == "done" and u["featured"] is True and u["stars"] == 3
    assert api.put(f"/api/career/projects/{p['id']}", json={"status": "bad"}).status_code == 422
    assert api.get("/api/career/projects").json()[0]["name"] == "dash"
    assert api.delete(f"/api/career/projects/{p['id']}").status_code == 200
    assert api.delete(f"/api/career/projects/{p['id']}").status_code == 404


def test_email_sync_unconfigured(api, monkeypatch):
    for k in ("IMAP_HOST", "IMAP_USER", "IMAP_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    from app.config import get_settings
    get_settings.cache_clear()
    r = api.post("/api/career/sync/email")
    assert r.status_code == 400 and "IMAP" in r.json()["detail"]
