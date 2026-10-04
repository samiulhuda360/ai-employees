"""Evidence for the weekly agent tune-up (Claudia, Sundays).

For each agent that can be tuned it prints: what the founder decided on its ideas in
the last 21 days (approved, done, rejected, parked, with any notes), how many ideas they
left untouched, what the fleet teacher learned for it, the rules already approved, and
past proposals so nothing is proposed twice. Claudia turns this into at most a few
proposed rules; nothing changes until the founder approves a proposal in Hermes HQ.
"""

from __future__ import annotations

import re
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

HOME = Path.home()
DB = HOME / "hq" / "hq.db"
PROFILES = HOME / ".hermes" / "profiles"
TUNABLE = {
    "blog-planner": "Blog Planner - daily blog ideas for fernway.example/blog",
    "social-planner": "Social Planner - daily post ideas",
    "growth-scout": "Growth Scout - Fernway product and growth research",
    "opportunity-scout": "Cash Builds - small tools to build and sell",
}
sys.path.insert(0, str(HOME / "hq" / "server"))


def main() -> None:
    import learnings
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    since = (datetime.now() - timedelta(days=21)).strftime("%Y-%m-%d")
    print(f"TUNE-UP EVIDENCE - {datetime.now():%Y-%m-%d}  (decisions since {since})")
    for agent, what in TUNABLE.items():
        print(f"\n################ {agent}  ({what})")
        rows = con.execute("SELECT status, type, title, note, date FROM ideas WHERE agent=? AND type!='proposal' AND status!='new' "
                           "AND COALESCE(decided_at, date) >= ? ORDER BY decided_at DESC", (agent, since)).fetchall()
        for status in ("approved", "done", "rejected", "parked"):
            mine = [r for r in rows if r["status"] == status]
            print(f"{status.upper()} ({len(mine)}):")
            for r in mine[:12]:
                print(f"  - [{r['type']}] {r['title'][:130]}" + (f"  | note: {r['note'][:160]}" if r["note"] else ""))
        untouched = con.execute("SELECT COUNT(*) FROM ideas WHERE agent=? AND status='new' AND date >= ?", (agent, since)).fetchone()[0]
        print(f"LEFT UNDECIDED: {untouched}")
        learned = learnings.for_agent(agent, days=14, limit=6)
        print(f"LEARNED FOR IT (last 14 days, {len(learned)}):")
        for e in learned:
            print(f"  - [{e['evidence']}, {e['date']}] {e['title']}: {e['what'][:260]}")
        soul = PROFILES / agent / "SOUL.md"
        rules = re.findall(r"^- \d{4}-\d\d-\d\d \[hq-\d+\] (.+)$", soul.read_text(encoding="utf-8"), re.M) if soul.exists() else []
        print(f"RULES ALREADY APPROVED ({len(rules)}):")
        for r in rules:
            print(f"  - {r}")
        past = con.execute("SELECT status, title FROM ideas WHERE agent=? AND type='proposal' ORDER BY id DESC LIMIT 12", (agent,)).fetchall()
        print(f"PAST PROPOSALS ({len(past)}; do not repeat these, whatever their status):")
        for r in past:
            print(f"  - [{r['status']}] {r['title'][:160]}")
    con.close()


if __name__ == "__main__":
    main()
