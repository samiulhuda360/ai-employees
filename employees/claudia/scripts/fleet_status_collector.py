"""Fleet status snapshot for Chief Assistant.

Reads every agent profile's cron jobs and recent report files and prints a
compact, ASCII-safe summary for injection into Chief's scheduled prompts.
Read-only: it never runs, edits, or pauses anything.

Lookback defaults to 1 day. `fleet_weekly_collector.py` imports this module
with a 7-day window for the weekly growth review.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
# Under a profile, HERMES_HOME is that profile (~/.hermes/profiles/<name>): the other
# agents live beside it, not inside it.
PROFILES_DIR = HERMES_HOME.parent if HERMES_HOME.parent.name == "profiles" else HERMES_HOME / "profiles"
AGENTS_DIR = Path.home() / "agents"
AGENTS = ["opportunity-scout", "growth-scout", "blog-planner", "social-planner", "prospect-finder", "client-wins", "youtube-watcher", "code-health"]
PC_RUN_AGENTS = {"youtube-watcher", "code-health"}
PC_SYNC_DIR = AGENTS_DIR / "youtube-watcher" / "pc-reports"  # filled by the PC-to-server sync

LOOKBACK_DAYS = 1
EXCERPT_CHARS = 1200
LIST_HEADLINES = False  # weekly review turns this on to see every report's headline
MAX_TOTAL_CHARS = 60_000


def ascii_safe(text: str) -> str:
    return (text or "").encode("ascii", errors="replace").decode("ascii")


def parse_time(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def report_body(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    # Jobs with continuity embed the previous run's full output (including its
    # own "## Response") above the current one, so the current answer is the
    # LAST "## Response", never the first.
    body = text.rsplit("## Response", 1)[1] if "## Response" in text else text
    return re.sub(r"\n{3,}", "\n\n", body).strip()


def is_suppressed_monitor(path: Path) -> bool:
    head = path.read_text(encoding="utf-8", errors="replace")[:600]
    return "agent run suppressed" in head or "no_change" in head


def recent_reports(job_dir: Path, cutoff: datetime) -> list[Path]:
    if not job_dir.exists():
        return []
    reports = []
    for path in job_dir.glob("*.md"):
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        except OSError:
            continue
        if mtime >= cutoff and not is_suppressed_monitor(path):
            reports.append(path)
    return sorted(reports, key=lambda p: p.stat().st_mtime)


def ledger_activity(agent: str, cutoff: datetime) -> list[str]:
    notes = []
    checks = {
        "opportunity-scout": [PROFILES_DIR / agent / "opportunity_pipeline.md"],
        "growth-scout": [AGENTS_DIR / agent / "product-dossier.md"],
        "youtube-watcher": [AGENTS_DIR / agent / "knowledge-base.md"],
    }.get(agent, [])
    for path in checks:
        if not path.exists():
            notes.append(f"{path.name}: missing")
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        dated = re.findall(r"^#{2,3} .*?(\d{4}-\d{2}-\d{2})", text, flags=re.M)
        dated += re.findall(r"^- date: (\d{4}-\d{2}-\d{2})", text, flags=re.M)
        recent = [d for d in dated if d >= cutoff.strftime("%Y-%m-%d")]
        notes.append(
            f"{path.name}: last modified {mtime.astimezone().strftime('%Y-%m-%d %H:%M')}, "
            f"{len(recent)} entries dated in window"
        )
    return notes


def pc_live_status(agent: str, now: datetime) -> list[str]:
    """Live job status and models of a PC agent, from the PC-to-server sync.

    The sync also mirrors the agent's SOUL.md and scripts to pc-profile/ and
    its project files to project/, so Chief can inspect its setup here.
    """
    base = AGENTS_DIR / agent
    status_path = base / "pc-reports" / "_pc_jobs_status.json"
    if not status_path.exists():
        return ["live status: not synced yet"]
    try:
        data = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ["live status: unreadable"]
    age = ""
    synced = parse_time(data.get("synced_at"))
    if synced:
        age_h = max(0.0, (now - synced).total_seconds() / 3600)
        age = f", status last changed {age_h:.1f}h ago"
    out = [f"live status from the PC{age} (models: "
           + ", ".join(f"{k}={v}" for k, v in (data.get("models") or {}).items()) + ")"]
    for j in data.get("jobs", []):
        out.append(f"- PC job '{j.get('name')}' schedule={j.get('schedule_display')} "
                   f"last_run={str(j.get('last_run_at') or 'never')[:16]} last_status={j.get('last_status')} "
                   f"next_run={str(j.get('next_run_at') or '-')[:16]}"
                   + (f" error={ascii_safe(str(j.get('last_error')))[:160]}"
                      if j.get("last_status") not in ("ok", None) and j.get("last_error") else ""))
    out.append(f"setup mirrored at {base}/pc-profile/ (SOUL.md, scripts) and {base}/project/")
    return out


def agent_section(agent: str, cutoff: datetime, now: datetime) -> str:
    profile = PROFILES_DIR / agent
    lines = [f"===== AGENT: {agent} ====="]
    if not profile.exists() and agent not in PC_RUN_AGENTS:
        return "\n".join(lines + ["profile missing on this server"])
    if agent in PC_RUN_AGENTS:
        # Its server jobs are paused on purpose. Listing them invites "unpause
        # it" advice, which would double-run the agent and fail on YouTube's
        # datacenter block, so report only what the PC has synced here.
        lines.append("RUNS ON THE USER'S PC BY DESIGN. It has no server jobs by design - never suggest creating them here.")
        sync_dir = AGENTS_DIR / agent / "pc-reports"
        lines += pc_live_status(agent, now)
        synced = sorted(sync_dir.glob("*.md"), key=lambda p: p.stat().st_mtime) if sync_dir.exists() else []
        fresh = [p for p in synced if datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc) >= cutoff]
        if not synced:
            lines.append("synced PC reports: none yet (PC-to-server sync not set up yet - not a fault)")
        else:
            lines.append(f"synced PC reports in window: {len(fresh)} (latest: {synced[-1].name})")
            if fresh:
                body = report_body(fresh[-1])
                lines.append("      " + ascii_safe(body[:EXCERPT_CHARS]).replace("\n", "\n      "))
        for note in ledger_activity(agent, cutoff):
            lines.append(f"- {note}")
        return "\n".join(lines)

    jobs_file = profile / "cron" / "jobs.json"
    jobs = json.loads(jobs_file.read_text(encoding="utf-8")).get("jobs", []) if jobs_file.exists() else []
    if not jobs:
        lines.append("no scheduled jobs")

    for job in jobs:
        last_run = parse_time(job.get("last_run_at"))
        age = f"{(now - last_run).total_seconds() / 3600:.1f}h ago" if last_run else "never"
        lines.append(
            f"- job '{job.get('name')}' id={job.get('id')} schedule={job.get('schedule_display')} "
            f"state={job.get('state')} last_run={age} last_status={job.get('last_status')} "
            f"failure_streak={job.get('failure_streak', 0)}"
        )
        if job.get("last_error"):
            lines.append(f"    last_error: {ascii_safe(str(job['last_error']))[:300]}")
        if job.get("last_delivery_error"):
            lines.append(f"    delivery_error: {ascii_safe(str(job['last_delivery_error']))[:200]}")

        reports = recent_reports(profile / "cron" / "output" / str(job.get("id")), cutoff)
        if not reports:
            continue
        lines.append(f"    reports in window: {len(reports)}")
        if LIST_HEADLINES:
            for path in reports:
                first = next((ln.strip() for ln in report_body(path).splitlines() if ln.strip()), "")
                lines.append(f"    - {path.stem}: {ascii_safe(first)[:170]}")
        latest = reports[-1]
        body = report_body(latest)
        lines.append(f"    latest report: {latest}  ({len(body)} chars)")
        excerpt = ascii_safe(body[:EXCERPT_CHARS]).replace("\n", "\n      ")
        lines.append(f"      {excerpt}")
        if len(body) < 400:
            lines.append("    WARNING: latest report is unusually short")

    for note in ledger_activity(agent, cutoff):
        lines.append(f"- {note}")
    return "\n".join(lines)


def main(lookback_days: int = LOOKBACK_DAYS) -> None:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=lookback_days)
    header = (
        f"FLEET STATUS SNAPSHOT | generated {now.astimezone().strftime('%Y-%m-%d %H:%M %Z')} "
        f"| window: last {lookback_days} day(s)\n"
        "Read-only snapshot. Open the full report files listed below when you need more detail.\n"
    )
    sections = [agent_section(agent, cutoff, now) for agent in AGENTS]
    output = header + "\n\n".join(sections)
    if len(output) > MAX_TOTAL_CHARS:
        output = output[:MAX_TOTAL_CHARS] + "\n... [snapshot truncated - open report files directly]"
    print(output)


if __name__ == "__main__":
    days = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else LOOKBACK_DAYS
    main(days)
