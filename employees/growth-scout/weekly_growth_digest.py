"""Collect the last seven days of Growth Scout reports for weekly synthesis."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path


PROFILE = Path.home() / ".hermes" / "profiles" / "growth-scout"
DAILY_OUTPUT = PROFILE / "cron" / "output" / "c025dc228edb"
LOOKBACK_DAYS = 7
MAX_REPORTS = 8
MAX_REPORT_CHARS = 20_000
MAX_TOTAL_CHARS = 100_000


def response_only(text: str) -> str:
    marker = "## Response"
    return text.split(marker, 1)[1].strip() if marker in text else text.strip()


def safe(text: str) -> str:
    return text.encode("ascii", errors="replace").decode("ascii")


def main() -> None:
    cutoff = datetime.now().timestamp() - timedelta(days=LOOKBACK_DAYS).total_seconds()
    paths = []
    if DAILY_OUTPUT.exists():
        for path in DAILY_OUTPUT.glob("*.md"):
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
        stamp = datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(timespec="minutes")
        chunk = f"\n===== DAILY REPORT | {stamp} | {path.name} =====\n{body[:MAX_REPORT_CHARS]}\n"
        if total + len(chunk) > MAX_TOTAL_CHARS:
            break
        chunks.append(chunk)
        total += len(chunk)

    if not chunks:
        print("NO_DAILY_GROWTH_REPORTS_FOUND")
        return
    print(f"fernway_DAILY_REPORTS: {len(chunks)}")
    print("".join(chunks))


if __name__ == "__main__":
    main()
