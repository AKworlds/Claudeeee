import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def settings_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DASH_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DASH_CONFIG", str(tmp_path / "none.yaml"))
    monkeypatch.setenv("DASH_SCHEDULER", "0")
    monkeypatch.setenv("DASH_AUTH", "0")  # auth has its own tests
    from app.config import get_settings
    get_settings.cache_clear()
    from app.db import init_db
    init_db()
    yield tmp_path
    get_settings.cache_clear()


@pytest.fixture()
def client(settings_env):
    from app.main import create_app
    with TestClient(create_app()) as c:
        yield c
