"""Print another agent's latest report so Chief can forward it to Telegram.

Blog Planner and Social Planner run as their own agents, but only Chief holds
the Telegram bot. Chief's relay jobs run this with --no-agent, so stdout goes to
Telegram verbatim and no model tokens are spent.

Each planner drafts on the budget model, then a fact-check job pinned to a
stronger model rewrites the draft with unsupported claims removed. The relay
sends the checked version when it exists and is newer than the draft;
otherwise it sends the draft, marked as unchecked.

Usage is fixed per wrapper script (relay_blog_ideas.py / relay_social_ideas.py),
because cron scripts cannot take arguments.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

PROFILES = Path.home() / ".hermes" / "profiles"


def newest_output(profile: str, job_name: str) -> Path | None:
    try:
        jobs = json.loads((PROFILES / profile / "cron" / "jobs.json").read_text(encoding="utf-8"))["jobs"]
    except (OSError, ValueError, KeyError):
        return None
    ids = [j["id"] for j in jobs if j.get("name") == job_name]
    out_dir = PROFILES / profile / "cron" / "output" / ids[0] if ids else None
    if not out_dir or not out_dir.exists():
        return None
    files = sorted(out_dir.glob("*.md"), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def body_of(path: Path, marker: str) -> tuple[str, bool]:
    """(deliverable, usable). Unusable = failed run, [SILENT], or no deliverable."""
    text = path.read_text(encoding="utf-8", errors="replace")
    if "(FAILED)" in (text.splitlines() or [""])[0]:
        return "", False
    body = text.rsplit("## Response", 1)[1].strip() if "## Response" in text else text.strip()
    # Models sometimes narrate before the deliverable; start at its heading.
    if marker not in body:
        return body, False
    body = body[body.index(marker):]
    # Backup-model runs are the ones that invented facts before; say so up front.
    fb = re.search(r"Provider fallback: \S+ unavailable; using (\S+?)(?: for this response)?\.?\s*$", text, re.M)
    if fb:
        body = re.sub(r"^.*Provider fallback: .*$\n?", "", body, flags=re.M).strip()
        body = f"[backup model {fb.group(1)} wrote this; double-check facts before acting]\n\n" + body
    # Models kept writing tomorrow's date in the heading; the file's own date is the truth.
    run_date = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d")
    body = re.sub(rf"^{re.escape(marker)} - [^(\n]*?(\s{{2,}}\(|\s*$|\s*\()",
                  lambda m: f"{marker} - {run_date}" + (m.group(1) if m.group(1).strip() else ""), body, count=1, flags=re.M)
    return body, True


def latest_report(profile: str, job_name: str, marker: str, review_job: str | None = None,
                  max_age_hours: float = 20) -> str:
    draft = newest_output(profile, job_name)
    if draft is None:
        return f"[relay] {profile} has not produced '{job_name}' yet."
    draft_body, draft_ok = body_of(draft, marker)
    age_h = (datetime.now().timestamp() - draft.stat().st_mtime) / 3600

    review = newest_output(profile, review_job) if review_job else None
    if review and review.stat().st_mtime >= draft.stat().st_mtime:
        review_body, review_ok = body_of(review, marker)
        if review_ok:
            return review_body

    if not draft_ok:
        return (f"[relay] {profile} '{job_name}' failed or produced no list on its latest run "
                f"({draft.name}). Ask Chief to check it.")
    note = "[unchecked: the fact-check step did not run, verify numbers and claims before posting]\n\n" \
        if review_job else ""
    if age_h > max_age_hours:
        note = f"[relay] {profile} did not run today; this is its last batch, {age_h:.0f}h old.\n" + note
    return note + draft_body


def main(profile: str, job_name: str, marker: str, review_job: str | None = None) -> None:
    text = latest_report(profile, job_name, marker, review_job)
    try:  # shorter, tidier Telegram message; the original is sent if formatting fails
        import tg_pretty
        text = tg_pretty.pretty(text)
    except Exception:  # noqa: BLE001
        pass
    print(text)
