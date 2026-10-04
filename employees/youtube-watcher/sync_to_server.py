"""One-way sync of every PC-only Hermes agent to the Hermes server.

Some agents must run on the PC: YouTube Watcher (YouTube blocks datacenter
IPs) and Code Health (the repos are local).
Chief Assistant runs on the server and reports on every agent, so for each PC
agent this pushes, under ~/agents/<agent>/ on the server:

  pc-reports/     every cron report, plus _pc_jobs_status.json (live job state and models)
  pc-profile/     SOUL.md, profile.yaml, scripts/*.py (how the agent is set up)
  project/        the agent's project files (code, config, data snapshots)

config.yaml and .env are never copied: they can hold tokens.

Only new or changed files are sent. Safe to run as often as you like; if the
server is unreachable it logs and exits, and the next run retries.

Run by the Windows scheduled task "Hermes YouTube Sync" (the name dates from
when it synced only YouTube Watcher).
"""

from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
import tarfile
from datetime import datetime
from pathlib import Path

SERVER = "hermes@hq.example.com"
SSH_KEY = Path.home() / ".ssh" / "id_ed25519"
REMOTE_ROOT = "agents"                          # relative to the server user's home

PROJECT = Path(__file__).resolve().parent       # <project>\youtube-watcher
HERMES_PROJECTS = PROJECT.parent                 # <project>
PROFILES = Path(os.environ["LOCALAPPDATA"]) / "hermes" / "profiles"
PROFILE = PROFILES / "youtube-watcher"
CHIEF_PROFILE = PROFILES / "chief-assistant"
STATE = PROFILE / "server-sync-state.json"
LOG = PROFILE / "logs" / "server-sync.log"

# PC agent -> its project folder (None when it has none).
PC_AGENTS = {
    "youtube-watcher": PROJECT,
    "code-health": HERMES_PROJECTS / "code-health",
}
# Agents whose reports are keyed by job id (Chief's PC reports use job names).
PC_RELAY_AGENTS = ("code-health",)
PROJECT_SUFFIXES = {".py", ".md", ".yaml", ".yml", ".json", ".txt"}
PROJECT_SKIP = {"server-sync-state.json", "osv_cache.json"}
MAX_PROJECT_FILE = 2_000_000

