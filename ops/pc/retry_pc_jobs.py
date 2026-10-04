"""Retry the PC agents' evening jobs once, an hour after their slot, if they failed or
never ran. Run by the Windows task "Hermes PC Retry" at 21:35 and 22:05.

For each job: if its slot today has passed by at least an hour and the job either did
not run since the slot or its last run failed, make sure the profile's gateway is up and
run the job once. A marker file stops a second retry on the same day.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path

HERMES_HOME = Path(os.environ["LOCALAPPDATA"]) / "hermes" / "profiles"
STARTUP = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
MARKERS = Path(__file__).resolve().parent / ".retry-markers"

# profile, job id, slot (HH, MM), weekday or None for daily (Mon=0)
JOBS = [
    ("youtube-watcher", "25026c8674d0", (20, 30), None),
    ("code-health", "434b49da964f", (21, 0), 0),
]


def clean_env() -> dict:
    # The Hermes gateway leaves these behind; they break other tools (see clean-npm.cmd).
    env = dict(os.environ)
    for k in ("NODE_OPTIONS", "PYTHONPATH", "VIRTUAL_ENV"):
        env.pop(k, None)
    return env


def job_state(profile: str, job_id: str) -> dict:
    data = json.loads((HERMES_HOME / profile / "cron" / "jobs.json").read_text(encoding="utf-8"))
    return next((j for j in data.get("jobs", []) if j.get("id") == job_id), {})


def gateway_up(profile: str) -> bool:
    out = subprocess.run(["hermes", "-p", profile, "gateway", "status"], capture_output=True, text=True, encoding="utf-8", errors="replace",
                         env=clean_env(), timeout=60, shell=True).stdout or ""
    return "Gateway process running" in out


def start_gateway(profile: str) -> None:
    vbs = STARTUP / f"Hermes_Gateway_{profile}.vbs"
    if vbs.exists():
        subprocess.Popen(["wscript.exe", str(vbs)])
        time.sleep(15)


def main() -> None:
    now = datetime.now()
    MARKERS.mkdir(exist_ok=True)
    for profile, job_id, (hh, mm), weekday in JOBS:
        if weekday is not None and now.weekday() != weekday:
            continue
        slot = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if now < slot + timedelta(hours=1):
            continue
        marker = MARKERS / f"{profile}-{now:%Y-%m-%d}"
        if marker.exists():
            continue
        j = job_state(profile, job_id)
        last = j.get("last_run_at")
        try:
            ran = last and datetime.fromisoformat(last).replace(tzinfo=None) >= slot
        except ValueError:
            ran = False
        failed = j.get("last_status") not in (None, "ok")
        if ran and not failed:
            continue
        reason = "failed" if ran else "did not run"
        if not gateway_up(profile):
            start_gateway(profile)
        r = subprocess.run(["hermes", "-p", profile, "cron", "run", job_id], capture_output=True, text=True, encoding="utf-8", errors="replace",
                           env=clean_env(), timeout=120, shell=True)
        marker.write_text(f"{now:%H:%M} {reason}; retry exit {r.returncode}\n{r.stdout[-400:]}", encoding="utf-8")
        print(f"{now:%H:%M} {profile}: {reason}, retried (exit {r.returncode})")


if __name__ == "__main__":
    main()
