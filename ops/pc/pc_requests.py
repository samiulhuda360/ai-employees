"""Run the PC agents' jobs when Hermes HQ asks for a recheck.

HQ (on the server) cannot reach into this PC, so its "Recheck now" button drops a
request file on the server: ~/agents/<agent>/pc-requests/<job_id>.req. This script, run
every 5 minutes by the Windows task "Hermes PC Requests", collects those requests over
SSH, runs each job once, and a few minutes later runs the normal sync so the fresh
report reaches the server and HQ.

Only jobs that exist in this PC's own Hermes profiles are run; anything else is ignored.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path

SERVER = "hermes@hq.example.com"
PROFILES = Path(os.environ["LOCALAPPDATA"]) / "hermes" / "profiles"
STARTUP = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
STATE = Path(__file__).resolve().parent / ".retry-markers"
PENDING_SYNC = STATE / "pending-sync"
PC_AGENTS = ("youtube-watcher", "code-health")
SYNC_TASK = "Hermes YouTube Sync"


def env() -> dict:
    e = dict(os.environ)
    for k in ("NODE_OPTIONS", "PYTHONPATH", "VIRTUAL_ENV"):
        e.pop(k, None)
    return e


def ssh(cmd: str) -> str:
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", SERVER, cmd],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    return r.stdout


LOCKFILES = ("package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml", "composer.lock",
             "requirements.txt", "poetry.lock", "Pipfile.lock", "uv.lock", "Gemfile.lock", "go.sum", "Cargo.lock")
SKIP_DIRS = {"node_modules", ".git", "vendor", ".venv", "venv", "dist", "build", "__pycache__", ".next"}


def lock_has(root: Path, pkg: str, ver: str) -> list[str]:
    """Lock files under root (4 levels deep) that still pin pkg at ver."""
    import re
    hits = []
    p, v = re.escape(pkg), re.escape(ver)
    pats = [
        re.compile(rf'"(?:node_modules/)?(?:[^"]*/node_modules/)?{p}"\s*:\s*\{{\s*"version"\s*:\s*"{v}"'),   # npm
        re.compile(rf'"name"\s*:\s*"{p}"\s*,\s*"version"\s*:\s*"v?{v}"'),                                   # composer
        re.compile(rf'(?im)^{p}\s*==\s*{v}\b'),                                                                # pip
        re.compile(rf'(?m)^"?{p}@[^\n]*:\n\s+version:?\s+"?{v}"?'),                                           # yarn / pnpm
        re.compile(rf'name\s*=\s*"{p}"\s*\nversion\s*=\s*"{v}"'),                                           # poetry / cargo / uv
    ]
    base = len(root.parts)
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if x not in SKIP_DIRS and len(Path(d).parts) - base < 4]
        for f in files:
            if f in LOCKFILES:
                try:
                    text = (Path(d) / f).read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                if any(pat.search(text) for pat in pats):
                    hits.append(str(Path(d) / f))
    return hits


def check_issue(title: str) -> dict | None:
    """Look straight at what the issue names. Returns {"fixed": bool, "detail": str}, or
    None when the title gives nothing checkable (then the whole audit is re-run instead)."""
    import re
    if ": " not in title:
        return None
    root_s, rest = title.split(": ", 1)
    root = Path(root_s.strip())
    if not root.exists():
        return {"fixed": True, "detail": f"{root} no longer exists"}
    sub = re.match(r"([A-Za-z0-9_.-]+) has\b", rest)  # "fernway-app has a CRITICAL ..." names the sub-project
    if sub and (root / sub.group(1)).is_dir():
        root = root / sub.group(1)
    checks, problems = [], []
    for pkg, ver in re.findall(r"([A-Za-z0-9@][A-Za-z0-9@/_.-]*)\s+v?(\d+\.\d+(?:\.\d+)?(?:[-.\w]*)?)", rest):
        ver = ver.rstrip(".")
        if pkg.lower() in ("since", "in", "to", "version", "python", "node", "php"):
            continue
        hits = lock_has(root, pkg, ver)
        checks.append(f"{pkg} {ver}")
        if hits:
            problems.append(f"{pkg} {ver} is still in {Path(hits[0]).relative_to(root)}")
    m = re.search(r"(\d+) uncommitted", rest)
    if m:
        r = subprocess.run(["git", "-C", str(root), "status", "--porcelain"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=60)
        n = len([ln for ln in r.stdout.splitlines() if ln.strip()]) if r.returncode == 0 else -1
        checks.append("uncommitted files")
        if n != 0:
            problems.append(f"{n} uncommitted files" if n > 0 else "could not read git status")
    if not checks:
        return None
    if problems:
        return {"fixed": False, "detail": "; ".join(problems)}
    # A vulnerability named without a version cannot be confirmed fixed from here.
    if re.search(r"vulnerab", rest, re.I) and not any(" " in c and c != "uncommitted files" for c in checks):
        return None
    if problems:
        return {"fixed": False, "detail": "; ".join(problems)}
    return {"fixed": True, "detail": f"{', '.join(checks)} no longer found in {root}"}


def known_jobs() -> dict[tuple[str, str], str]:
    out = {}
    for agent in PC_AGENTS:
        f = PROFILES / agent / "cron" / "jobs.json"
        if f.exists():
            for j in json.loads(f.read_text(encoding="utf-8")).get("jobs", []):
                out[(agent, j["id"])] = j.get("name", "")
    return out


def main() -> None:
    STATE.mkdir(exist_ok=True)
    now = datetime.now()

    # A job we started a few minutes ago has probably finished: push its report to the server.
    if PENDING_SYNC.exists() and time.time() - PENDING_SYNC.stat().st_mtime > 360:
        subprocess.run(["schtasks", "/run", "/tn", SYNC_TASK], capture_output=True)
        PENDING_SYNC.unlink(missing_ok=True)
        print(f"{now:%H:%M} sync started")

    listing = ssh("ls ~/agents/*/pc-requests/*.req 2>/dev/null")
    jobs = known_jobs()
    for path in [p.strip() for p in listing.splitlines() if p.strip()]:
        parts = path.split("/")
        agent, job_id = parts[-3], parts[-1][:-4]
        if job_id.startswith("issue-"):  # one specific issue: look at it directly, answer at once
            body = ssh(f"cat '{path}'; rm -f '{path}'")
            try:
                req = json.loads(body)
                result = check_issue(req["title"])
            except Exception as exc:  # noqa: BLE001
                result, req = {"fixed": False, "detail": f"check failed: {exc}"}, {"id": job_id[6:]}
            if result is None:  # nothing checkable in the title: fall back to the full audit
                job = next((jid for (a, jid) in jobs if a == agent), None)
                if job:
                    subprocess.Popen(["hermes", "-p", agent, "cron", "run", job], env=env(), shell=True,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    PENDING_SYNC.write_text(f"{agent}/{job} {now:%Y-%m-%d %H:%M}", encoding="utf-8")
                print(f"{now:%H:%M} issue {req['id']}: not directly checkable, full audit started")
                continue
            payload = json.dumps(result).replace("'", "")
            ssh(f"mkdir -p ~/agents/{agent}/pc-requests/results && echo '{payload}' > ~/agents/{agent}/pc-requests/results/{int(req['id'])}.json")
            print(f"{now:%H:%M} issue {req['id']}: {'FIXED' if result['fixed'] else 'still there'} - {result['detail']}")
            continue
        ssh(f"rm -f '{path}'")  # take the request off the queue first: never run it twice
        if (agent, job_id) not in jobs:
            print(f"{now:%H:%M} ignored unknown request {agent}/{job_id}")
            continue
        status = subprocess.run(["hermes", "-p", agent, "gateway", "status"], capture_output=True, text=True, encoding="utf-8", errors="replace",
                                env=env(), timeout=60, shell=True).stdout or ""
        if "Gateway process running" not in status:
            vbs = STARTUP / f"Hermes_Gateway_{agent}.vbs"
            if vbs.exists():
                subprocess.Popen(["wscript.exe", str(vbs)])
                time.sleep(15)
        subprocess.Popen(["hermes", "-p", agent, "cron", "run", job_id], env=env(), shell=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        PENDING_SYNC.write_text(f"{agent}/{job_id} {now:%Y-%m-%d %H:%M}", encoding="utf-8")
        print(f"{now:%H:%M} started {agent} / {jobs[(agent, job_id)]}")


if __name__ == "__main__":
    main()
