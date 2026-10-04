"""Hermes HQ API: the back end of the fleet dashboard.

FastAPI on 127.0.0.1:8787, behind nginx. Reads hq.db (filled by hq_ingest.py) and
the live Hermes files; changes things only through an allowlist of Hermes CLI
commands, each written to the audit table.

Login: password (hash in ~/hq/.env) + a 6-digit code Claudia sends to the owner's
Telegram with `hermes send`, so this service never handles the bot token.
"""

from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

import demo
import hq_priority

# HQ_HOME points HQ at another home folder (the demo data). HQ_DEMO=1 replaces Telegram, the
# Hermes CLI and the model with local stand-ins (demo.py), so the dashboard runs anywhere.
HOME = Path(os.environ.get("HQ_HOME") or Path.home())
DEMO = os.environ.get("HQ_DEMO") == "1"
HQ = HOME / "hq"
DB = HQ / "hq.db"
ENV = HQ / ".env"
PROFILES = HOME / ".hermes" / "profiles"
AGENTS_DIR = HOME / "agents"
HERMES = str(HOME / ".local" / "bin" / "hermes")
SESSION_DAYS = 30
CODE_TTL = 300

app = FastAPI(title="Hermes HQ", docs_url=None, redoc_url=None, openapi_url=None)


# ------------------------------------------------------------------ config + db

def env() -> dict:
    out = {}
    if ENV.exists():
        for line in ENV.read_text(encoding="utf-8").splitlines():
            k, _, v = line.partition("=")
            if k.strip():
                out[k.strip()] = v.strip()
    return out


def db() -> sqlite3.Connection:
    con = sqlite3.connect(DB, timeout=10)
    con.row_factory = sqlite3.Row
    con.executescript("""
      CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, created_at REAL, expires_at REAL, ip TEXT);
      CREATE TABLE IF NOT EXISTS login_codes (id TEXT PRIMARY KEY, code_hash TEXT, expires_at REAL, tries INTEGER DEFAULT 0);
      CREATE TABLE IF NOT EXISTS login_attempts (ip TEXT, at REAL);
      CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY, at TEXT, who TEXT, action TEXT, target TEXT, args TEXT, result TEXT);
    """)
    return con


def rows(sql: str, args: tuple = ()) -> list[dict]:
    with db() as con:
        return [dict(r) for r in con.execute(sql, args)]


def audit(action: str, target: str, args: str, result: str) -> None:
    with db() as con:
        con.execute("INSERT INTO audit(at,who,action,target,args,result) VALUES(?,?,?,?,?,?)",
                    (datetime.now().astimezone().isoformat(timespec="seconds"), "owner", action, target, args, result[:500]))


# ------------------------------------------------------------------ auth

def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def check_password(given: str) -> bool:
    if DEMO:
        return hmac.compare_digest(given, "demo")
    stored = env().get("HQ_PASSWORD_HASH", "")  # pbkdf2$<iterations>$<salt hex>$<hash hex>
    try:
        _, iters, salt, digest = stored.split("$")
        test = hashlib.pbkdf2_hmac("sha256", given.encode(), bytes.fromhex(salt), int(iters)).hex()
        return hmac.compare_digest(test, digest)
    except ValueError:
        return False


def rate_limited(ip: str) -> bool:
    with db() as con:
        con.execute("DELETE FROM login_attempts WHERE at < ?", (time.time() - 900,))
        n = con.execute("SELECT COUNT(*) FROM login_attempts WHERE ip=?", (ip,)).fetchone()[0]
        con.execute("INSERT INTO login_attempts(ip,at) VALUES(?,?)", (ip, time.time()))
    return n >= 10


def client_ip(req: Request) -> str:
    return req.headers.get("x-real-ip") or (req.client.host if req.client else "?")


def require_session(req: Request) -> None:
    token = req.cookies.get("hq_session", "")
    if not token:
        raise HTTPException(401, "login required")
    with db() as con:
        row = con.execute("SELECT expires_at FROM sessions WHERE token_hash=?", (sha(token),)).fetchone()
    if not row or row[0] < time.time():
        raise HTTPException(401, "session expired")
    # CSRF: every write must carry the header the front end sets; browsers cannot forge it cross-site.
    if req.method not in ("GET", "HEAD") and req.headers.get("x-hq") != "1":
        raise HTTPException(403, "missing header")


class LoginIn(BaseModel):
    password: str


class CodeIn(BaseModel):
    challenge: str
    code: str


