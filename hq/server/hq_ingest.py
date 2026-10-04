"""Hermes HQ ingest: copy every agent's output into hq.db so nothing is ever lost.

Runs every 10 minutes from cron (and once by hand to backfill). It is idempotent:
a report is keyed by its path and content hash, so re-runs never duplicate.

Sources on the Hermes server:
  ~/.hermes/profiles/<agent>/cron/jobs.json           jobs (schedule, state, model pin)
  ~/.hermes/profiles/<agent>/cron/output/<job>/*.md   runs (report files)
  ~/agents/<agent>/pc-reports/*.md                    runs of PC agents (mirrored)
  ~/agents/<agent>/pc-reports/_pc_jobs_status.json    jobs of PC agents (mirrored)
  ~/agents/prospect-finder/prospects.csv              prospects
  ~/agents/client-wins/pulse-history.json             customer weeks
  ~/agents/chief-assistant/ideas-backlog.md           backlog ideas (section 3 and 4)

Ideas inside reports are extracted by type-specific parsers in hq_parsers.py; a
report whose parser fails still has its full body stored, so it can be re-parsed
later when the parser improves.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

HOME = Path(os.environ.get("HQ_HOME") or Path.home())
PROFILES = HOME / ".hermes" / "profiles"
AGENTS = HOME / "agents"
DB = HOME / "hq" / "hq.db"

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hq_parsers  # noqa: E402

AGENT_META = {
    "chief-assistant": ("Claudia", "server", "Chief AI Assistant: fleet manager, Telegram front desk, accountability partner"),
    "opportunity-scout": ("Opportunity Scout", "server", "Buildable AI-native SaaS ideas, revenue first"),
    "growth-scout": ("Growth Scout", "server", "Fernway product and growth research"),
    "blog-planner": ("Blog Planner", "server", "Easy-to-rank blog ideas for fernway.example/blog"),
    "social-planner": ("Social Planner", "server", "Post ideas for Facebook, LinkedIn and X"),
    "prospect-finder": ("Prospect Finder", "server", "Local businesses with weak Google Business Profiles"),
    "client-wins": ("Client Wins", "server", "Weekly customer health: winning, at risk, upsell"),
    "youtube-watcher": ("YouTube Watcher", "pc", "Learns from SEO, AI and SaaS videos"),
    "code-health": ("Code Health", "pc", "Weekly security and dependency audit of the repos"),
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
  id TEXT PRIMARY KEY, name TEXT, runs_on TEXT, purpose TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY, agent TEXT, name TEXT, schedule TEXT, deliver TEXT, state TEXT,
  model TEXT, provider TEXT, no_agent INTEGER, last_run_at TEXT, last_status TEXT,
  last_error TEXT, next_run_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY, agent TEXT, job_id TEXT, job_name TEXT, path TEXT UNIQUE,
  content_hash TEXT, started_at TEXT, status TEXT, used_fallback INTEGER,
  fallback_model TEXT, headline TEXT, body TEXT, parsed INTEGER DEFAULT 0, ingested_at TEXT);
CREATE INDEX IF NOT EXISTS runs_agent_time ON runs(agent, started_at);
CREATE TABLE IF NOT EXISTS ideas (
  id INTEGER PRIMARY KEY, key TEXT UNIQUE, run_id INTEGER, agent TEXT, date TEXT, week TEXT,
  type TEXT, title TEXT, summary TEXT, detail TEXT, source_url TEXT, evidence TEXT,
  status TEXT DEFAULT 'new', decided_at TEXT, note TEXT, created_at TEXT);
CREATE INDEX IF NOT EXISTS ideas_week ON ideas(week, agent, type);
CREATE TABLE IF NOT EXISTS prospects (
  cid TEXT PRIMARY KEY, date TEXT, business TEXT, niche TEXT, city TEXT, rank INTEGER,
  rating REAL, reviews INTEGER, claimed TEXT, photos INTEGER, website TEXT, phone TEXT,
  email TEXT, signals TEXT, status TEXT, note TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS customer_weeks (
  date TEXT PRIMARY KEY, accounts INTEGER, paying INTEGER, revenue_usd REAL,
  winning INTEGER, at_risk INTEGER);
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY, at TEXT, who TEXT, action TEXT, target TEXT, args TEXT, result TEXT);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def iso_week(date_str: str) -> str:
    try:
        d = datetime.fromisoformat(date_str[:10])
        y, w, _ = d.isocalendar()
        return f"{y}-W{w:02d}"
    except ValueError:
        return ""


def connect() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    return con


# ---------------------------------------------------------------- agents + jobs

def ingest_agents(con: sqlite3.Connection) -> None:
    for aid, (name, runs_on, purpose) in AGENT_META.items():
        con.execute("INSERT INTO agents(id,name,runs_on,purpose,updated_at) VALUES(?,?,?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET name=excluded.name, runs_on=excluded.runs_on, "
                    "purpose=excluded.purpose, updated_at=excluded.updated_at",
                    (aid, name, runs_on, purpose, now()))


def ingest_jobs(con: sqlite3.Connection) -> int:
    n = 0
    for aid, (_, runs_on, _) in AGENT_META.items():
        if runs_on == "server":
            path = PROFILES / aid / "cron" / "jobs.json"
            jobs = json.loads(path.read_text(encoding="utf-8")).get("jobs", []) if path.exists() else []
        else:
            path = AGENTS / aid / "pc-reports" / "_pc_jobs_status.json"
            jobs = json.loads(path.read_text(encoding="utf-8")).get("jobs", []) if path.exists() else []
        for j in jobs:
            sched = j.get("schedule_display") or (j.get("schedule") or {}).get("display") or ""
            con.execute(
                "INSERT INTO jobs(id,agent,name,schedule,deliver,state,model,provider,no_agent,last_run_at,"
                "last_status,last_error,next_run_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET name=excluded.name, schedule=excluded.schedule, deliver=excluded.deliver, "
                "state=excluded.state, model=excluded.model, provider=excluded.provider, no_agent=excluded.no_agent, "
                "last_run_at=excluded.last_run_at, last_status=excluded.last_status, last_error=excluded.last_error, "
                "next_run_at=excluded.next_run_at, updated_at=excluded.updated_at",
                (j.get("id"), aid, j.get("name"), sched, j.get("deliver"), j.get("state"), j.get("model"),
                 j.get("model_provider"), 1 if j.get("no_agent") else 0, j.get("last_run_at"), j.get("last_status"),
                 j.get("last_error"), j.get("next_run_at"), now()))
            n += 1
    return n


# ---------------------------------------------------------------- runs

FALLBACK_RE = re.compile(r"(?:⚠️?\s*)?Provider fallback: (\S+) unavailable; using (\S+?)(?: for this response)?\.?\s*$", re.M)
RUNTIME_RE = re.compile(r"\*\*Run Time:\*\*\s*(\S+ \S+)")
JOB_RE = re.compile(r"^# Cron Job: (.+?)(?: \(FAILED\))?\s*$", re.M)


NARRATION_RE = re.compile(r"\b(let me|now let me|i'll (now )?(analy[sz]e|process|review)|i now have|i already have|"
                          r"based on my (comprehensive )?(research|analysis)|excellent\.|perfect\.)", re.I)


def response_of(text: str) -> str:
    body = text.rsplit("## Response", 1)[1].strip() if "## Response" in text else text.split("\n---\n", 1)[-1].strip()
    # Drop the model thinking aloud before the report ("Let me check...") so it never becomes an idea.
    m = re.search(r"^(#{1,3} |\*\*Decision today|Decision today|Best bet today)", body, re.M)
    if m and m.start() > 0 and NARRATION_RE.search(body[: m.start()]):
        body = body[m.start():]
    return body


def parse_report(path: Path) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    first = text.splitlines()[0] if text else ""
    failed = "(FAILED)" in first
    m = JOB_RE.search(text)
    job_name = m.group(1).strip() if m else path.parent.name
    rt = RUNTIME_RE.search(text)
    started = rt.group(1).replace(" ", "T") if rt else datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")
    fb = FALLBACK_RE.search(text)
    body = response_of(text)
    if fb:
        body = FALLBACK_RE.sub("", body).strip()
    headline = next((ln.strip() for ln in body.splitlines() if ln.strip() and not ln.startswith("```")), "")[:200]
    return {"job_name": job_name, "started_at": started, "status": "failed" if failed else "ok",
            "used_fallback": 1 if fb else 0, "fallback_model": fb.group(2) if fb else None,
            "headline": headline, "body": body, "hash": hashlib.sha256(text.encode()).hexdigest()[:16]}


def report_files() -> list[tuple[str, str, Path]]:
    out = []
    for aid, (_, runs_on, _) in AGENT_META.items():
        if runs_on == "server":
            root = PROFILES / aid / "cron" / "output"
            if root.exists():
                for job_dir in root.iterdir():
                    if job_dir.is_dir():
                        out += [(aid, job_dir.name, p) for p in job_dir.glob("*.md")]
        else:
            root = AGENTS / aid / "pc-reports"
            if root.exists():
                for p in root.glob("*.md"):
                    job_id = p.name.split("__", 1)[0]
                    out.append((aid, job_id, p))
    # Claudia's PC twin: project follow-up reports, mirrored under chief-assistant/pc-reports
    root = AGENTS / "chief-assistant" / "pc-reports"
    if root.exists():
        out += [("chief-assistant", "pc:" + p.name.split("__", 1)[0], p) for p in root.glob("*.md")]
    return out


def ingest_runs(con: sqlite3.Connection) -> tuple[int, int]:
    new = ideas = 0
    known = {r[0]: r[1] for r in con.execute("SELECT path, content_hash FROM runs")}
    for aid, job_id, path in report_files():
        key = str(path)
        info = parse_report(path)
        if known.get(key) == info["hash"]:
            continue
        cur = con.execute(
            "INSERT INTO runs(agent,job_id,job_name,path,content_hash,started_at,status,used_fallback,fallback_model,"
            "headline,body,parsed,ingested_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,0,?) "
            "ON CONFLICT(path) DO UPDATE SET content_hash=excluded.content_hash, status=excluded.status, "
            "used_fallback=excluded.used_fallback, fallback_model=excluded.fallback_model, headline=excluded.headline, "
            "body=excluded.body, parsed=0, ingested_at=excluded.ingested_at",
            (aid, job_id, info["job_name"], key, info["hash"], info["started_at"], info["status"],
             info["used_fallback"], info["fallback_model"], info["headline"], info["body"], now()))
        # Always look the id up: after an upsert that UPDATEs, lastrowid is the id of some
        # earlier INSERT, which once re-linked (and deleted) other runs' ideas.
        run_id = con.execute("SELECT id FROM runs WHERE path=?", (key,)).fetchone()[0]
        new += 1
        if info["status"] == "ok":
            ideas += ingest_ideas(con, run_id, aid, info)
        con.execute("UPDATE runs SET parsed=1 WHERE id=?", (run_id,))
    return new, ideas


def ingest_ideas(con: sqlite3.Connection, run_id: int, agent: str, info: dict) -> int:
    n = 0
    date = info["started_at"][:10]
    con.execute("DELETE FROM ideas WHERE run_id=? AND status='new'", (run_id,))
    for idea in hq_parsers.extract(agent, info["job_name"], info["body"]):
        owner = idea.get("agent", agent)  # a tune-up proposal belongs to the agent it would change
        key = hashlib.sha256(f"{owner}|{idea['type']}|{idea['title'].lower()}".encode()).hexdigest()[:20]
        con.execute(
            "INSERT INTO ideas(key,run_id,agent,date,week,type,title,summary,detail,source_url,evidence,status,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,'new',?) ON CONFLICT(key) DO UPDATE SET run_id=excluded.run_id, "
            "date=excluded.date, week=excluded.week, summary=excluded.summary, detail=excluded.detail, "
            "source_url=excluded.source_url, evidence=excluded.evidence",
            (key, run_id, owner, date, iso_week(date), idea["type"], idea["title"][:200], idea.get("summary", "")[:600],
             idea.get("detail", ""), idea.get("source_url"), idea.get("evidence", ""), now()))
        n += 1
    return n


# ---------------------------------------------------------------- side files

def ingest_prospects(con: sqlite3.Connection) -> int:
    path = AGENTS / "prospect-finder" / "prospects.csv"
    if not path.exists():
        return 0
    n = 0
    with path.open(encoding="utf-8", newline="") as handle:
        for r in csv.DictReader(handle):
            con.execute(
                "INSERT INTO prospects(cid,date,business,niche,city,rank,rating,reviews,claimed,photos,website,phone,email,"
                "signals,status,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(cid) DO UPDATE SET "
                "status=CASE WHEN prospects.status IN ('suggested','') OR prospects.status IS NULL THEN excluded.status ELSE prospects.status END, "
                "email=COALESCE(NULLIF(excluded.email,''), prospects.email), updated_at=excluded.updated_at",
                (r.get("cid"), r.get("date"), r.get("business"), r.get("niche"), r.get("city"), r.get("rank") or None,
                 r.get("rating") or None, r.get("reviews") or None, r.get("claimed"), r.get("photos") or None,
                 r.get("website"), r.get("phone"), r.get("email"), r.get("signals"), r.get("status") or "suggested", now()))
            n += 1
    return n


def ingest_customer_weeks(con: sqlite3.Connection) -> int:
    path = AGENTS / "client-wins" / "pulse-history.json"
    if not path.exists():
        return 0
    rows = json.loads(path.read_text(encoding="utf-8"))
    for h in rows:
        con.execute("INSERT OR REPLACE INTO customer_weeks(date,accounts,paying,revenue_usd,winning,at_risk) VALUES(?,?,?,?,?,?)",
                    (h.get("date"), h.get("accounts"), h.get("paying"), h.get("revenue_usd"), h.get("winning"), h.get("at_risk")))
    return len(rows)


def ingest_backlog(con: sqlite3.Connection) -> int:
    path = AGENTS / "chief-assistant" / "ideas-backlog.md"
    if not path.exists():
        return 0
    n = 0
    for idea in hq_parsers.extract_backlog(path.read_text(encoding="utf-8")):
        key = hashlib.sha256(f"backlog|{idea['title'].lower()}".encode()).hexdigest()[:20]
        con.execute(
            "INSERT INTO ideas(key,run_id,agent,date,week,type,title,summary,detail,source_url,evidence,status,created_at) "
            "VALUES(?,NULL,'backlog',?,?,?,?,?,?,NULL,?,?,?) ON CONFLICT(key) DO UPDATE SET summary=excluded.summary, "
            "detail=excluded.detail, status=CASE WHEN ideas.decided_at IS NULL THEN excluded.status ELSE ideas.status END",
            (key, idea["date"], iso_week(idea["date"]), idea["type"], idea["title"][:200], idea.get("summary", "")[:600],
             idea.get("detail", ""), idea.get("evidence", ""), idea.get("status", "new"), now()))
        n += 1
    return n


def ingest_knowledge(con: sqlite3.Connection) -> int:
    """YouTube Watcher's learnings (mirrored from the PC) as ideas of type 'learning'."""
    path = AGENTS / "youtube-watcher" / "knowledge-base.md"
    if not path.exists():
        return 0
    n = 0
    for idea in hq_parsers.extract_knowledge(path.read_text(encoding="utf-8", errors="replace")):
        key = hashlib.sha256(f"youtube-watcher|learning|{idea['title'].lower()}".encode()).hexdigest()[:20]
        con.execute(
            "INSERT INTO ideas(key,run_id,agent,date,week,type,title,summary,detail,source_url,evidence,status,created_at) "
            "VALUES(?,NULL,'youtube-watcher',?,?,'learning',?,?,?,?,?,'new',?) ON CONFLICT(key) DO UPDATE SET "
            "summary=excluded.summary, detail=excluded.detail, source_url=excluded.source_url, evidence=excluded.evidence",
            (key, idea["date"], iso_week(idea["date"]), idea["title"][:200], idea["summary"][:600],
             idea["detail"], idea["source_url"], idea["evidence"], now()))
        n += 1
    return n


def main() -> None:
    con = connect()
    if "--reparse" in sys.argv:
        # Re-read every stored report with the current parsers. Decided ideas keep their decision.
        with con:
            con.execute("UPDATE runs SET content_hash=''")
    with con:
        ingest_agents(con)
        jobs = ingest_jobs(con)
        runs, ideas = ingest_runs(con)
        prospects = ingest_prospects(con)
        weeks = ingest_customer_weeks(con)
        backlog = ingest_backlog(con)
        backlog += ingest_knowledge(con)
        con.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('last_ingest',?)", (now(),))
    totals = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("runs", "ideas", "prospects", "jobs")}
    print(f"{now()} jobs={jobs} new_runs={runs} new_ideas={ideas} prospects={prospects} weeks={weeks} backlog={backlog} totals={totals}")


if __name__ == "__main__":
    main()
