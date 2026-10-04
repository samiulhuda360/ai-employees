"""Code Health Watcher collector: raw findings for the weekly fix list.

For each project in projects.yaml it checks, using only the standard library:

- known vulnerabilities in locked dependencies (npm package-lock.json,
  composer.lock, pinned requirements.txt) via the free OSV.dev API
- hardcoded secrets in tracked source files (key-shaped strings, not .env files)
- .env or credential files committed to git
- no git at all (no history, no backup), uncommitted work, last commit age

Prints plain text for the agent, which turns it into a short, ranked fix list.
"""

from __future__ import annotations

import json
import re
import subprocess
import urllib.request
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG = HERE / "projects.yaml"
CACHE = HERE / "osv_cache.json"
SKIP_DIRS = {"node_modules", "vendor", ".git", "__pycache__", ".venv", "venv", "dist",
             "build", ".next", ".pytest_cache", "storage", "cache", "site-packages"}
SOURCE_EXT = {".py", ".js", ".ts", ".tsx", ".jsx", ".php", ".json", ".yaml", ".yml",
              ".html", ".vue", ".mjs", ".cjs", ".ini", ".conf", ".sh", ".ps1", ".bat"}
SECRET_PATTERNS = {
    "AI provider key": re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),
    "OpenAI key": re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9]{32,}"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    "Google API key": re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),
    "Stripe secret key": re.compile(r"\bsk_live_[0-9A-Za-z]{24,}\b"),
    "Private key block": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
}
MAX_FILE_BYTES = 400_000
MAX_FILES_PER_PROJECT = 6000


def load_projects() -> list[Path]:
    # A flat "- path" list; parsed by hand to stay dependency-free.
    paths = []
    for line in CONFIG.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\s*-\s*['\"]?(.+?)['\"]?\s*$", line)
        if m:
            paths.append(Path(m.group(1)))
    return paths


def walk(root: Path):
    count = 0
    stack = [root]
    while stack:
        d = stack.pop()
        try:
            entries = list(d.iterdir())
        except OSError:
            continue
        for e in entries:
            if e.is_dir():
                # Skip hidden dirs, dependency dirs and any Python virtualenv.
                if (e.name not in SKIP_DIRS and not e.name.startswith(".")
                        and not (e / "pyvenv.cfg").exists()):
                    stack.append(e)
            else:
                count += 1
                if count > MAX_FILES_PER_PROJECT:
                    return
                yield e


# ---------- dependencies ----------