@app.post("/api/login")
def login(body: LoginIn, req: Request):
    ip = client_ip(req)
    if rate_limited(ip):
        raise HTTPException(429, "Too many attempts. Wait 15 minutes.")
    if not check_password(body.password):
        time.sleep(1)
        raise HTTPException(401, "Wrong password")
    code = f"{secrets.randbelow(1_000_000):06d}"
    challenge = secrets.token_urlsafe(16)
    with db() as con:
        con.execute("INSERT INTO login_codes(id,code_hash,expires_at) VALUES(?,?,?)",
                    (challenge, sha(code), time.time() + CODE_TTL))
    if DEMO:  # no Telegram in demo mode: the login screen shows the code
        return {"challenge": challenge, "demo_code": code}
    msg = f"Hermes HQ login code: {code}\nValid 5 minutes. If this was not you, ignore it and tell Claudia."
    r = subprocess.run([HERMES, "-p", "chief-assistant", "send", "--to", "telegram", msg],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise HTTPException(502, "Could not send the Telegram code")
    return {"challenge": challenge}


@app.post("/api/verify")
def verify(body: CodeIn, req: Request, resp: Response):
    with db() as con:
        row = con.execute("SELECT code_hash, expires_at, tries FROM login_codes WHERE id=?", (body.challenge,)).fetchone()
        if not row or row[1] < time.time() or row[2] >= 5:
            raise HTTPException(401, "Code expired. Log in again.")
        con.execute("UPDATE login_codes SET tries=tries+1 WHERE id=?", (body.challenge,))
        if not hmac.compare_digest(sha(body.code.strip()), row[0]):
            raise HTTPException(401, "Wrong code")
        con.execute("DELETE FROM login_codes WHERE id=?", (body.challenge,))
        token = secrets.token_urlsafe(32)
        con.execute("INSERT INTO sessions(token_hash,created_at,expires_at,ip) VALUES(?,?,?,?)",
                    (sha(token), time.time(), time.time() + SESSION_DAYS * 86400, client_ip(req)))
    resp.set_cookie("hq_session", token, max_age=SESSION_DAYS * 86400, httponly=True, secure=True, samesite="strict")
    audit("login", "hq", "", "ok")
    return {"ok": True}


@app.post("/api/logout", dependencies=[Depends(require_session)])
def logout(req: Request, resp: Response):
    with db() as con:
        con.execute("DELETE FROM sessions WHERE token_hash=?", (sha(req.cookies.get("hq_session", "")),))
    resp.delete_cookie("hq_session")
    return {"ok": True}


@app.get("/api/mode")
def mode():
    return {"demo": DEMO}


@app.get("/api/me", dependencies=[Depends(require_session)])
def me():
    return {"ok": True}


# ------------------------------------------------------------------ read: overview

QUIET = (23, 7)


def job_health(j: dict) -> str:
    if j.get("state") == "paused":
        return "paused"
    if j.get("last_status") in ("error", "failed", "delivery_failed"):
        return "failed"
    nxt = j.get("next_run_at")
    try:
        if nxt and datetime.fromisoformat(nxt) < datetime.now().astimezone() - timedelta(minutes=30):
            return "late"
    except ValueError:
        pass
    return "ok"


@app.get("/api/overview", dependencies=[Depends(require_session)])
def overview():
    agents = rows("SELECT * FROM agents ORDER BY runs_on DESC, name")
    jobs = rows("SELECT * FROM jobs")
    since = (datetime.now() - timedelta(days=7)).isoformat()
    week_runs = rows("SELECT agent, COUNT(*) n, SUM(status='failed') failed, SUM(used_fallback) fallback, "
                     "SUM(status='failed' AND started_at >= ?) failed_24h "
                     "FROM runs WHERE started_at >= ? GROUP BY agent",
                     ((datetime.now() - timedelta(hours=24)).isoformat(), since))
    wr = {r["agent"]: r for r in week_runs}
    # An agent's "last report" is its own work: relays and silent monitor ticks do not count.
    last = {r["agent"]: r for r in rows(
        "SELECT r.agent, r.job_name, r.started_at, r.status, r.used_fallback, r.headline FROM runs r "
        "JOIN (SELECT agent, MAX(started_at) m FROM runs WHERE job_name NOT LIKE 'Relay %' AND headline != '[SILENT]' "
        "      GROUP BY agent) x ON x.agent=r.agent AND x.m=r.started_at")}
    out = []
    for a in agents:
        aj = [j for j in jobs if j["agent"] == a["id"]]
        health = [job_health(j) for j in aj if j.get("state") != "paused"] or ["idle"]
        state = "failed" if "failed" in health else "late" if "late" in health else \
            "paused" if aj and all(j.get("state") == "paused" for j in aj) else "ok" if aj else "idle"
        nxt = sorted([j["next_run_at"] for j in aj if j.get("next_run_at") and j.get("state") != "paused"])
        out.append({**a, "state": state, "jobs": len(aj), "next_run_at": nxt[0] if nxt else None,
                    "last": last.get(a["id"]), "week": wr.get(a["id"], {"n": 0, "failed": 0, "fallback": 0, "failed_24h": 0})})
    ideas_week = rows("SELECT type, COUNT(*) n FROM ideas WHERE date >= ? AND agent != 'backlog' GROUP BY type",
                      ((datetime.now() - timedelta(days=7)).date().isoformat(),))
    cw = rows("SELECT * FROM customer_weeks ORDER BY date DESC LIMIT 1")
    timeline = rows("SELECT agent, job_name, started_at, status, used_fallback, headline FROM runs "
                    "WHERE started_at >= ? ORDER BY started_at DESC LIMIT 40",
                    ((datetime.now() - timedelta(hours=24)).isoformat(),))
    hour = datetime.now().hour
    meta = {r["key"]: r["value"] for r in rows("SELECT key, value FROM meta")}
    ranked = fleet_priority()
    p1_by_agent: dict[str, int] = {}
    for r in ranked:
        if r["tier"] == "P1":
            aid = "chief-assistant" if r["agent"] == "backlog" else r["agent"]
            p1_by_agent[aid] = p1_by_agent.get(aid, 0) + 1
    for a in out:
        a["p1"] = p1_by_agent.get(a["id"], 0)
    fallback_24h = rows("SELECT COUNT(*) n FROM runs WHERE used_fallback=1 AND started_at >= ?",
                        ((datetime.now() - timedelta(hours=24)).isoformat(),))[0]["n"]
    return {"agents": out, "ideas_week": ideas_week, "customers": cw[0] if cw else None, "timeline": timeline,
            "quiet_hours": hour >= QUIET[0] or hour < QUIET[1], "last_ingest": meta.get("last_ingest"),
            "fleet_paused": fleet_paused(), "fallback_24h": fallback_24h,
            "priority": {"top": [r for r in ranked if r["tier"] == "P1"][:8],
                         "p1": sum(1 for r in ranked if r["tier"] == "P1"),
                         "p2": sum(1 for r in ranked if r["tier"] == "P2")}}


def fleet_priority(agent: str = "") -> list[dict]:
    """Every open idea of the last 60 days, scored by hq_priority (fleet-wide or one agent)."""
    sql = ("SELECT id, agent, date, type, title, summary, detail, evidence, status, source_url FROM ideas "
           "WHERE date >= date('now', '-60 days')")
    args: tuple = ()
    if agent:
        sql += " AND agent=?"
        args = (agent,)
    return hq_priority.rank(rows(sql, args))


@app.get("/api/agents/{agent}", dependencies=[Depends(require_session)])
def agent_detail(agent: str):
    a = rows("SELECT * FROM agents WHERE id=?", (agent,))
    if not a:
        raise HTTPException(404, "unknown agent")
    jobs = rows("SELECT * FROM jobs WHERE agent=? ORDER BY name", (agent,))
    for j in jobs:
        j["health"] = job_health(j)
    runs = rows("SELECT id, job_name, started_at, status, used_fallback, fallback_model, headline FROM runs "
                "WHERE agent=? ORDER BY started_at DESC LIMIT 60", (agent,))
    soul = soul_path(agent)
    return {**a[0], "jobs": jobs, "runs": runs, "soul": soul.read_text(encoding="utf-8") if soul and soul.exists() else "",
            "brain": brain(agent)}


@app.get("/api/runs/{run_id}", dependencies=[Depends(require_session)])
def run_detail(run_id: int):
    r = rows("SELECT * FROM runs WHERE id=?", (run_id,))
    if not r:
        raise HTTPException(404, "no such run")
    r[0]["ideas"] = rows("SELECT id, type, title, status FROM ideas WHERE run_id=?", (run_id,))
    return r[0]


def soul_path(agent: str) -> Path | None:
    p = PROFILES / agent / "SOUL.md"
    if p.exists():
        return p
    p = AGENTS_DIR / agent / "pc-profile" / "SOUL.md"
    return p if p.exists() else None


def brain(agent: str) -> dict:
    """Long-term memory (MEMORY.md, USER.md) as entries, for the brain panel."""
    mem_dir = PROFILES / agent / "memories"
    out = {}
    for name in ("MEMORY.md", "USER.md"):
        f = mem_dir / name
        if f.exists():
            out[name] = [e.strip() for e in f.read_text(encoding="utf-8").split("\n§\n") if e.strip()]
    kb = AGENTS_DIR / agent / "knowledge-base.md"
    if kb.exists():
        out["knowledge-base"] = re.findall(r"^### (.+)$", kb.read_text(encoding="utf-8"), re.M)[-40:]
    try:  # what the fleet teacher learned for this agent, and the rules the founder approved
        import learnings
        taught = [f"{e['date']} · {e['evidence'].lower()} · {e['title']}: {e['what'][:300]}" for e in learnings.for_agent(agent, days=30, limit=20)]
        if taught:
            out["taught by YouTube Watcher (last 30 days)"] = taught
    except Exception:  # noqa: BLE001
        pass
    soul = PROFILES / agent / "SOUL.md"
    if soul.exists():
        rules = re.findall(r"^- (\d{4}-\d\d-\d\d) \[hq-\d+\] (.+)$", soul.read_text(encoding="utf-8"), re.M)
        if rules:
            out["rules you approved"] = [f"{d} · {r}" for d, r in rules]
    return out


# ------------------------------------------------------------------ read: ideas, prospects, customers, me

@app.get("/api/ideas", dependencies=[Depends(require_session)])
def ideas(week: str = "", agent: str = "", type: str = "", status: str = "", q: str = "", limit: int = 300):
    sql, args = "SELECT id, agent, date, week, type, title, summary, source_url, evidence, status, note, decided_at FROM ideas WHERE 1=1", []
    for col, val in (("week", week), ("agent", agent), ("type", type), ("status", status)):
        if val:
            sql += f" AND {col}=?"
            args.append(val)
    if q:
        sql += " AND (title LIKE ? OR summary LIKE ?)"
        args += [f"%{q}%", f"%{q}%"]
    sql += " ORDER BY date DESC, id DESC LIMIT ?"
    args.append(min(limit, 2000))
    weeks = rows("SELECT week, COUNT(*) n FROM ideas WHERE week != '' GROUP BY week ORDER BY week DESC")
    return {"ideas": rows(sql, tuple(args)), "weeks": weeks,
            "types": rows("SELECT type, COUNT(*) n FROM ideas GROUP BY type ORDER BY n DESC")}


# ------------------------------------------------------------------ Build / Write boards

BOARD = {"build": ("cash_build", "saas", "feature", "quick_win"), "write": ("blog",)}


def board_items(kind: str) -> list[dict]:
    """Every open idea of this kind from every agent, repeats merged, best first, with
    all the research (each report that raised it) attached."""
    types = BOARD[kind]
    marks = ",".join("?" * len(types))
    all_rows = rows(f"SELECT id, agent, date, type, title, summary, detail, evidence, status, source_url, note FROM ideas "
                    f"WHERE type IN ({marks}) AND date >= date('now', '-180 days')", types)
    groups: dict[str, list[dict]] = {}
    for r in all_rows:
        groups.setdefault(f"{r['type']}|{hq_priority._key(r['title'])}", []).append(r)
    out = []
    for item in hq_priority.rank(all_rows):
        same = sorted(groups.get(f"{item['type']}|{hq_priority._key(item['title'])}", []), key=lambda r: (r["date"], r["id"]), reverse=True)
        summ = item["summary"] or ""
        facts = {}
        for label, pat in (("build", r"build:\s*([\d.]+\s*days?)"), ("price", r"price:\s*(USD\s*[\d.]+(?:\s*(?:one-off|/mo|/month|/yr|lifetime))?)"),
                           ("sell via", r"sell via:\s*([^-]+?)\s*-"), ("query", r"Query:\s*([^|\n]+?)\s*(?:\||- why|$)")):
            m = re.search(pat, summ)
            if m:
                facts[label] = m.group(1).strip()[:60]
        if summ.startswith("Tier "):
            facts["tier"] = summ[5:6]
        item.update({"note": next((r["note"] for r in same if r.get("note")), "") or "", "facts": facts,
                     "mentions": [{"id": r["id"], "date": r["date"], "agent": r["agent"], "evidence": r["evidence"] or "",
                                   "source_url": r["source_url"], "summary": r["summary"] or "", "detail": (r["detail"] or "")[:4000]}
                                  for r in same if r["status"] not in ("done", "rejected", "parked")]})
        if item["mentions"]:
            out.append(item)
    return out


@app.get("/api/board", dependencies=[Depends(require_session)])
def board(kind: str = "build"):
    if kind not in BOARD:
        raise HTTPException(400, "kind must be build or write")
    items = board_items(kind)
    counts: dict[str, int] = {}
    for i in items:
        counts[i["type"]] = counts.get(i["type"], 0) + 1
    return {"items": items, "counts": counts, "total": len(items)}


@app.get("/api/board.csv", dependencies=[Depends(require_session)])
def board_csv(kind: str = "build"):
    if kind not in BOARD:
        raise HTTPException(400, "kind must be build or write")
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["rank", "score", "priority", "type", "title", "summary", "times raised", "first seen", "last seen", "status", "why", "source", "note"])
    for n, i in enumerate(board_items(kind), 1):
        dates = [m["date"] for m in i["mentions"]]
        w.writerow([n, i["score"], i["tier"], i["type"], i["title"], i["summary"], len(i["mentions"]), min(dates), max(dates),
                    i["status"], "; ".join(i["why"]), i["source_url"] or "", i["note"]])
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="hermes-{kind}-board.csv"'})


