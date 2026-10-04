"""Hand each agent what the fleet teacher (YouTube Watcher) learned for it.

YouTube Watcher appends entries to knowledge-base.md on the PC; the sync mirrors the
file to ~/agents/youtube-watcher/. Each entry names the agents it is for on a
"- for:" line (older entries without one are routed by their lane). Collectors call
block(agent) and add the result to what the agent reads at the start of a run.

    python3 learnings.py blog-planner      # print one agent's block
"""

from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

KB = Path(os.environ.get("HQ_HOME") or Path.home()) / "agents" / "youtube-watcher" / "knowledge-base.md"
AGENT_IDS = ("blog-planner", "social-planner", "growth-scout", "opportunity-scout", "prospect-finder",
             "client-wins", "code-health", "chief-assistant")
LANE_AGENTS = {
    "agents": ("chief-assistant",), "tooling": ("chief-assistant", "code-health"), "techniques": ("chief-assistant",),
    "saas": ("opportunity-scout",), "seo": ("blog-planner", "social-planner", "growth-scout"),
    "local_seo": ("blog-planner", "social-planner", "growth-scout", "prospect-finder"),
}
RANK = {"VERIFIED": 0, "DEMONSTRATED": 1, "CLAIMED": 2}


def entries() -> list[dict]:
    if not KB.exists():
        return []
    out = []
    text = KB.read_text(encoding="utf-8", errors="replace")
    for m in re.finditer(r"^### (.+?)\n(.*?)(?=^### |^## |\Z)", text, re.S | re.M):
        title, body = m.group(1).strip(), m.group(2)
        dm = re.search(r"- date:\s*(\d{4}-\d{2}-\d{2})(?:\s+lane:\s*(\S+))?", body)
        if title.startswith("<") or not dm:
            continue
        get = lambda k: (re.search(rf"^- {k}:\s*(.+?)(?=^- |\Z)", body, re.S | re.M) or [None, ""])[1]  # noqa: E731
        flat = lambda s: " ".join(s.split())  # noqa: E731
        named = [a for a in re.split(r"[,\s]+", get("for").lower()) if a]
        if "fleet" in named:
            agents = AGENT_IDS
        else:
            agents = tuple(a for a in named if a in AGENT_IDS) or LANE_AGENTS.get(dm.group(2) or "", ())
        url = re.search(r"https?://\S+", get("source"))
        ev = next((k for k in RANK if k in get("evidence")), "CLAIMED")
        out.append({"title": title, "date": dm.group(1), "lane": dm.group(2) or "", "evidence": ev, "agents": agents,
                    "what": flat(get("what")), "applies": flat(get("applies to")), "url": url.group(0).rstrip(".,)") if url else ""})
    return out


def for_agent(agent: str, days: int = 21, limit: int = 8) -> list[dict]:
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    mine = [e for e in entries() if agent in e["agents"] and e["date"] >= since]
    mine.sort(key=lambda e: (RANK[e["evidence"]], -int(e["date"].replace("-", ""))))
    return mine[:limit]


def block(agent: str) -> str:
    """Text for an agent's run. Empty string when there is nothing for it."""
    try:
        mine = for_agent(agent)
    except Exception as exc:  # noqa: BLE001  never break a collector
        return f"(learnings unavailable: {exc})"
    if not mine:
        return ""
    lines = ["", "===== LEARNINGS FOR YOU (from the fleet teacher, last 21 days) =====",
             "Use these where they fit today's work. VERIFIED and DEMONSTRATED items may shape what you recommend;",
             "a CLAIMED item is a lead to check, never a fact to state. They do not override your SOUL.md or the founder's decisions."]
    for e in mine:
        lines.append(f"- [{e['evidence']}, {e['date']}] {e['title']}: {e['what'][:420]}" + (f" (source: {e['url']})" if e["url"] else ""))
    return "\n".join(lines)


if __name__ == "__main__":
    print(block(sys.argv[1] if len(sys.argv) > 1 else "blog-planner"))
