"""Stand-ins for demo mode (HQ_DEMO=1): no Telegram, no Hermes install, no model.

hermes() plays the few Hermes CLI commands HQ is allowed to run, against the demo home
(hq/demo/home): it edits the jobs.json files the real CLI would edit, so the normal ingest
picks the change up. ask() is a small rule-based Claudia that answers from the same live
context the real one gets and returns the same JSON, so HQ's action allowlist still checks it.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

PAGES = {
    "prospect": "/prospects", "customer": "/customers", "idea": "/ideas", "build": "/build", "write": "/write",
    "blog": "/write", "log": "/log", "audit": "/log", "journal": "/me", "me": "/me", "agent": "/agents",
    "crew": "/agents", "deck": "/", "home": "/",
}
ALIASES = {"claudia": "chief-assistant", "chief": "chief-assistant", "youtube": "youtube-watcher", "code": "code-health"}


def hermes(args: list[str], home: Path) -> tuple[int, str]:
    """`hermes pause|resume` and `hermes -p <agent> cron run|pause|resume|edit <id>`."""
    hermes_dir = home / ".hermes"
    if args in (["pause"], ["resume"]):
        estop = hermes_dir / "ESTOP"
        if args[0] == "pause":
            estop.touch()
        else:
            estop.unlink(missing_ok=True)
        return 0, f"(demo) fleet {args[0]}d"
    if len(args) < 5 or args[0] != "-p" or args[2] != "cron":
        return 1, f"(demo) not simulated: hermes {' '.join(args)}"
    agent, action, job_id = args[1], args[3], args[4]
    path = hermes_dir / "profiles" / agent / "cron" / "jobs.json"
    if not path.exists():
        return 1, f"(demo) no jobs for {agent}"
    data = json.loads(path.read_text(encoding="utf-8"))
    job = next((j for j in data["jobs"] if j["id"] == job_id), None)
    if not job:
        return 1, f"(demo) no job {job_id}"
    now = datetime.now().astimezone()
    if action == "pause":
        job.update(state="paused", enabled=False, paused_at=now.isoformat(timespec="seconds"))
    elif action == "resume":
        job.update(state="scheduled", enabled=True, paused_at=None)
    elif action == "edit" and "--schedule" in args:
        expr = args[args.index("--schedule") + 1]
        job.update(schedule={"kind": "cron", "expr": expr, "display": expr}, schedule_display=expr)
    elif action == "run":
        job.update(last_run_at=now.isoformat(timespec="seconds"), last_status="ok")
        replay_last_report(path.parent / "output" / job_id, now)
    else:
        return 1, f"(demo) unknown cron action {action}"
    path.write_text(json.dumps(data, indent=1), encoding="utf-8")
    return 0, f"(demo) {agent}: cron {action} {job_id}"


def replay_last_report(out_dir: Path, now: datetime) -> None:
    """A demo 'run now' re-issues the job's latest report with a new run time."""
    reports = sorted(out_dir.glob("*.md"))
    if not reports:
        return
    text = re.sub(r"\*\*Run Time:\*\* .+", f"**Run Time:** {now:%Y-%m-%d %H:%M:%S}", reports[-1].read_text(encoding="utf-8"))
    (out_dir / f"{now:%Y-%m-%d_%H-%M-%S}.md").write_text(text, encoding="utf-8")


def ask(said: str, page: str, overview: dict) -> str:
    """Reply as JSON {"say", "action"}, like the real Claudia."""
    s = said.lower()
    agents = overview["agents"]
    by_name = {a["name"].lower(): a["id"] for a in agents} | ALIASES
    named = next((aid for name, aid in sorted(by_name.items(), key=lambda kv: -len(kv[0])) if name in s), None)

    def reply(say: str, action: dict | None = None) -> str:
        return json.dumps({"say": say, "action": action})

    m = re.search(r"\b(approve|reject|park)\w*\b.*?\b(\d+)\b", s)
    if m:
        status = {"approve": "approved", "reject": "rejected", "park": "parked"}[m.group(1)]
        return reply(f"I have put idea {m.group(2)} on screen as {status}. Confirm it and I will record it.",
                     {"type": "idea_status", "idea_id": int(m.group(2)), "status": status})
    m = re.match(r"(?:log|note|add)\s+(?:that\s+)?(?:i\s+)?(plan|done|did|finished)\w*[:\s]+(.+)", s)
    if m:
        kind = "plan" if m.group(1) == "plan" else "done"
        return reply("Noted in today's journal.", {"type": "log", "kind": kind, "text": said[m.start(2):].strip()})
    if re.search(r"\b(run|rerun|start)\b", s) and named:
        name = next(a["name"] for a in agents if a["id"] == named)
        return reply(f"I have put a run of {name} on screen. Confirm it and it starts.", {"type": "run_job", "agent": named})
    if re.search(r"\b(go to|open|show|take me)\b", s):
        if named:
            return reply("Here you go.", {"type": "navigate", "path": f"/agents/{named}"})
        target = next((path for word, path in PAGES.items() if word in s), None)
        if target:
            return reply("Here you go.", {"type": "navigate", "path": target})
    if re.search(r"\b(first|priority|priorities|focus|should i do|next)\b", s):
        top = overview["priority"]["top"]
        if not top:
            return reply("Nothing urgent. Every open item is P2 or lower.")
        t = top[0]
        why = ", ".join(t.get("why", [])[:2]) or "it scores highest"
        return reply(f"Start with {t['title'][:90].rstrip('.')}. It is P1 because {why}. "
                     f"There are {overview['priority']['p1']} P1 items in total.")
    if re.search(r"\b(status|how is|how are|fleet|broken|failed|health|anything wrong)\b", s):
        failed = [a["name"] for a in agents if a["state"] == "failed"]
        late = [a["name"] for a in agents if a["state"] == "late"]
        say = f"{len(agents)} agents. "
        say += f"{', '.join(failed)} failed its last run. " if failed else "No failed runs. "
        say += f"{', '.join(late)} is running late. " if late else ""
        if overview.get("fallback_24h"):
            say += f"{overview['fallback_24h']} runs used the backup model today, so check their facts."
        return reply(say.strip())
    if re.search(r"\b(hi|hello|hey|morning|evening)\b", s):
        return reply("Hello. Ask me what to do first, how the fleet is doing, or say open prospects.")
    return reply("This is demo mode, so I only follow a few requests: what should I do first, how is the fleet, "
                 "open a page, run an agent, or approve an idea by number.")