RECHECKABLE = {"code_fix", "customer_risk", "customer_lead", "customer_win", "prospect", "job"}
_STOP = set("the a an of in on to for and or is has have with from at by its it this that".split())


def _tokens(title: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9][a-z0-9._-]+", (title or "").lower()) if t not in _STOP}


def ensure_recheck_column() -> None:
    with db() as con:
        cols = [r[1] for r in con.execute("PRAGMA table_info(ideas)")]
        if "recheck_at" not in cols:
            con.execute("ALTER TABLE ideas ADD COLUMN recheck_at TEXT")


def resolve_rechecks(agent: str = "") -> None:
    """Settle issues waiting on a recheck once the agent has reported again.
    Still in the new report (same or near-same wording) -> stays open, noted.
    Not in the new report -> marked done as "Fixed", shown green for a day, then gone."""
    ensure_recheck_column()
    pending = rows("SELECT id, agent, type, title, run_id, recheck_at FROM ideas WHERE recheck_at IS NOT NULL"
                   + (" AND agent=?" if agent else ""), (agent,) if agent else ())
    for i in pending:
        # A direct answer from the PC (tools/pc_requests.py looked at the lock file / git).
        res = AGENTS_DIR / i["agent"] / "pc-requests" / "results" / f"{i['id']}.json"
        if res.exists():
            try:
                r = json.loads(res.read_text(encoding="utf-8"))
            except ValueError:
                r = {"fixed": False, "detail": "unreadable result"}
            res.unlink(missing_ok=True)
            now_s = datetime.now().strftime("%Y-%m-%d %H:%M")
            with db() as con:
                if r.get("fixed"):
                    con.execute("UPDATE ideas SET recheck_at=NULL, status='done', decided_at=?, note=? WHERE id=?",
                                (datetime.now().astimezone().isoformat(timespec="seconds"), f"Fixed: {r.get('detail', '')}"[:500], i["id"]))
                else:
                    con.execute("UPDATE ideas SET recheck_at=NULL, note=? WHERE id=?",
                                (f"Still there at recheck {now_s}: {r.get('detail', '')}"[:500], i["id"]))
            audit("recheck.result", f"{i['agent']}/{i['title'][:80]}", str(i["id"]), "fixed" if r.get("fixed") else "still there")
            continue
        newer = rows("SELECT id, started_at FROM runs WHERE agent=? AND started_at > ? AND status='ok' "
                     "AND job_name NOT LIKE 'Relay %' AND headline != '[SILENT]' ORDER BY started_at DESC LIMIT 1",
                     (i["agent"], i["recheck_at"][:19]))
        if not newer:
            if datetime.now() - datetime.fromisoformat(i["recheck_at"][:19]) > timedelta(hours=3):
                with db() as con:  # the run never came (PC off, job failed): stop waiting
                    con.execute("UPDATE ideas SET recheck_at=NULL, note=? WHERE id=?",
                                ("Recheck did not complete: the agent has not reported since. Try again.", i["id"]))
            continue
        run = newer[0]
        seen = rows("SELECT id, title FROM ideas WHERE run_id=? AND type=?", (run["id"], i["type"]))
        mine = _tokens(i["title"])
        still = any(s["id"] == i["id"] or (mine and len(mine & _tokens(s["title"])) / len(mine) >= 0.6) for s in seen)
        when = run["started_at"][:16].replace("T", " ")
        with db() as con:
            if still:
                con.execute("UPDATE ideas SET recheck_at=NULL, note=? WHERE id=?", (f"Still there at recheck {when}", i["id"]))
            else:
                con.execute("UPDATE ideas SET recheck_at=NULL, status='done', decided_at=?, note=? WHERE id=?",
                            (datetime.now().astimezone().isoformat(timespec="seconds"), f"Fixed: not found at recheck {when}", i["id"]))
        audit("recheck.result", f"{i['agent']}/{i['title'][:80]}", str(i["id"]), "still there" if still else "fixed")


