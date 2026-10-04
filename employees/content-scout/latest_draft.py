"""Find a planner's newest draft so its fact-check job can review it.

The fact-check job's pre-run script prints the draft; Hermes puts that output in the
prompt, and the job rewrites the draft with unsupported claims removed.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

PROFILES = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes") / "profiles"


def draft_for(profile: str, job_name: str) -> str:
    try:
        jobs = json.loads((PROFILES / profile / "cron" / "jobs.json").read_text(encoding="utf-8"))["jobs"]
    except (OSError, ValueError, KeyError):
        return f"NO DRAFT: {profile} has no jobs file."
    ids = [j["id"] for j in jobs if j.get("name") == job_name]
    out = PROFILES / profile / "cron" / "output" / ids[0] if ids else None
    files = sorted(out.glob("*.md"), key=lambda p: p.stat().st_mtime) if out and out.exists() else []
    if not files:
        return f"NO DRAFT: '{job_name}' has not run yet."
    return f"DRAFT FILE: {files[-1].name}\n\n{files[-1].read_text(encoding='utf-8', errors='replace')}"