# Windows' own OpenSSH works from Task Scheduler without Git Bash on PATH.
SSH_EXE = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "OpenSSH" / "ssh.exe"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def log(message: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {message}\n")
    # Keep the log small.
    try:
        lines = LOG.read_text(encoding="utf-8").splitlines()
        if len(lines) > 500:
            LOG.write_text("\n".join(lines[-300:]) + "\n", encoding="utf-8")
    except OSError:
        pass


def slug(text: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in text.lower()).strip("-")


def read_jobs(profile: Path) -> list[dict] | None:
    try:
        return json.loads((profile / "cron" / "jobs.json").read_text(encoding="utf-8-sig"))["jobs"]
    except (OSError, ValueError, KeyError):
        return None


def model_summary(profile: Path) -> dict:
    """Model settings, read without copying config.yaml (it can hold tokens)."""
    try:
        text = (profile / "config.yaml").read_text(encoding="utf-8-sig")
    except OSError:
        return {}
    out, section = {}, ""
    for line in text.splitlines():
        top = re.match(r"^([a-z_]+):", line)
        if top:
            section = top.group(1)
            continue
        m = re.match(r"^\s+-?\s*(default|model|provider|model_provider):\s*(\S+)", line)
        if m and section in ("model", "cron", "fallback_providers"):
            out.setdefault(f"{section}.{m.group(1)}", m.group(2))
    return out


def write_status(agent: str, profile: Path) -> Path | None:
    """Live job status for one PC agent.

    Monitor-style jobs only write a report when something changed, so an old
    failed report can stay "latest" for days after the cause is fixed. The live
    status tells the server what is true right now. Rewritten only on change,
    so the sync stays idle otherwise.
    """
    jobs = read_jobs(profile)
    if jobs is None:
        return None
    status = [{k: j.get(k) for k in ("id", "name", "state", "schedule_display", "last_run_at",
                                     "last_status", "last_error", "next_run_at")}
              for j in jobs if j.get("state") != "paused"]
    body = {"jobs": status, "models": model_summary(profile)}
    status_file = profile / "pc-jobs-status.json"
    try:
        old = json.loads(status_file.read_text(encoding="utf-8"))
        changed = {"jobs": old.get("jobs"), "models": old.get("models")} != body
    except (OSError, ValueError):
        changed = True
    if changed:
        body = {"synced_at": datetime.now().astimezone().isoformat(timespec="seconds"), **body}
        status_file.write_text(json.dumps(body, indent=1), encoding="utf-8")
    return status_file


def mirror_agent(agent: str, project: Path | None, mapping: dict) -> None:
    """Status, setup and project files of one PC agent."""
    profile = PROFILES / agent
    if not profile.exists():
        return
    status_file = write_status(agent, profile)
    if status_file:
        rel = f"{agent}/pc-reports/_pc_jobs_status.json"
        mapping[rel] = (status_file, rel)
    for name in ("SOUL.md", "profile.yaml"):
        f = profile / name
        if f.exists():
            rel = f"{agent}/pc-profile/{name}"
            mapping[rel] = (f, rel)
    scripts = profile / "scripts"
    for f in scripts.glob("*.py") if scripts.exists() else []:
        rel = f"{agent}/pc-profile/scripts/{f.name}"
        mapping[rel] = (f, rel)
    if project and project.exists():
        for f in project.iterdir():
            if (f.is_file() and f.suffix.lower() in PROJECT_SUFFIXES and f.name not in PROJECT_SKIP
                    and f.stat().st_size <= MAX_PROJECT_FILE):
                rel = f"{agent}/project/{f.name}"
                mapping[rel] = (f, rel)


def files_to_sync() -> dict[str, tuple[Path, str]]:
    """State key -> (local file, path relative to ~/agents on the server).

    YouTube Watcher report keys keep their original form so a state file from
    before this change stays valid and nothing already synced is resent.
    """
    mapping: dict[str, tuple[Path, str]] = {}

    # --- YouTube Watcher: knowledge base and watchlist at the agent root
    # (the server's content collectors read them there), plus reports.
    for name in ("knowledge-base.md", "watchlist.yaml"):
        source = PROJECT / name
        if source.exists():
            mapping[name] = (source, f"youtube-watcher/{name}")
    output_root = PROFILE / "cron" / "output"
    if output_root.exists():
        for job_dir in output_root.iterdir():
            if not job_dir.is_dir():
                continue
            for report in job_dir.glob("*.md"):
                # Prefix with the job id so daily and weekly reports never collide.
                key = f"pc-reports/{job_dir.name}__{report.name}"
                mapping[key] = (report, f"youtube-watcher/{key}")

    # --- Chief on the PC: project follow-up reports, named by job.
    jobs = read_jobs(CHIEF_PROFILE) or []
    names = {j["id"]: j.get("name", j["id"]) for j in jobs if j.get("state") != "paused"}
    chief_out = CHIEF_PROFILE / "cron" / "output"
    for job_id, name in names.items():
        job_dir = chief_out / job_id
        if not job_dir.is_dir():
            continue
        for report in job_dir.glob("*.md"):
            try:
                head = report.read_text(encoding="utf-8", errors="replace")[:600]
            except OSError:
                continue
            if "agent run suppressed" in head:   # monitor tick with nothing new
                continue
            rel = f"chief-assistant/pc-reports/{slug(name)}__{report.name}"
            mapping[rel] = (report, rel)

    # --- Reports of the other PC agents, relayed to Telegram by the server's Chief.
    for agent in PC_RELAY_AGENTS:
        out = PROFILES / agent / "cron" / "output"
        if not out.exists():
            continue
        for job_dir in out.iterdir():
            for report in job_dir.glob("*.md") if job_dir.is_dir() else []:
                rel = f"{agent}/pc-reports/{job_dir.name}__{report.name}"
                mapping[rel] = (report, rel)

    # --- Live status, setup and project files of every PC agent.
    for agent, project in PC_AGENTS.items():
        mirror_agent(agent, project, mapping)
    # The knowledge base and watchlist already go to the YouTube Watcher root.
    for name in ("knowledge-base.md", "watchlist.yaml"):
        mapping.pop(f"youtube-watcher/project/{name}", None)
    return mapping


def fingerprint(path: Path) -> str:
    stat = path.stat()
    return f"{stat.st_mtime_ns}:{stat.st_size}"


def main() -> int:
    try:
        # utf-8-sig: tolerate a byte-order mark if the file was ever saved by
        # a Windows editor or PowerShell, instead of silently resending everything.
        state = json.loads(STATE.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        state = {}

    mapping = files_to_sync()
    changed = {key: (local, rel) for key, (local, rel) in mapping.items()
               if state.get(key) != fingerprint(local)}
    if not changed:
        return 0

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for key, (local, rel) in sorted(changed.items()):
            archive.add(local, arcname=rel)

    command = [
        str(SSH_EXE if SSH_EXE.exists() else "ssh"),
        "-i", str(SSH_KEY),
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=15",
        "-o", "StrictHostKeyChecking=accept-new",
        SERVER,
        "mkdir -p " + " ".join(f"{REMOTE_ROOT}/{agent}" for agent in PC_AGENTS)
        + f" && tar -xzf - -C {REMOTE_ROOT}",
    ]
    try:
        result = subprocess.run(command, input=buffer.getvalue(), capture_output=True,
                                timeout=120, creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log(f"FAILED to reach server: {type(exc).__name__}: {exc}")
        return 1
    if result.returncode != 0:
        log(f"FAILED (exit {result.returncode}): {result.stderr.decode(errors='replace').strip()[:300]}")
        return 1

    for key, (local, _rel) in changed.items():
        state[key] = fingerprint(local)
    STATE.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")
    log(f"synced {len(changed)} file(s): {', '.join(sorted(changed))[:400]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
