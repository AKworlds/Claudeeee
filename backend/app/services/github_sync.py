"""Sync public GitHub repos into the projects table."""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

import httpx

from ..config import get_settings
from .errors import SyncError

API = "https://api.github.com"
STALE_DAYS = 90



def _fetch_repos(client: httpx.Client, user: str, token: str | None) -> list[dict]:
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url: str | None = f"{API}/users/{user}/repos"
    params: dict | None = {"per_page": 100, "sort": "pushed"}
    repos: list[dict] = []
    while url:
        try:
            r = client.get(url, params=params, headers=headers, timeout=15)
        except httpx.HTTPError as e:
            raise SyncError(f"GitHub request failed: {e}") from e
        if r.status_code == 404:
            raise SyncError(f"GitHub user '{user}' not found")
        if r.status_code in (401, 403):
            raise SyncError(f"GitHub API refused the request ({r.status_code}): bad token or rate limit")
        if r.status_code != 200:
            raise SyncError(f"GitHub API error {r.status_code}")
        repos.extend(r.json())
        m = re.search(r'<([^>]+)>;\s*rel="next"', r.headers.get("link", ""))
        url, params = (m.group(1) if m else None), None
    return repos


def sync_github(conn, client: httpx.Client | None = None) -> dict:
    """Upsert the configured user's non-fork repos as projects; returns {added, updated, message}."""
    s = get_settings()
    user = s.config["career"]["github_user"]
    own = client or httpx.Client()
    try:
        repos = _fetch_repos(own, user, s.github_token)
    finally:
        if client is None:
            own.close()
    now = datetime.now(timezone.utc)
    added = updated = 0
    for repo in repos:
        if repo.get("fork") or repo["name"].lower() == user.lower():
            continue
        ext = f"github:{repo['id']}"
        pushed = repo.get("pushed_at")
        vals = (repo["name"], repo.get("description") or "", repo["html_url"],
                repo.get("stargazers_count", 0), repo.get("language") or "", pushed)
        if conn.execute("SELECT 1 FROM projects WHERE external_id=?", (ext,)).fetchone():
            conn.execute("UPDATE projects SET name=?, description=?, url=?, stars=?, language=?, last_push=? "
                         "WHERE external_id=?", (*vals, ext))
            updated += 1
        else:
            stale = True
            if pushed:
                stale = now - datetime.fromisoformat(pushed.replace("Z", "+00:00")) > timedelta(days=STALE_DAYS)
            conn.execute("INSERT INTO projects(name, description, url, stars, language, last_push, status, source, external_id) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?, 'github', ?)",
                         (*vals, "done" if stale else "building", ext))
            added += 1
    conn.execute("INSERT OR REPLACE INTO settings(key, value) VALUES ('last_sync_github', ?)", (json.dumps(now.isoformat()),))
    return {"added": added, "updated": updated, "message": f"GitHub: {added} added, {updated} updated"}
