import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.db import get_conn
from app.services.github_sync import SyncError, sync_github


def iso(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def repo(id_, name, days=1, fork=False, stars=0):
    return {"id": id_, "name": name, "fork": fork, "html_url": f"https://github.com/AKworlds/{name}",
            "description": f"{name} desc", "stargazers_count": stars, "language": "Python", "pushed_at": iso(days)}


def make_client(pages, seen=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        page = int(request.url.params.get("page", 1))
        headers = {}
        if page < len(pages):
            headers["Link"] = f'<https://api.github.com/users/AKworlds/repos?page={page + 1}>; rel="next"'
        return httpx.Response(200, json=pages[page - 1], headers=headers)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_sync_upsert(settings_env, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    from app.config import get_settings
    get_settings.cache_clear()
    seen = []
    pages = [[repo(1, "fresh"), repo(2, "forked", fork=True)], [repo(3, "old", days=200), repo(4, "AKworlds")]]
    with get_conn() as conn:
        res = sync_github(conn, make_client(pages, seen))
        assert (res["added"], res["updated"]) == (2, 0)
        assert seen[0].headers["authorization"] == "Bearer tok" and len(seen) == 2
        st = {r["name"]: r["status"] for r in conn.execute("SELECT name, status FROM projects")}
        assert st == {"fresh": "building", "old": "done"}
        conn.execute("UPDATE projects SET status='idea', featured=1 WHERE name='fresh'")
        pages[0][0]["stargazers_count"] = 9
        res = sync_github(conn, make_client(pages))
        assert (res["added"], res["updated"]) == (0, 2)
        r = conn.execute("SELECT * FROM projects WHERE name='fresh'").fetchone()
        assert (r["status"], r["featured"], r["stars"], r["source"]) == ("idea", 1, 9, "github")
        assert json.loads(conn.execute("SELECT value FROM settings WHERE key='last_sync_github'").fetchone()[0])


def test_sync_errors(settings_env):
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    with get_conn() as conn, pytest.raises(SyncError, match="not found"):
        sync_github(conn, client)
