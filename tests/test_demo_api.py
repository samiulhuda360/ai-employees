"""HQ API in demo mode: the guard rails (login, CSRF, allowlist) and the loops that change files."""

from __future__ import annotations

import csv
import json

from fastapi.testclient import TestClient


def test_login_needs_password_then_code(hq):
    c = TestClient(hq.app, base_url="https://testserver")
    assert c.get("/api/overview").status_code == 401
    assert c.post("/api/login", json={"password": "wrong"}).status_code == 401
    r = c.post("/api/login", json={"password": "demo"}).json()
    assert c.post("/api/verify", json={"challenge": r["challenge"], "code": "000000" if r["demo_code"] != "000000" else "111111"}).status_code == 401
    assert c.post("/api/verify", json={"challenge": r["challenge"], "code": r["demo_code"]}).status_code == 200
    assert c.get("/api/overview").status_code == 200


def test_login_is_rate_limited(hq):
    c = TestClient(hq.app, base_url="https://testserver")
    with hq.db() as con:
        con.execute("DELETE FROM login_attempts")
    codes = [c.post("/api/login", json={"password": "wrong"}).status_code for _ in range(11)]
    assert codes[:10] == [401] * 10 and codes[10] == 429
    with hq.db() as con:
        con.execute("DELETE FROM login_attempts")


def test_writes_need_the_csrf_header(client):
    client.headers.pop("x-hq")
    assert client.post("/api/fleet/pause").status_code == 403
    assert client.get("/api/overview").status_code == 200


def test_overview_has_every_agent_and_a_late_one(client):
    o = client.get("/api/overview").json()
    states = {a["id"]: a["state"] for a in o["agents"]}
    assert len(states) == 9 and states["youtube-watcher"] == "late"
    assert o["priority"]["p1"] > 0


def test_ask_actions_pass_the_allowlist(client, hq, monkeypatch):
    for said, expected in [("open prospects", {"type": "navigate", "path": "/prospects"}),
                           ("run blog planner", {"type": "run_job", "agent": "blog-planner"})]:
        assert client.post("/api/ask", json={"text": said}).json()["action"] == expected
    # Whatever the model says, HQ drops actions outside the allowlist.
    bad = [{"type": "navigate", "path": "/../../etc"}, {"type": "run_job", "agent": "code-health"},  # PC agent
           {"type": "idea_status", "idea_id": 1, "status": "deleted"}, {"type": "send_email", "to": "x"}]
    for action in bad:
        monkeypatch.setattr(hq.demo, "ask", lambda *a, act=action: json.dumps({"say": "ok", "action": act}))
        assert client.post("/api/ask", json={"text": "do it"}).json()["action"] is None


def test_pause_and_resume_a_job(client, demo_home):
    job = client.get("/api/agents/social-planner").json()["jobs"][0]
    assert client.post(f"/api/jobs/{job['id']}/pause").status_code == 200
    jobs = json.loads((demo_home / ".hermes/profiles/social-planner/cron/jobs.json").read_text(encoding="utf-8"))["jobs"]
    assert next(j for j in jobs if j["id"] == job["id"])["state"] == "paused"
    assert next(j for j in client.get("/api/agents/social-planner").json()["jobs"] if j["id"] == job["id"])["state"] == "paused"
    client.post(f"/api/jobs/{job['id']}/resume")
    assert next(j for j in client.get("/api/agents/social-planner").json()["jobs"] if j["id"] == job["id"])["state"] == "scheduled"
    assert [a["action"] for a in client.get("/api/audit").json()["audit"][:2]] == ["job.resume", "job.pause"]


def test_pc_jobs_cannot_be_controlled_from_hq(client):
    job = client.get("/api/agents/code-health").json()["jobs"][0]
    assert client.post(f"/api/jobs/{job['id']}/run").status_code == 400


def test_bad_schedule_is_refused(client):
    job = client.get("/api/agents/blog-planner").json()["jobs"][0]
    assert client.post(f"/api/jobs/{job['id']}/schedule", json={"schedule": "rm -rf /"}).status_code == 400


def test_emergency_stop(client, demo_home):
    client.post("/api/fleet/pause")
    assert (demo_home / ".hermes" / "ESTOP").exists() and client.get("/api/overview").json()["fleet_paused"]
    client.post("/api/fleet/resume")
    assert not client.get("/api/overview").json()["fleet_paused"]


def test_approving_a_proposal_writes_the_rule_and_rejecting_removes_it(client, demo_home):
    prop = next(i for i in client.get("/api/ideas?type=proposal").json()["ideas"] if i["agent"] == "social-planner")
    soul = demo_home / ".hermes/profiles/social-planner/SOUL.md"
    tag = f"[hq-{prop['id']}]"
    client.post(f"/api/ideas/{prop['id']}", json={"status": "approved"})
    assert tag in soul.read_text(encoding="utf-8")
    assert any(tag[1:-1] in r or prop["title"] in r for r in client.get("/api/agents/social-planner").json()["brain"]["rules you approved"])
    client.post(f"/api/ideas/{prop['id']}", json={"status": "rejected"})
    assert tag not in soul.read_text(encoding="utf-8")
    assert list(soul.parent.glob("SOUL.md.bak-*")), "a backup is kept before every change"


def test_prospect_status_syncs_to_the_agents_ledger(client, demo_home):
    cid = client.get("/api/prospects").json()["prospects"][0]["cid"]
    client.post(f"/api/prospects/{cid}", json={"status": "won"})
    with (demo_home / "agents/prospect-finder/prospects.csv").open(encoding="utf-8") as h:
        assert next(r for r in csv.DictReader(h) if r["cid"] == cid)["status"] == "won"


def test_journal_log(client, demo_home):
    client.post("/api/me/log", json={"kind": "done", "text": "wrote the tests"})
    assert "wrote the tests" in (demo_home / "agents/chief-assistant/journal.md").read_text(encoding="utf-8")
    assert client.post("/api/me/log", json={"kind": "other", "text": "x"}).status_code == 400
