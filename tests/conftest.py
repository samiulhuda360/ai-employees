from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "hq" / "server"
sys.path.insert(0, str(SERVER))


@pytest.fixture(scope="session")
def demo_home(tmp_path_factory) -> Path:
    """A fresh demo home (fake fleet files + hq.db built by the real ingest)."""
    home = tmp_path_factory.mktemp("demo") / "home"
    subprocess.run([sys.executable, str(ROOT / "hq" / "demo" / "seed.py"), "--out", str(home)], check=True,
                   capture_output=True)
    return home


@pytest.fixture()
def hq(demo_home, monkeypatch):
    """hq_api loaded in demo mode against the demo home. Job changes re-ingest synchronously."""
    monkeypatch.setenv("HQ_HOME", str(demo_home))
    monkeypatch.setenv("HQ_DEMO", "1")
    import hq_ingest
    import learnings
    import hq_api
    for mod in (hq_ingest, learnings, hq_api):
        importlib.reload(mod)
    monkeypatch.setattr(hq_api, "refresh_jobs", hq_ingest.main)
    return hq_api


@pytest.fixture()
def client(hq):
    from fastapi.testclient import TestClient
    c = TestClient(hq.app, base_url="https://testserver")
    with hq.db() as con:  # every test logs in from the same address; start under the rate limit
        con.execute("DELETE FROM login_attempts")
    r = c.post("/api/login", json={"password": "demo"}).json()
    assert c.post("/api/verify", json={"challenge": r["challenge"], "code": r["demo_code"]}).status_code == 200
    c.headers["x-hq"] = "1"
    return c
