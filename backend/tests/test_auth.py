import base64
import time

import pytest

from app import auth

PASSWORD = "correct horse battery"
H = {"X-Dash": "1"}


@pytest.fixture()
def secured(client, monkeypatch):
    auth._failures.clear()
    monkeypatch.setenv("DASH_AUTH", "1")
    monkeypatch.setenv("DASH_COOKIE_SECURE", "0")
    monkeypatch.setenv("DASH_PASSWORD_HASH", auth.hash_password(PASSWORD, n=2**10))
    monkeypatch.setenv("DASH_SECRET_KEY", "k" * 40)
    client.follow_redirects = False
    yield client
    auth._failures.clear()


def login(c, pw=PASSWORD):
    return c.post("/api/auth/login", json={"password": pw}, headers=H)


def test_unconfigured_fails_closed(client, monkeypatch):
    monkeypatch.setenv("DASH_AUTH", "1")
    monkeypatch.delenv("DASH_PASSWORD_HASH", raising=False)
    monkeypatch.delenv("DASH_SECRET_KEY", raising=False)
    r = client.get("/api/finance/summary")
    assert r.status_code == 503 and "Login not configured" in r.json()["detail"]
    assert client.get("/api/health").status_code == 200


def test_wrong_password_401(secured):
    assert login(secured, "nope").status_code == 401


def test_rate_limit(secured):
    for _ in range(5):
        assert login(secured, "nope").status_code == 401
    assert login(secured, "nope").status_code == 429
    assert login(secured).status_code == 429


def test_login_unlocks_api(secured):
    assert secured.get("/api/finance/summary").status_code == 401
    r = login(secured)
    assert r.status_code == 200
    assert "httponly" in r.headers["set-cookie"].lower() and "samesite=lax" in r.headers["set-cookie"].lower()
    assert secured.get("/api/finance/summary").status_code == 200
    assert secured.get("/api/auth/me").status_code == 200


def test_page_redirects_to_login(secured):
    r = secured.get("/")
    assert r.status_code == 302 and r.headers["location"] == "/login.html"


def test_tampered_and_expired_cookie(secured):
    key = "k" * 40
    for bad in ("garbage", auth.make_cookie(key)[:-2] + "00", auth.make_cookie("other" * 10),
                auth.make_cookie(key, now=time.time() - auth.MAX_AGE - 10)):
        secured.cookies.clear()
        secured.cookies.set("dash_session", bad)
        assert secured.get("/api/finance/summary").status_code == 401
    forged = base64.urlsafe_b64encode(str(int(time.time()) + 9999).encode()).decode().rstrip("=") + ".abc"
    secured.cookies.set("dash_session", forged)
    assert secured.get("/api/finance/summary").status_code == 401


def test_missing_xdash_is_403(secured):
    login(secured)
    r = secured.post("/api/auth/logout")
    assert r.status_code == 403
    r = secured.post("/api/finance/milestones", json={"name": "x", "kind": "custom", "metric": "custom", "target": 1})
    assert r.status_code == 403
    r = secured.post("/api/finance/milestones", json={"name": "x", "kind": "custom", "metric": "custom", "target": 1}, headers=H)
    assert r.status_code != 403


def test_public_paths(secured):
    for p in ("/login.html", "/manifest.webmanifest", "/sw.js", "/api/health", "/styles.css", "/login.js"):
        assert secured.get(p).status_code in (200, 404), p
        assert secured.get(p).status_code != 302 and secured.get(p).status_code != 401, p
    r = secured.get("/manifest.webmanifest")
    if r.status_code == 200:
        assert r.headers["content-type"].startswith("application/manifest+json")


def test_logout_clears(secured):
    login(secured)
    assert secured.get("/api/finance/summary").status_code == 200
    assert secured.post("/api/auth/logout", headers=H).status_code == 200
    assert secured.get("/api/finance/summary").status_code == 401


def test_hash_format_roundtrip():
    h = auth.hash_password("abc12345", n=2**10)
    assert h.startswith("scrypt$1024$8$1$")
    assert auth.verify_password("abc12345", h) and not auth.verify_password("x", h)
    assert not auth.verify_password("x", "garbage")


def test_global_failure_cap_and_traversal(secured):
    import app.auth as auth
    import time
    now = time.time()
    for i in range(auth.GLOBAL_LIMIT):  # many different IPs each failing once
        auth._failures[f"10.0.{i // 250}.{i % 250}"] = [now]
    r = secured.post("/api/auth/login", json={"password": "anything"})
    assert r.status_code == 429
    auth._failures.clear()
    assert secured.get("/icons/..%2Findex.html").status_code == 400  # httpx would normalize a literal ../
