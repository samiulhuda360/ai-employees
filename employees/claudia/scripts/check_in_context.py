"""Context for Claudia's accountability check-ins (morning, evening, Friday review, inbox pick).

Prints plain text: the journal (plans and what got done), the task inbox, and the latest
evidence from other agents (prospects, code health). Claudia turns it into a short Telegram
check-in. No model runs here: the script only gathers what the model will read.
"""

from __future__ import annotations

import csv
import re
from datetime import datetime, timedelta
from pathlib import Path

AGENTS = Path.home() / "agents"
CHIEF = AGENTS / "chief-assistant"
DAYS = 7
EXCERPT = 1500


def ascii_safe(text: str) -> str:
    return (text or "").encode("ascii", errors="replace").decode("ascii")


def recent_journal(days: int) -> str:
    """journal.md has one '## yyyy-mm-dd' section per day with 'Plan:' and 'Done:' lines."""
    path = CHIEF / "journal.md"
    if not path.exists():
        return "(no journal yet)"
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    sections = re.split(r"^(?=## \d{4}-\d{2}-\d{2})", path.read_text(encoding="utf-8"), flags=re.M)
    keep = [s.strip() for s in sections if s.startswith("## ") and s[3:13] >= cutoff]
    return "\n\n".join(keep) or f"(no entries in the last {days} days)"


def response_of(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    body = text.rsplit("## Response", 1)[1] if "## Response" in text else text.split("---", 1)[-1]
    return body.strip()


def newest(folder: Path, prefix: str = "") -> Path | None:
    files = list(folder.glob(f"{prefix}*.md")) if folder.exists() else []
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


def age(path: Path) -> str:
    hours = (datetime.now().timestamp() - path.stat().st_mtime) / 3600
    return f"{hours:.0f}h old" if hours < 48 else f"{hours / 24:.0f} days old"


def section(title: str, body: str) -> str:
    return f"\n===== {title} =====\n{ascii_safe(body)}"


def main() -> None:
    now = datetime.now()
    out = [f"CHECK-IN CONTEXT | {now:%Y-%m-%d %A %H:%M}"]
    out.append(section(f"JOURNAL, last {DAYS} days", recent_journal(DAYS)))

    inbox = CHIEF / "task-inbox.md"
    items = [ln for ln in inbox.read_text(encoding="utf-8").splitlines() if ln.startswith("- ")] if inbox.exists() else []
    out.append(section(f"TASK INBOX ({len(items)} items)", "\n".join(items) or "(empty)"))

    ledger = AGENTS / "prospect-finder" / "prospects.csv"
    if ledger.exists():
        with ledger.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        by_status: dict[str, int] = {}
        for r in rows:
            by_status[r.get("status") or "suggested"] = by_status.get(r.get("status") or "suggested", 0) + 1
        today_rows = [r for r in rows if r.get("date") == now.strftime("%Y-%m-%d")]
        out.append(section(
            "PROSPECTS (prospect-finder/prospects.csv)",
            f"today: {len(today_rows)} new; all time by status: {by_status}\n"
            + "\n".join(f"- {r['business']} ({r['niche']}, {r['city']}): {r['signals'][:120]}" for r in today_rows[:5]),
        ))

    ch = newest(AGENTS / "code-health" / "pc-reports")
    if ch:
        body = response_of(ch)
        body = body[body.find("CODE HEALTH"):] if "CODE HEALTH" in body else body
        out.append(section(f"CODE HEALTH, latest weekly report ({age(ch)})", body[:EXCERPT]))

    print("\n".join(out))


if __name__ == "__main__":
    main()
