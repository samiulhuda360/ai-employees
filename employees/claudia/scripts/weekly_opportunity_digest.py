"""Collect recent Opportunity Scout reports for Chief Assistant.

This script is intentionally read-only. Hermes injects its stdout into the
Friday summary prompt, allowing Chief Assistant to synthesize the actual
research reports produced by the separate Opportunity Scout profile.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path


PROFILE_ROOT = Path.home() / ".hermes" / "profiles" / "opportunity-scout"
OUTPUT_ROOT = PROFILE_ROOT / "cron" / "output"

JOBS = {
    "fe63e8036714": "Daily opportunity radar",
    "d8967ae5cace": "Weekly build thesis",
    "0a4c481b8126": "Flippa commercial signal scan",
}

LOOKBACK_DAYS = 7
MAX_REPORTS = 14
MAX_CHARS_PER_REPORT = 18_000
MAX_TOTAL_CHARS = 90_000


def response_only(text: str) -> str:
    marker = "## Response"
    if marker in text:
        return text.split(marker, 1)[1].strip()
    return text.strip()


def console_safe(text: str) -> str:
    """Keep Windows scheduled-task stdout stable and ASCII-safe."""
    return text.encode("ascii", errors="replace").decode("ascii")


def main() -> None:
    cutoff = datetime.now().timestamp() - timedelta(days=LOOKBACK_DAYS).total_seconds()
    reports: list[tuple[float, str, Path]] = []

    for job_id, label in JOBS.items():
        job_dir = OUTPUT_ROOT / job_id
        if not job_dir.exists():
            continue
        for path in job_dir.glob("*.md"):
            try:
                modified = path.stat().st_mtime
            except OSError:
                continue
            if modified >= cutoff:
                reports.append((modified, label, path))

    reports.sort(key=lambda item: item[0], reverse=True)
    reports = reports[:MAX_REPORTS]

    if not reports:
        print("NO_RECENT_OPPORTUNITY_REPORTS")
        return

    chunks: list[str] = []
    total = 0
    for modified, label, path in reports:
        try:
            body = response_only(path.read_text(encoding="utf-8", errors="replace"))
        except OSError as exc:
            body = f"Could not read report: {exc}"

        body = console_safe(body[:MAX_CHARS_PER_REPORT])
        stamp = datetime.fromtimestamp(modified).astimezone().isoformat(timespec="minutes")
        chunk = f"\n===== {label} | {stamp} | {path.name} =====\n{body}\n"
        if total + len(chunk) > MAX_TOTAL_CHARS:
            break
        chunks.append(chunk)
        total += len(chunk)

    print(f"RECENT_OPPORTUNITY_REPORTS: {len(chunks)}")
    print("".join(chunks))


if __name__ == "__main__":
    main()
