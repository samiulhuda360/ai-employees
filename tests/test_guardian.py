"""The guardian: no model, silent when the fleet is healthy, one line per problem otherwise."""

from __future__ import annotations

import importlib
import sqlite3
import sys
from datetime import datetime, timedelta

import pytest
from conftest import ROOT

sys.path.insert(0, str(ROOT / "employees" / "claudia" / "scripts"))


@pytest.fixture()
def guard(tmp_path, monkeypatch):
    monkeypatch.setenv("HQ_HOME", str(tmp_path))
    import hq_ingest
    importlib.reload(hq_ingest)
    hq_ingest.connect().close()  # empty hq.db with the real schema
    import fleet_guard
    importlib.reload(fleet_guard)
    # Not on a server: both services report active and the disk has room.
    monkeypatch.setattr(fleet_guard.subprocess, "run", lambda *a, **k: type("R", (), {"stdout": "active"})())
    monkeypatch.setattr(fleet_guard.shutil, "disk_usage", lambda _p: type("U", (), {"used": 1, "total": 10, "free": 9 * 2**30})())
    con = sqlite3.connect(tmp_path / "hq" / "hq.db")
    con.execute("INSERT INTO meta(key, value) VALUES('last_ingest', ?)", (datetime.now().isoformat(),))
    con.commit()
    yield fleet_guard, con
    con.close()


def test_silent_when_healthy(guard, capsys):
    fleet_guard, _ = guard
    assert fleet_guard.check() == []
    fleet_guard.main()
    assert capsys.readouterr().out == ""  # empty output: Claudia sends nothing


def test_reports_failures_backup_model_missed_jobs_and_stalls(guard):
    fleet_guard, con = guard
    now = datetime.now()
    con.execute("INSERT INTO runs(agent, job_name, path, started_at, status, used_fallback) VALUES"
                "('blog-planner', 'Daily blog ideas', 'a', ?, 'failed', 0), ('social-planner', 'Daily social ideas', 'b', ?, 'ok', 1)",
                ((now - timedelta(hours=2)).isoformat(), (now - timedelta(hours=3)).isoformat()))
    con.execute("INSERT INTO jobs(id, agent, name, state, next_run_at) VALUES('j1', 'growth-scout', 'Daily radar', 'scheduled', ?)",
                ((now - timedelta(hours=5)).isoformat(),))
    con.execute("UPDATE meta SET value=? WHERE key='last_ingest'", ((now - timedelta(hours=2)).isoformat(),))
    con.commit()
    found = " | ".join(fleet_guard.check())
    for expected in ("FAILED x1: blog-planner / Daily blog ideas", "BACKUP MODEL: 1 run(s)", "MISSED: growth-scout / Daily radar",
                     "HQ INGEST STALLED"):
        assert expected in found