@app.post("/api/ideas/{idea_id}/recheck", dependencies=[Depends(require_session)])
def idea_recheck(idea_id: int):
    """Recheck one issue: run its agent again and settle this issue from the new report."""
    ensure_recheck_column()
    r = rows("SELECT id, agent, type, title FROM ideas WHERE id=?", (idea_id,))
    if not r:
        raise HTTPException(404, "no such idea")
    if r[0]["type"] not in RECHECKABLE:
        raise HTTPException(400, "This kind of item cannot be rechecked.")
    if r[0]["type"] == "code_fix":
        # Code issues are checked directly on the PC (lock file or git), not by a full audit.
        req = AGENTS_DIR / r[0]["agent"] / "pc-requests"
        req.mkdir(parents=True, exist_ok=True)
        (req / f"issue-{idea_id}.req").write_text(json.dumps({"id": idea_id, "title": r[0]["title"]}), encoding="utf-8")
        with db() as con:
            con.execute("UPDATE ideas SET recheck_at=? WHERE id=?", (datetime.now().isoformat(timespec="seconds"), idea_id))
        audit("recheck", f"{r[0]['agent']}/{r[0]['title'][:80]}", str(idea_id), "direct check queued for the PC")
        return {"ok": True, "where": "pc", "job": "direct check", "eta": "about a minute"}
    # One run settles every issue waiting on this agent: only start it if none is under way.
    waiting = rows("SELECT COUNT(*) n FROM ideas WHERE agent=? AND recheck_at > ?",
                   (r[0]["agent"], (datetime.now() - timedelta(minutes=30)).isoformat(timespec="seconds")))[0]["n"]
    with db() as con:
        con.execute("UPDATE ideas SET recheck_at=? WHERE id=?", (datetime.now().isoformat(timespec="seconds"), idea_id))
    started = recheck(r[0]["agent"]) if not waiting else {"where": "already running", "job": "", "eta": "shares the recheck already under way"}
    return {"ok": True, **{k: started[k] for k in ("where", "job", "eta")}}


