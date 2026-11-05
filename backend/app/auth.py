"""Session login for the app: scrypt password check + HMAC-signed cookie.

Env (read at request time): DASH_AUTH (default 1), DASH_PASSWORD_HASH, DASH_SECRET_KEY, DASH_COOKIE_SECURE (default 1).
CLI: `python -m app.auth hash` / `python -m app.auth secret`.
"""
from __future__ import annotations

import base64
import binascii
import getpass
import hashlib
import hmac
import mimetypes
import os
import secrets
import sys
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse

# StaticFiles uses mimetypes; make sure the PWA manifest gets the right type.
mimetypes.add_type("application/manifest+json", ".webmanifest")

COOKIE = "dash_session"
MAX_AGE = 30 * 24 * 3600
LOGIN_PAGE = "/login.html"
PUBLIC_EXACT = {"/login.html", "/login.js", "/styles.css", "/manifest.webmanifest", "/sw.js", "/api/health"}
PUBLIC_PREFIX = ("/icons/", "/api/auth/")
RATE_LIMIT, RATE_WINDOW = 5, 15 * 60
GLOBAL_LIMIT = 30  # failed logins from all IPs combined per window: blunts distributed guessing
MAX_TRACKED_IPS = 10_000
NOT_CONFIGURED = "Login not configured: set DASH_PASSWORD_HASH and DASH_SECRET_KEY"

_failures: dict[str, list[float]] = {}


# ---------- password hashing ----------
def hash_password(password: str, n: int = 2**15, r: int = 8, p: int = 1) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, maxmem=128 * 1024 * 1024, dklen=32)
    b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
    return f"scrypt${n}${r}${p}${b64(salt)}${b64(dk)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_b64, hash_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        salt, want = base64.b64decode(salt_b64), base64.b64decode(hash_b64)
        n, r, p = int(n), int(r), int(p)
        got = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, maxmem=128 * 1024 * 1024, dklen=len(want))
    except (ValueError, TypeError, binascii.Error):
        return False
    return hmac.compare_digest(got, want)


# ---------- session cookie ----------
def _sign(payload: str, key: str) -> str:
    return hmac.new(key.encode(), payload.encode(), hashlib.sha256).hexdigest()


def make_cookie(key: str, now: float | None = None) -> str:
    expiry = int((now if now is not None else time.time()) + MAX_AGE)
    payload = base64.urlsafe_b64encode(str(expiry).encode()).decode().rstrip("=")
    return payload + "." + _sign(payload, key)


def valid_cookie(value: str | None, key: str) -> bool:
    if not value or "." not in value:
        return False
    payload, sig = value.rsplit(".", 1)
    if not hmac.compare_digest(sig, _sign(payload, key)):
        return False
    try:
        expiry = int(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)).decode())
    except (ValueError, binascii.Error):
        return False
    return expiry > time.time()


# ---------- helpers ----------
def _enabled() -> bool:
    return os.getenv("DASH_AUTH", "1") != "0"


def _configured() -> bool:
    return bool(os.getenv("DASH_PASSWORD_HASH")) and bool(os.getenv("DASH_SECRET_KEY"))


def _is_public(path: str) -> bool:
    return path in PUBLIC_EXACT or path.startswith(PUBLIC_PREFIX)


def _client(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _recent_failures(ip: str) -> list[float]:
    cutoff = time.time() - RATE_WINDOW
    lst = [t for t in _failures.get(ip, []) if t > cutoff]
    if lst:
        _failures[ip] = lst
    else:
        _failures.pop(ip, None)
    return lst


def _global_failures() -> int:
    cutoff = time.time() - RATE_WINDOW
    if len(_failures) > MAX_TRACKED_IPS:  # bound memory under a flood of distinct IPs
        for ip in [k for k, v in _failures.items() if not v or v[-1] <= cutoff]:
            _failures.pop(ip, None)
    return sum(1 for v in _failures.values() for t in v if t > cutoff)


def install_auth(app: FastAPI) -> None:
    """Register login routes and the session-check middleware."""

    @app.post("/api/auth/login")
    async def login(request: Request):
        if not _enabled():
            return {"ok": True}
        if not _configured():
            return JSONResponse({"detail": NOT_CONFIGURED}, status_code=503)
        ip = _client(request)
        if len(_recent_failures(ip)) >= RATE_LIMIT or _global_failures() >= GLOBAL_LIMIT:
            return JSONResponse({"detail": "Too many failed attempts. Try again in 15 minutes."}, status_code=429)
        try:
            body = await request.json()
            password = body.get("password") if isinstance(body, dict) else None
        except ValueError:
            password = None
        if not isinstance(password, str) or not verify_password(password, os.environ["DASH_PASSWORD_HASH"]):
            _failures.setdefault(ip, []).append(time.time())
            return JSONResponse({"detail": "Wrong password"}, status_code=401)
        _failures.pop(ip, None)
        resp = JSONResponse({"ok": True})
        resp.set_cookie(
            COOKIE, make_cookie(os.environ["DASH_SECRET_KEY"]), max_age=MAX_AGE, path="/", httponly=True,
            samesite="lax", secure=os.getenv("DASH_COOKIE_SECURE", "1") != "0",
        )
        return resp

    @app.post("/api/auth/logout")
    async def logout():
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(COOKIE, path="/")
        return resp

    @app.get("/api/auth/me")
    async def me(request: Request):
        if not _enabled():
            return {"authenticated": True, "auth": False}
        if not _configured():
            return JSONResponse({"detail": NOT_CONFIGURED}, status_code=503)
        if valid_cookie(request.cookies.get(COOKIE), os.environ["DASH_SECRET_KEY"]):
            return {"authenticated": True, "auth": True}
        return JSONResponse({"detail": "Not signed in"}, status_code=401)

    @app.middleware("http")
    async def session_guard(request: Request, call_next):
        if not _enabled():
            return await call_next(request)
        path = request.url.path
        if ".." in path or "\\" in path:  # no traversal tricks around the public-path allowlist
            return JSONResponse({"detail": "Bad path"}, status_code=400)
        is_api = path.startswith("/api/")
        # CSRF guard: state-changing API calls must carry a custom header (not sendable cross-site without CORS).
        if is_api and request.method not in ("GET", "HEAD", "OPTIONS") and path != "/api/auth/login":
            if request.headers.get("x-dash") != "1":
                return JSONResponse({"detail": "Missing X-Dash header"}, status_code=403)
        if _is_public(path):
            return await call_next(request)
        if not _configured():
            return JSONResponse({"detail": NOT_CONFIGURED}, status_code=503)
        if not valid_cookie(request.cookies.get(COOKIE), os.environ["DASH_SECRET_KEY"]):
            if is_api:
                return JSONResponse({"detail": "Not signed in"}, status_code=401)
            return RedirectResponse(LOGIN_PAGE, status_code=302)
        return await call_next(request)


def _main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "hash":
        a = getpass.getpass("Password: ")
        if a != getpass.getpass("Repeat: "):
            print("Passwords differ.", file=sys.stderr)
            return 1
        if len(a) < 8:
            print("Use at least 8 characters.", file=sys.stderr)
            return 1
        print(hash_password(a))
        return 0
    if cmd == "secret":
        print(secrets.token_urlsafe(48))
        return 0
    print("usage: python -m app.auth hash|secret", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
