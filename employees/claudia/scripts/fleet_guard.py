"""Fleet guardian: a no-model health check Claudia relays to Telegram.

Runs as a --no-agent job on the server three times a day. Prints nothing when all is
well (empty output = no message). When something is wrong it prints a short list, one
line per problem, with what to do. It found nothing? Then the fleet is healthy, the
host is healthy and the primary model is available.

Checks: failed runs, runs on the backup model (Codex quota gone), jobs that missed
their slot, HQ ingest stalled, hq-api / nginx down, disk nearly full, PC sync stale.
"""

from __future__ import annotations

import os
import re
import shutil
import sqlite3
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

HOME = Path(os.environ.get("HQ_HOME") or Path.home())
DB = HOME / "hq" / "hq.db"
LOGS = HOME / ".hermes" / "profiles"
PC_SYNC = HOME / "agents" / "youtube-watcher" / "pc-reports" / "_pc_jobs_status.json"
NOW = datetime.now()


def q(sql: str, args: tuple = ()) -> list[sqlite3.Row]:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


def check() -> list[str]:
    out: list[str] = []
    day = (NOW - timedelta(hours=24)).isoformat(timespec="seconds")

    failed = q("SELECT agent, job_name, COUNT(*) n FROM runs WHERE status='failed' AND started_at >= ? "
               "GROUP BY agent, job_name ORDER BY n DESC", (day,))
    for r in failed:
        out.append(f"FAILED x{r['n']}: {r['agent']} / {r['job_name']} in the last 24h. Open the run in HQ.")

    fb = q("SELECT COUNT(*) n, MAX(started_at) last FROM runs WHERE used_fallback=1 AND started_at >= ?", (day,))[0]
    if fb["n"]:
        # The logs say how long Codex asked us to wait.
        wait = ""
        for log in LOGS.glob("*/logs/*.log"):
            try:
                tail = log.read_text(encoding="utf-8", errors="replace")[-200_000:]
            except OSError:
                continue
            for m in re.finditer(r"^(\S+ \S+) WARNING .*quota exhausted \(429\); retry after (\d+)s", tail, re.M):
                try:
                    when = datetime.strptime(m.group(1)[:19], "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    continue
                back = when + timedelta(seconds=int(m.group(2)))
                if back > NOW:
                    wait = f" Codex quota is out until about {back:%a %H:%M}."
        out.append(f"BACKUP MODEL: {fb['n']} run(s) used the backup model in the last 24h (last {fb['last'][11:16]})."
                   f"{wait} Treat those reports with care; numbers may be invented.")

    late = q("SELECT agent, name, next_run_at FROM jobs WHERE state='scheduled' AND next_run_at IS NOT NULL")
    for j in late:
        try:
            nxt = datetime.fromisoformat(j["next_run_at"]).replace(tzinfo=None)
        except ValueError:
            continue
        if nxt < NOW - timedelta(hours=2):
            out.append(f"MISSED: {j['agent']} / {j['name']} was due {nxt:%a %H:%M} and has not run. Is the gateway up?")

    meta = {r["key"]: r["value"] for r in q("SELECT key, value FROM meta")}
    try:
        last_ingest = datetime.fromisoformat(meta.get("last_ingest", "")).replace(tzinfo=None)
        if last_ingest < NOW - timedelta(minutes=45):
            out.append(f"HQ INGEST STALLED: last update {last_ingest:%a %H:%M}. Run: ~/hq/venv/bin/python ~/hq/server/hq_ingest.py")
    except ValueError:
        out.append("HQ INGEST: never recorded a run.")

    for unit, cmd in (("hq-api", ["systemctl", "--user", "is-active", "hq-api"]), ("nginx", ["systemctl", "is-active", "nginx"])):
        try:
            state = subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            state = "unknown"
        if state != "active":
            out.append(f"{unit.upper()} is {state}. HQ is down. Restart: systemctl {'--user ' if unit == 'hq-api' else ''}restart {unit}")

    usage = shutil.disk_usage("/")
    pct = usage.used * 100 // usage.total
    if pct >= 85:
        out.append(f"DISK {pct}% full ({usage.free // 2**30} GB left). Clear ~/.hermes/profiles/*/cron/output or old sessions.")

    if PC_SYNC.exists():
        age = NOW - datetime.fromtimestamp(PC_SYNC.stat().st_mtime)
        if age > timedelta(hours=30):
            out.append(f"PC SYNC STALE: nothing from the PC for {age.days}d {age.seconds // 3600}h. Is the PC on and its gateway running?")
    return out


def main() -> None:
    problems = check()
    if not problems:
        return  # empty output: Claudia sends nothing
    print(f"FLEET GUARDIAN - {NOW:%a %d %b %H:%M} - {len(problems)} thing(s) need you")
    print()
    for p in problems:
        print(f"- {p}")


if __name__ == "__main__":
    main()