@app.get("/api/priority", dependencies=[Depends(require_session)])
def priority(agent: str = "", limit: int = 12):
    """Top open ideas for an agent (or the whole fleet), scored by hq_priority."""
    src = hq_priority.AGENT_ALIAS.get(agent, agent)
    resolve_rechecks(src)
    ranked = fleet_priority(src)
    state = {r["id"]: r for r in rows("SELECT id, recheck_at, note FROM ideas WHERE agent=?", (src,))} if src else {}
    for r in ranked:
        st = state.get(r["id"], {})
        r["rechecking"] = bool(st.get("recheck_at"))
        r["note"] = st.get("note") or ""
        r["recheckable"] = r["type"] in RECHECKABLE
    fixed = rows("SELECT id, type, title, note, decided_at FROM ideas WHERE status='done' AND note LIKE 'Fixed:%' "
                 "AND decided_at >= ?" + (" AND agent=?" if src else "") + " ORDER BY decided_at DESC LIMIT 20",
                 ((datetime.now().astimezone() - timedelta(hours=24)).isoformat(timespec="seconds"),) + ((src,) if src else ()))
    counts = {t: sum(1 for r in ranked if r["tier"] == t) for t in ("P1", "P2", "P3")}
    shown = [r for r in ranked if r["tier"] != "P3"][: max(1, min(limit, 50))]
    waiting = [r for r in ranked if r.get("rechecking") and r not in shown]
    return {"items": shown + waiting, "counts": counts, "open": len(ranked), "fixed": fixed}


@app.get("/api/ideas/{idea_id}", dependencies=[Depends(require_session)])
def idea_detail(idea_id: int):
    r = rows("SELECT * FROM ideas WHERE id=?", (idea_id,))
    if not r:
        raise HTTPException(404, "no such idea")
    return r[0]


class IdeaUpdate(BaseModel):
    status: str | None = None
    note: str | None = None


@app.post("/api/ideas/{idea_id}", dependencies=[Depends(require_session)])
def idea_update(idea_id: int, body: IdeaUpdate):
    if body.status and body.status not in ("new", "approved", "parked", "rejected", "done"):
        raise HTTPException(400, "bad status")
    with db() as con:
        if body.status:
            con.execute("UPDATE ideas SET status=?, decided_at=? WHERE id=?",
                        (body.status, datetime.now().astimezone().isoformat(timespec="seconds"), idea_id))
        if body.note is not None:
            con.execute("UPDATE ideas SET note=? WHERE id=?", (body.note[:2000], idea_id))
    row = rows("SELECT id, agent, type, title, status FROM ideas WHERE id=?", (idea_id,))
    if row and row[0]["type"] == "proposal" and body.status:
        apply_proposal(row[0])
    audit("idea", str(idea_id), json.dumps(body.model_dump()), "ok")
    write_decisions_file()
    return idea_detail(idea_id)


# Agents whose instructions the weekly tune-up may change, once the founder approves.
TUNABLE = {"blog-planner", "social-planner", "growth-scout", "opportunity-scout"}
RULES_HEAD = "## Learned rules (approved in Hermes HQ)"
RULES_INTRO = ("Proposed by the weekly tune-up from the founder's decisions and the fleet teacher's learnings, and "
               "approved by the founder. Follow them; where one conflicts with an older instruction above, the newer "
               "approved rule wins.")


def apply_proposal(i: dict) -> None:
    """Approved -> the rule is written into the agent's SOUL.md (a dated backup is kept).
    Any other status -> the rule is taken out again. One tagged line per proposal."""
    soul = PROFILES / i["agent"] / "SOUL.md"
    if i["agent"] not in TUNABLE or not soul.exists():
        return
    text = soul.read_text(encoding="utf-8")
    tag = f"[hq-{i['id']}]"
    lines = [ln for ln in text.rstrip("\n").splitlines() if tag not in ln]
    if i["status"] == "approved":
        if RULES_HEAD not in text:
            lines += ["", RULES_HEAD, "", RULES_INTRO, ""]
        lines.append(f"- {datetime.now():%Y-%m-%d} {tag} {' '.join(i['title'].split())}")
    new = "\n".join(lines) + "\n"
    if new == text:
        return
    (soul.parent / f"SOUL.md.bak-{datetime.now():%Y%m%d-%H%M%S}").write_text(text, encoding="utf-8")
    soul.write_text(new, encoding="utf-8")
    mirror = AGENTS_DIR / i["agent"] / "SOUL.md"
    if mirror.exists():
        mirror.write_text(new, encoding="utf-8")
    audit("soul.rule", i["agent"], tag, "added" if i["status"] == "approved" else "removed")