def deps_from_package_lock(path: Path) -> list[tuple[str, str, str, bool]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for key, meta in (data.get("packages") or {}).items():
        if not key or "node_modules/" not in key or not meta.get("version"):
            continue
        name = key.rsplit("node_modules/", 1)[1]
        out.append(("npm", name, meta["version"], bool(meta.get("dev"))))
    if not out:  # lockfile v1
        def rec(deps):
            for name, meta in (deps or {}).items():
                if meta.get("version"):
                    out.append(("npm", name, meta["version"], bool(meta.get("dev"))))
                rec(meta.get("dependencies"))
        rec(data.get("dependencies"))
    return out


def deps_from_composer_lock(path: Path) -> list[tuple[str, str, str, bool]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for section, dev in (("packages", False), ("packages-dev", True)):
        for p in data.get(section) or []:
            v = str(p.get("version", "")).lstrip("v")
            if p.get("name") and v and not v.startswith("dev-"):
                out.append(("Packagist", p["name"], v, dev))
    return out


def deps_from_requirements(path: Path) -> list[tuple[str, str, str, bool]]:
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"^\s*([A-Za-z0-9_.\-\[\]]+)\s*==\s*([0-9][^\s;#]*)", line)
        if m:
            out.append(("PyPI", re.sub(r"\[.*\]", "", m.group(1)), m.group(2), False))
    return out


def unpinned_requirements(path: Path) -> int:
    n = 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.split("#")[0].strip()
        if s and not s.startswith("-") and "==" not in s:
            n += 1
    return n


def osv_batch(queries: list[dict]) -> list[list[str]]:
    results = []
    for i in range(0, len(queries), 900):
        body = json.dumps({"queries": queries[i:i + 900]}).encode()
        req = urllib.request.Request("https://api.osv.dev/v1/querybatch", data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read())
        for res in data.get("results", []):
            results.append([v["id"] for v in res.get("vulns", []) or []])
    return results


def osv_details(ids: set[str]) -> dict:
    try:
        cache = json.loads(CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    for vid in sorted(ids - cache.keys())[:250]:
        try:
            with urllib.request.urlopen(f"https://api.osv.dev/v1/vulns/{vid}", timeout=30) as r:
                v = json.loads(r.read())
        except Exception:
            continue
        sev = (v.get("database_specific") or {}).get("severity") or ""
        fixed = []
        for aff in v.get("affected", []):
            for rng in aff.get("ranges", []):
                fixed += [e["fixed"] for e in rng.get("events", []) if "fixed" in e]
        cache[vid] = {"summary": (v.get("summary") or v.get("details") or "")[:140],
                      "severity": sev, "fixed": sorted(set(fixed))[:3],
                      "aliases": [a for a in v.get("aliases", []) if a.startswith("CVE")][:1]}
    CACHE.write_text(json.dumps(cache), encoding="utf-8")
    return cache


def vkey(v: str) -> tuple:
    return tuple(int(x) if x.isdigit() else 0 for x in re.split(r"[.\-+]", v)[:4])


def relevant_fixes(current: str, fixes: set[str]) -> list[str]:
    """Fixed versions on the installed major line and newer than it; else the lowest newer one."""
    cur = vkey(current)
    newer = sorted((f for f in fixes if vkey(f) > cur), key=vkey)
    same_major = [f for f in newer if vkey(f)[:1] == cur[:1]]
    return (same_major or newer)[:1]


# ---------- git and secrets ----------

def git(root: Path, *args: str) -> str:
    try:
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=60).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


CODE_MARKERS = ("package.json", "composer.json", "requirements.txt", "pyproject.toml", "index.php", "manage.py", "app.py")


def sub_repos(root: Path, depth: int = 3) -> list[Path]:
    """Git repositories inside a project folder (many projects keep repos one level down)."""
    found, stack = [], [(root, 0)]
    while stack:
        d, lvl = stack.pop()
        try:
            children = list(d.iterdir())
        except OSError:
            continue
        for c in children:
            if not c.is_dir() or c.name in SKIP_DIRS - {".git"}:
                continue
            if (c / ".git").exists():
                found.append(c)
            elif lvl + 1 < depth and not c.name.startswith("."):
                stack.append((c, lvl + 1))
    return sorted(found)


def git_facts(root: Path) -> list[str]:
    if not (root / ".git").exists():
        repos = sub_repos(root)
        if not repos:
            return ["NO GIT: no repository in this folder or its subfolders (no local history)"]
        facts = [f"git repos inside: {', '.join(str(r.relative_to(root)) for r in repos)}"]
        for r in repos:
            facts += [f"[{r.relative_to(root)}] {f}" for f in git_facts(r)]
        # Code folders directly under the project that sit in no repository.
        loose = [c.name for c in root.iterdir()
                 if c.is_dir() and not c.name.startswith(".") and c.name not in SKIP_DIRS
                 and not any(c == r or c in r.parents or r in c.parents for r in repos)
                 and any((c / m).exists() for m in CODE_MARKERS)]
        if loose:
            facts.append(f"code folders not in any repo: {', '.join(sorted(loose))}")
        return facts
    facts = []
    last = git(root, "log", "-1", "--format=%cs %s")
    facts.append(f"last commit: {last or 'none'}")
    dirty = [ln for ln in git(root, "status", "--porcelain").splitlines() if ln.strip()]
    if dirty:
        facts.append(f"uncommitted changes: {len(dirty)} files")
    tracked = git(root, "ls-files").splitlines()
    bad = [f for f in tracked if re.search(r"(^|/)\.env($|\.)(?!example|sample|template)", f)
           or re.search(r"\.(pem|key|p12|pfx)$", f) or f.endswith("id_rsa")]
    if bad:
        facts.append("COMMITTED SECRET FILES: " + ", ".join(bad[:8]))
    if not git(root, "remote"):
        facts.append("no git remote: commits exist only on this PC")
    return facts


def secret_hits(root: Path) -> list[str]:
    hits = []
    for f in walk(root):
        if f.suffix.lower() not in SOURCE_EXT or f.name.startswith(".env"):
            continue
        if "example" in f.name.lower() or f.name.endswith(".lock") or f.name == "package-lock.json":
            continue
        try:
            if f.stat().st_size > MAX_FILE_BYTES:
                continue
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for label, pat in SECRET_PATTERNS.items():
            m = pat.search(text)
            if m:
                line_no = text[:m.start()].count("\n") + 1
                hits.append(f"{label} in {f.relative_to(root)}:{line_no}")
                break
        if len(hits) >= 10:
            break
    return hits


# ---------- main ----------

def main() -> None:
    projects = [p for p in load_projects() if p.exists()]
    print(f"CODE HEALTH INPUTS | {datetime.now():%Y-%m-%d %H:%M} | {len(projects)} projects")

    per_project: dict[Path, dict] = {}
    all_deps = []
    for root in projects:
        info = {"git": git_facts(root), "secrets": secret_hits(root), "deps": [],
                "unpinned": [], "lockfiles": []}
        for f in walk(root):
            try:
                if f.name == "package-lock.json":
                    d = deps_from_package_lock(f)
                elif f.name == "composer.lock":
                    d = deps_from_composer_lock(f)
                elif f.name == "requirements.txt":
                    d = deps_from_requirements(f)
                    n = unpinned_requirements(f)
                    if n:
                        info["unpinned"].append(f"{f.relative_to(root)}: {n} unpinned (cannot be checked)")
                else:
                    continue
            except (OSError, ValueError) as e:
                info["lockfiles"].append(f"{f.relative_to(root)}: unreadable ({e})")
                continue
            info["lockfiles"].append(f"{f.relative_to(root)}: {len(d)} packages")
            for eco, name, ver, dev in d:
                info["deps"].append((str(f.relative_to(root)), eco, name, ver, dev))
                all_deps.append((eco, name, ver))
        per_project[root] = info

    unique = sorted(set(all_deps))
    vuln_map: dict[tuple, list[str]] = {}
    osv_error = ""
    if unique:
        try:
            res = osv_batch([{"package": {"name": n, "ecosystem": e}, "version": v}
                             for e, n, v in unique])
            vuln_map = {dep: ids for dep, ids in zip(unique, res) if ids}
        except Exception as e:
            osv_error = f"OSV lookup failed: {e}"
    details = osv_details({i for ids in vuln_map.values() for i in ids}) if vuln_map else {}
    if osv_error:
        print(osv_error)

    for root, info in per_project.items():
        print(f"\n===== {root} =====")
        for fact in info["git"]:
            print(f"- {fact}")
        for s in info["secrets"]:
            print(f"- HARDCODED SECRET: {s}")
        print("- dependency files: " + ("; ".join(info["lockfiles"]) or "none"))
        for u in info["unpinned"]:
            print(f"- {u}")
        seen = set()
        vulns = []
        for lock, eco, name, ver, dev in info["deps"]:
            ids = vuln_map.get((eco, name, ver))
            if not ids or (name, ver) in seen:
                continue
            seen.add((name, ver))
            worst = ""
            fixes = set()
            for vid in ids:
                d = details.get(vid, {})
                worst = worst or d.get("severity", "")
                fixes.update(d.get("fixed", []))
            first = details.get(ids[0], {})
            vulns.append(f"  {eco} {name} {ver}{' (dev)' if dev else ''} [{lock}] - {len(ids)} advisories"
                         f"{', severity ' + worst if worst else ''}"
                         f"{', upgrade to ' + relevant_fixes(ver, fixes)[0] if relevant_fixes(ver, fixes) else ''}"
                         f" - {first.get('summary', '')}")
        print(f"- vulnerable packages: {len(vulns)}")
        for v in vulns[:25]:
            print(v)
        if len(vulns) > 25:
            print(f"  ...and {len(vulns) - 25} more")


if __name__ == "__main__":
    main()
