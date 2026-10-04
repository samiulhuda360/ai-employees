"""Collect the last seven days of YouTube Watcher reports for weekly synthesis.

Mirrors the digest pattern used by the other Hermes bots: it reads the daily
cron job's stored outputs, strips the prompt echo, and prints a bounded bundle
the weekly job can reason over.

The daily job id is read from cron/jobs.json by job name, so re-creating the
daily job does not break this script.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

PROFILE = Path(
    os.environ.get(
        "YOUTUBE_WATCHER_PROFILE",
        str(Path(os.environ["LOCALAPPDATA"]) / "hermes" / "profiles" / "youtube-watcher"),
    )
)
CRON = PROFILE / "cron"
DAILY_JOB_NAME = "Daily YouTube learning run"
KNOWLEDGE_BASE = Path(__file__).resolve().parent / "knowledge-base.md"

LOOKBACK_DAYS = 7
MAX_REPORTS = 8
MAX_REPORT_CHARS = 20_000
MAX_TOTAL_CHARS = 100_000


def response_only(text: str) -> str:
    marker = "## Response"
    return text.split(marker, 1)[1].strip() if marker in text else text.strip()


def safe(text: str) -> str:
    return text.encode("ascii", errors="replace").decode("ascii")


def daily_output_dir() -> Path | None:
    """Find the daily job's output folder by job name, falling back to a scan."""
    jobs_file = CRON / "jobs.json"
    try:
        jobs = json.loads(jobs_file.read_text(encoding="utf-8")).get("jobs", [])
    except (OSError, ValueError):
        jobs = []
    for job in jobs:
        if job.get("name") == DAILY_JOB_NAME and job.get("id"):
            candidate = CRON / "output" / job["id"]
            if candidate.exists():
                return candidate

    output_root = CRON / "output"
    if not output_root.exists():
        return None
    folders = [path for path in output_root.iterdir() if path.is_dir()]
    if not folders:
        return None
    return max(folders, key=lambda path: path.stat().st_mtime)


def main() -> None:
    output_dir = daily_output_dir()
    cutoff = datetime.now().timestamp() - timedelta(days=LOOKBACK_DAYS).total_seconds()

    paths = []
    if output_dir and output_dir.exists():
        for path in output_dir.glob("*.md"):
            try:
                if path.stat().st_mtime >= cutoff:
                    paths.append(path)
            except OSError:
                continue
    paths.sort(key=lambda path: path.stat().st_mtime, reverse=True)

    chunks: list[str] = []
    total = 0
    for path in paths[:MAX_REPORTS]:
        body = safe(response_only(path.read_text(encoding="utf-8", errors="replace")))
        stamp = (
            datetime.fromtimestamp(path.stat().st_mtime)
            .astimezone()
            .isoformat(timespec="minutes")
        )
        chunk = f"\n===== DAILY LEARNING REPORT | {stamp} | {path.name} =====\n{body[:MAX_REPORT_CHARS]}\n"
        if total + len(chunk) > MAX_TOTAL_CHARS:
            break
        chunks.append(chunk)
        total += len(chunk)

    if KNOWLEDGE_BASE.exists():
        try:
            entries = sum(
                1
                for line in KNOWLEDGE_BASE.read_text(encoding="utf-8", errors="replace").splitlines()
                if line.startswith("### ")
            )
            print(f"KNOWLEDGE_BASE: {KNOWLEDGE_BASE} ({entries} entries)")
        except OSError:
            pass

    if not chunks:
        print("NO_DAILY_LEARNING_REPORTS_FOUND")
        return
    print(f"YOUTUBE_WATCHER_DAILY_REPORTS: {len(chunks)}")
    print("".join(chunks))


if __name__ == "__main__":
    main()