def write_decisions_file() -> None:
    """Claudia reads this in her reviews: every decision made in HQ."""
    decided = rows("SELECT date, agent, type, title, status, note, decided_at FROM ideas "
                   "WHERE decided_at IS NOT NULL ORDER BY decided_at DESC LIMIT 300")
    lines = ["# Decisions made in Hermes HQ", "", "Newest first. Written by HQ; read-only for agents.", ""]
    lines += [f"- {d['decided_at'][:10]} | {d['status'].upper()} | {d['type']} | {d['title']}"
              + (f" | note: {d['note']}" if d.get("note") else "") for d in decided]
    (AGENTS_DIR / "chief-assistant" / "hq-decisions.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


@app.get("/api/ideas.csv", dependencies=[Depends(require_session)])
def ideas_csv(week: str = "", agent: str = "", type: str = "", status: str = ""):
    data = ideas(week, agent, type, status, "", 20000)["ideas"]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=["date", "week", "agent", "type", "title", "summary", "status", "note", "source_url", "evidence"])
    w.writeheader()
    for d in data:
        w.writerow({k: d.get(k) for k in w.fieldnames})
    name = f"hermes-ideas-{datetime.now():%Y-%m-%d}.csv"
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/api/ideas.xlsx", dependencies=[Depends(require_session)])
def ideas_xlsx(week: str = "", agent: str = "", type: str = "", status: str = ""):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    data = ideas(week, agent, type, status, "", 20000)["ideas"]
    wb = Workbook()
    ws = wb.active
    ws.title = "Ideas"
    cols = ["date", "week", "agent", "type", "title", "summary", "status", "note", "source_url", "evidence"]
    ws.append([c.replace("_", " ").title() for c in cols])
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="0B3D4F")
    for d in data:
        ws.append([d.get(c) for c in cols])
    widths = {"A": 11, "B": 10, "C": 22, "D": 14, "E": 60, "F": 80, "G": 11, "H": 30, "I": 40, "J": 30}
    for col, w in widths.items():
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    buf = io.BytesIO()
    wb.save(buf)
    name = f"hermes-ideas-{datetime.now():%Y-%m-%d}.xlsx"
    return Response(buf.getvalue(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/api/prospects", dependencies=[Depends(require_session)])
def prospects():
    return {"prospects": rows("SELECT * FROM prospects ORDER BY date DESC, rank")}


class ProspectUpdate(BaseModel):
    status: str | None = None
    note: str | None = None


@app.post("/api/prospects/{cid}", dependencies=[Depends(require_session)])
def prospect_update(cid: str, body: ProspectUpdate):
    if body.status and body.status not in ("suggested", "contacted", "replied", "won", "no fit"):
        raise HTTPException(400, "bad status")
    with db() as con:
        if body.status:
            con.execute("UPDATE prospects SET status=?, updated_at=? WHERE cid=?", (body.status, datetime.now().isoformat(), cid))
        if body.note is not None:
            con.execute("UPDATE prospects SET note=? WHERE cid=?", (body.note[:2000], cid))
    sync_prospect_status(cid, body.status)
    audit("prospect", cid, json.dumps(body.model_dump()), "ok")
    return {"ok": True}


def sync_prospect_status(cid: str, status: str | None) -> None:
    """Keep prospects.csv (the agent's ledger) in step with decisions made in HQ."""
    if not status:
        return
    path = AGENTS_DIR / "prospect-finder" / "prospects.csv"
    if not path.exists():
        return
    with path.open(encoding="utf-8", newline="") as h:
        data = list(csv.DictReader(h))
    if not data:
        return
    for r in data:
        if r.get("cid") == cid:
            r["status"] = status
    with path.open("w", encoding="utf-8", newline="") as h:
        w = csv.DictWriter(h, fieldnames=list(data[0].keys()))
        w.writeheader()
        w.writerows(data)


@app.get("/api/customers", dependencies=[Depends(require_session)])
def customers():
    weeks = rows("SELECT * FROM customer_weeks ORDER BY date")
    signals = rows("SELECT id, date, type, title, summary, status, note FROM ideas "
                   "WHERE type IN ('customer_win','customer_risk','customer_lead') ORDER BY date DESC, type")
    return {"weeks": weeks, "signals": signals}


@app.get("/api/me/journal", dependencies=[Depends(require_session)])
def journal():
    base = AGENTS_DIR / "chief-assistant"
    read = lambda n: (base / n).read_text(encoding="utf-8") if (base / n).exists() else ""
    records = sorted((base / "records").glob("*.md")) if (base / "records").exists() else []
    return {"journal": read("journal.md"), "inbox": read("task-inbox.md"), "about": read("about-me.md"),
            "records": [{"name": p.name, "text": p.read_text(encoding="utf-8")} for p in records[-12:]]}


# ------------------------------------------------------------------ control (allowlisted)

def hermes(args: list[str], timeout: int = 120) -> tuple[int, str]:
    if DEMO:
        return demo.hermes(args, HOME)
    r = subprocess.run([HERMES] + args, capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout + r.stderr).strip()


def job_row(job_id: str) -> dict:
    r = rows("SELECT * FROM jobs WHERE id=?", (job_id,))
    if not r:
        raise HTTPException(404, "unknown job")
    j = r[0]
    if rows("SELECT runs_on FROM agents WHERE id=?", (j["agent"],))[0]["runs_on"] != "server":
        raise HTTPException(400, "This agent runs on the PC; control it there.")
    return j


@app.post("/api/jobs/{job_id}/{action}", dependencies=[Depends(require_session)])
def job_action(job_id: str, action: str):
    if action not in ("run", "pause", "resume"):
        raise HTTPException(400, "unknown action")
    j = job_row(job_id)
    code, out = hermes(["-p", j["agent"], "cron", action, job_id])
    audit(f"job.{action}", f"{j['agent']}/{j['name']}", job_id, out)
    if code != 0:
        raise HTTPException(500, out[-300:])
    refresh_jobs()
    return {"ok": True, "output": out[-300:]}


@app.post("/api/agents/{agent}/recheck", dependencies=[Depends(require_session)])
def recheck(agent: str):
    """Run the agent's own job again so its findings refresh. Server agents run at once;
    PC agents get a request file that the PC collects within five minutes
    (tools/pc_requests.py), runs, and syncs back."""
    a = rows("SELECT runs_on FROM agents WHERE id=?", (agent,))
    if not a:
        raise HTTPException(404, "unknown agent")
    jobs = [j for j in rows("SELECT * FROM jobs WHERE agent=? ORDER BY name", (agent,))
            if j.get("state") != "paused" and not j["name"].startswith(("Relay", "Weekly:", "Fleet"))
            and not j.get("no_agent") or (j.get("no_agent") and agent != "chief-assistant" and j.get("state") != "paused")]
    # The job that produces the findings: prefer a daily one over a fact-check or weekly extra.
    jobs.sort(key=lambda j: ("fact check" in j["name"].lower(), not j["name"].lower().startswith(("daily", "weekly"))))
    if not jobs or agent == "chief-assistant":
        raise HTTPException(400, "This agent has no job to recheck.")
    j = jobs[0]
    if a[0]["runs_on"] == "server":
        code, out = hermes(["-p", agent, "cron", "run", j["id"]])
        audit("recheck", f"{agent}/{j['name']}", j["id"], out)
        if code != 0:
            raise HTTPException(500, out[-300:])
        refresh_jobs()
        return {"ok": True, "where": "server", "job": j["name"], "eta": "a few minutes"}
    if not re.fullmatch(r"[0-9a-f]{8,16}", j["id"]):
        raise HTTPException(400, "unexpected job id")
    req = AGENTS_DIR / agent / "pc-requests"
    req.mkdir(parents=True, exist_ok=True)
    (req / f"{j['id']}.req").write_text(datetime.now().isoformat(timespec="seconds"), encoding="utf-8")
    audit("recheck", f"{agent}/{j['name']}", j["id"], "queued for the PC")
    return {"ok": True, "where": "pc", "job": j["name"], "eta": "up to 5 minutes to start, if the PC is on"}


class ScheduleIn(BaseModel):
    schedule: str


@app.post("/api/jobs/{job_id}/schedule", dependencies=[Depends(require_session)])
def job_schedule(job_id: str, body: ScheduleIn):
    if not re.fullmatch(r"[\d*/,\- ]{9,60}|every \d+[mh]", body.schedule.strip()):
        raise HTTPException(400, "Use a cron expression like '30 9 * * 1-5' or 'every 6h'.")
    j = job_row(job_id)
    code, out = hermes(["-p", j["agent"], "cron", "edit", job_id, "--schedule", body.schedule.strip()])
    audit("job.schedule", f"{j['agent']}/{j['name']}", body.schedule, out)
    if code != 0:
        raise HTTPException(500, out[-300:])
    refresh_jobs()
    return {"ok": True}


def fleet_paused() -> bool:
    # `hermes pause` writes this sentinel at the fleet root; `hermes resume` removes it.
    return (HOME / ".hermes" / "ESTOP").exists()


@app.post("/api/fleet/{action}", dependencies=[Depends(require_session)])
def fleet(action: str):
    if action not in ("pause", "resume"):
        raise HTTPException(400, "unknown action")
    code, out = hermes([action])
    audit(f"fleet.{action}", "all", "", out)
    if code != 0:
        raise HTTPException(500, out[-300:])
    return {"ok": True, "output": out[-300:]}


def refresh_jobs() -> None:
    subprocess.Popen([sys.executable, str(Path(__file__).resolve().parent / "hq_ingest.py")],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@app.get("/api/audit", dependencies=[Depends(require_session)])
def audit_log():
    return {"audit": rows("SELECT * FROM audit ORDER BY id DESC LIMIT 200")}


# ------------------------------------------------------------------ voice: Kokoro TTS (free, on this server)

VOICES = {  # British voices for the crew; Claudia gets the calm one.
    "chief-assistant": "bf_emma", "opportunity-scout": "bm_george", "growth-scout": "bm_lewis",
    "blog-planner": "bf_isabella", "social-planner": "bf_alice", "prospect-finder": "bm_daniel",
    "client-wins": "bf_lily", "code-health": "bm_george", "youtube-watcher": "bf_alice",
}
AUDIO = HQ / "audio"
_kokoro = None


def kokoro():
    global _kokoro
    if _kokoro is None:
        from kokoro_onnx import Kokoro
        _kokoro = Kokoro(str(HQ / "models" / "kokoro-v1.0.onnx"), str(HQ / "models" / "voices-v1.0.bin"))
    return _kokoro


class TtsIn(BaseModel):
    agent: str = "chief-assistant"
    text: str


# Neural voices (Microsoft, via edge-tts): natural, fast, and they report where each
# word starts, which makes Claudia's lip-sync exact. Kokoro stays as the offline fallback.
NEURAL = {
    "chief-assistant": "en-GB-SoniaNeural", "opportunity-scout": "en-GB-RyanNeural",
    "growth-scout": "en-GB-ThomasNeural", "blog-planner": "en-GB-LibbyNeural",
    "social-planner": "en-GB-MaisieNeural", "prospect-finder": "en-AU-WilliamNeural",
    "client-wins": "en-AU-NatashaNeural",
    "code-health": "en-IE-ConnorNeural", "youtube-watcher": "en-NZ-MollyNeural",
}


def speakable(text: str) -> str:
    text = re.sub(r"https?://\S+", "link", text)
    text = re.sub(r"[*_`#>|]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()[:600]


def neural_tts(voice: str, text: str) -> tuple[bytes, dict]:
    import asyncio
    import edge_tts

    async def run():
        audio, words, wtimes, wdurs = bytearray(), [], [], []
        async for ch in edge_tts.Communicate(text, voice, rate="+4%", boundary="WordBoundary").stream():
            if ch["type"] == "audio":
                audio += ch["data"]
            elif ch["type"] == "WordBoundary":
                words.append(ch["text"]); wtimes.append(round(ch["offset"] / 1e4)); wdurs.append(round(ch["duration"] / 1e4))
        return bytes(audio), {"words": words, "wtimes": wtimes, "wdurations": wdurs}
    return asyncio.run(asyncio.wait_for(run(), timeout=12))


@app.post("/api/tts", dependencies=[Depends(require_session)])
def tts(body: TtsIn):
    text = speakable(body.text)
    if not text:
        raise HTTPException(400, "nothing to say")
    voice = NEURAL.get(body.agent, "en-GB-SoniaNeural")
    AUDIO.mkdir(exist_ok=True)
    key = sha(voice + "|" + text)[:24]
    mp3, meta = AUDIO / f"{key}.mp3", AUDIO / f"{key}.json"
    if not mp3.exists():
        try:
            audio, timing = neural_tts(voice, text)
            if not audio:
                raise RuntimeError("empty audio")
            mp3.write_bytes(audio)
            meta.write_text(json.dumps(timing, ensure_ascii=True), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001  network or service trouble: fall back to Kokoro
            audit("tts.fallback", body.agent, text[:80], str(exc)[:120])
            return tts_kokoro(body)
    headers = {"Cache-Control": "private, max-age=86400", "Access-Control-Expose-Headers": "x-words"}
    try:
        headers["x-words"] = meta.read_text(encoding="utf-8")
    except OSError:
        pass
    return FileResponse(mp3, media_type="audio/mpeg", headers=headers)


def tts_kokoro(body: TtsIn):
    import soundfile as sf
    from fastapi.responses import FileResponse
    text = re.sub(r"\s+", " ", body.text).strip()[:500]
    if not text:
        raise HTTPException(400, "nothing to say")
    voice = VOICES.get(body.agent, "bf_emma")
    AUDIO.mkdir(exist_ok=True)
    f = AUDIO / f"{sha(voice + '|' + text)[:24]}.wav"
    if not f.exists():
        samples, sr = kokoro().create(text, voice=voice, speed=1.0, lang="en-gb")
        sf.write(str(f), samples, sr)
        # Keep the cache small: newest 400 clips.
        clips = sorted(AUDIO.glob("*.wav"), key=lambda p: p.stat().st_mtime)
        for old in clips[:-400]:
            old.unlink(missing_ok=True)
    return FileResponse(f, media_type="audio/wav", headers={"Cache-Control": "private, max-age=86400"})


# ------------------------------------------------------------------ Claudia: talk to her real brain, act through an allowlist

ALLOWED_PATHS = {"/", "/agents", "/ideas", "/build", "/write", "/prospects", "/customers", "/me", "/log"}


def live_context() -> str:
    o = overview()
    lines = ["FLEET NOW:"]
    for a in o["agents"]:
        last = a.get("last") or {}
        lines.append(f"- {a['name']} ({a['id']}, {a['runs_on']}): {a['state']}; next run {a.get('next_run_at') or '-'}; "
                     f"last: {(last.get('headline') or '')[:120]}")
    ideas_new = rows("SELECT id, agent, type, title FROM ideas WHERE status='new' AND agent!='backlog' "
                     "AND type NOT IN ('customer_win','customer_risk','customer_lead','prospect','job','learning') "
                     "ORDER BY date DESC, id DESC LIMIT 12")
    top = [r for r in o["priority"]["top"]][:6]
    lines.append(f"DO FIRST (P1, {o['priority']['p1']} in total; id | agent | title | why):")
    lines += [f"- {r['id']} | {r['agent']} | {r['title'][:90]} | {', '.join(r['why'][:3])}" for r in top]
    if o.get("fallback_24h"):
        lines.append(f"WARNING: {o['fallback_24h']} run(s) in the last 24h used the backup model; their facts may be wrong.")
    lines.append("RECENT UNDECIDED IDEAS (id | agent | type | title):")
    lines += [f"- {i['id']} | {i['agent']} | {i['type']} | {i['title'][:100]}" for i in ideas_new]
    if o.get("customers"):
        c = o["customers"]
        lines.append(f"CUSTOMERS: {c['paying']} paying, {c['winning']} winning, {c['at_risk']} at risk, lifetime ${c['revenue_usd']:.0f}")
    j = AGENTS_DIR / "chief-assistant" / "journal.md"
    today = datetime.now().strftime("%Y-%m-%d")
    if j.exists():
        sec = re.search(rf"^## {today}\n(.*?)(?=^## |\Z)", j.read_text(encoding="utf-8"), re.S | re.M)
        lines.append("TODAY'S JOURNAL:\n" + (sec.group(1).strip() if sec else "(nothing logged today)"))
    lines.append(f"NOW: {datetime.now():%A %Y-%m-%d %H:%M} NZ; quiet hours: {o['quiet_hours']}; emergency stop: {o['fleet_paused']}")
    return "\n".join(lines)


ASK_RULES = """You are Claudia, speaking OUT LOUD to the founder inside Hermes HQ (the fleet dashboard).
Reply with ONE JSON object and nothing else:
{"say": "<what you say: at most 3 short spoken sentences, plain words, no markdown, no lists>",
 "action": <one action object or null>}
Allowed actions (pick one only if the founder clearly asked for it):
- {"type":"navigate","path":"/" | "/agents" | "/agents/<agent-id>" | "/ideas" | "/build" (things to build, ranked) | "/write" (blog ideas, ranked) | "/prospects" | "/customers" | "/me" | "/log"}
- {"type":"run_job","agent":"<server agent id>"}   (HQ asks the founder to confirm before running)
- {"type":"idea_status","idea_id":<id from the list>,"status":"approved"|"parked"|"rejected"|"done"}   (HQ confirms first)
- {"type":"log","kind":"plan"|"done","text":"<short text>"}   (adds to today's journal)
Never draft or send messages to customers or prospects; if asked, say that stays with the founder.
Never claim you did something that needs confirmation; say you have put it on screen for the founder to confirm.
Use only the facts below. If you do not know, say so briefly.
"""


class AskIn(BaseModel):
    text: str
    page: str = "/"


@app.post("/api/ask", dependencies=[Depends(require_session)])
def ask(body: AskIn):
    said = body.text.strip()[:500]
    if not said:
        raise HTTPException(400, "empty")
    prompt = f"{ASK_RULES}\n{live_context()}\nThe founder is on page: {body.page}\n\nThe founder said: \"{said}\""
    if DEMO:  # no model: a rule-based stand-in answers, and the same allowlist below still applies
        out = demo.ask(said, body.page, overview())
    else:
        r = subprocess.run([HERMES, "-p", "chief-assistant", "chat", "-q", prompt, "--oneshot", "-Q",
                            "--max-turns", "1", "--reasoning", "low"], capture_output=True, text=True, timeout=90, cwd="/tmp")
        out = re.sub(r"\n?session_id:.*$", "", (r.stdout or "").strip(), flags=re.S).strip()
    reply = {"say": "", "action": None}
    m = re.search(r"\{.*\}", out, re.S)
    if m:
        try:
            reply.update(json.loads(m.group(0)))
        except ValueError:
            reply["say"] = out
    else:
        reply["say"] = out or "Sorry, I could not think of an answer just now."
    act = reply.get("action") if isinstance(reply.get("action"), dict) else None
    if act:  # enforce the allowlist here, whatever the model said
        t = act.get("type")
        ok = bool(t == "navigate" and (act.get("path") in ALLOWED_PATHS or re.fullmatch(r"/agents/[a-z-]+", str(act.get("path")))))
        ok |= t == "run_job" and bool(rows("SELECT 1 FROM agents WHERE id=? AND runs_on='server'", (str(act.get("agent")),)))
        ok |= t == "idea_status" and act.get("status") in ("approved", "parked", "rejected", "done") and str(act.get("idea_id", "")).isdigit()
        ok |= t == "log" and act.get("kind") in ("plan", "done") and bool(str(act.get("text", "")).strip())
        reply["action"] = act if ok else None
    reply["say"] = str(reply.get("say") or "")[:600]
    audit("voice.ask", "claudia", said[:200], json.dumps(reply)[:400])
    return reply


class LogIn(BaseModel):
    kind: str
    text: str


@app.post("/api/me/log", dependencies=[Depends(require_session)])
def journal_log(body: LogIn):
    """Append to today's Plan: or Done: line in Claudia's journal (the same file Telegram check-ins use)."""
    if body.kind not in ("plan", "done"):
        raise HTTPException(400, "kind must be plan or done")
    text = re.sub(r"\s+", " ", body.text).strip()[:300]
    path = AGENTS_DIR / "chief-assistant" / "journal.md"
    s = path.read_text(encoding="utf-8") if path.exists() else "# Journal\n"
    today = datetime.now().strftime("%Y-%m-%d")
    label = "Plan:" if body.kind == "plan" else "Done:"
    m = re.search(rf"^## {today}\n(.*?)(?=^## |\Z)", s, re.S | re.M)
    if not m:
        s = s.rstrip() + f"\n\n## {today}\nPlan:\nDone:\nNotes:\n"
        m = re.search(rf"^## {today}\n(.*?)(?=^## |\Z)", s, re.S | re.M)
    sec = m.group(1)
    if re.search(rf"^{label}", sec, re.M):
        new = re.sub(rf"^({label}.*)$", lambda x: x.group(1).rstrip() + ("; " if x.group(1).strip() != label else " ") + text, sec, count=1, flags=re.M)
    else:
        new = sec + f"{label} {text}\n"
    s = s[:m.start(1)] + new + s[m.end(1):]
    path.write_text(s, encoding="utf-8")
    audit("journal", body.kind, text, "ok")
    return {"ok": True}


@app.exception_handler(HTTPException)
def http_error(_req: Request, exc: HTTPException):
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
