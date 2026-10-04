"""Content Desk collector: raw material for the daily social post pack.

Bundles what the fleet learned today so the posts are built on real findings,
not invention: the latest Growth Scout radar, recent YouTube Watcher entries in
the SEO lanes, today's content brief, the brand proof points, and the last
14 days of the social ledger (to avoid repeating an angle). Prints plain text.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dr_check  # noqa: E402

BASE = dr_check.BASE
HOME = Path.home()
PROFILES = HOME / ".hermes" / "profiles"
AGENTS = HOME / "agents"
SOCIAL_CFG = BASE / "social-config.yaml"
SEEDS = BASE / "content-seeds.yaml"
SOCIAL_LEDGER = BASE / "social-ledger.md"
MAX_CHARS = 45_000


def ascii_safe(s: str) -> str:
    return (s or "").encode("ascii", errors="replace").decode("ascii")


def response_of(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    # Continuity jobs embed the previous run's output above the current one;
    # the current answer is the LAST "## Response".
    body = text.rsplit("## Response", 1)[1] if "## Response" in text else text
    return body.strip()


def newest_report(profile: str, job_name: str | None = None, max_age_days: int = 3) -> tuple[Path | None, str]:
    """Newest report for a profile; optionally only for one job name."""
    prof = PROFILES / profile
    jobs_file = prof / "cron" / "jobs.json"
    job_ids = None
    if job_name and jobs_file.exists():
        jobs = json.loads(jobs_file.read_text(encoding="utf-8")).get("jobs", [])
        job_ids = {j["id"] for j in jobs if j.get("name") == job_name}
    candidates = []
    out_root = prof / "cron" / "output"
    if out_root.exists():
        for job_dir in out_root.iterdir():
            if job_ids is not None and job_dir.name not in job_ids:
                continue
            candidates += list(job_dir.glob("*.md"))
    candidates = [p for p in candidates if "suppressed" not in p.read_text(encoding="utf-8", errors="replace")[:500]]
    if not candidates:
        return None, "none found"
    newest = max(candidates, key=lambda p: p.stat().st_mtime)
    age_h = (datetime.now() - datetime.fromtimestamp(newest.stat().st_mtime)).total_seconds() / 3600
    if age_h > max_age_days * 24:
        return newest, f"STALE ({age_h/24:.1f} days old)"
    return newest, f"{age_h:.1f}h old"


def kb_entries(lanes: set[str], days: int = 21) -> list[str]:
    kb = AGENTS / "youtube-watcher" / "knowledge-base.md"
    if not kb.exists():
        return []
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    blocks = re.split(r"^(?=### )", kb.read_text(encoding="utf-8", errors="replace"), flags=re.M)
    keep = []
    for b in blocks:
        lane = re.search(r"lane:\s*([a-z_]+)", b)
        date = re.search(r"date:\s*(\d{4}-\d{2}-\d{2})", b)
        if lane and date and lane.group(1) in lanes and date.group(1) >= cutoff:
            keep.append(b.strip())
    return keep


def recent_ledger(days: int = 14) -> list[str]:
    if not SOCIAL_LEDGER.exists():
        return []
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    return [ln for ln in SOCIAL_LEDGER.read_text(encoding="utf-8").splitlines()
            if re.match(r"^\d{4}-\d{2}-\d{2} \|", ln) and ln[:10] >= cutoff]


def main() -> None:
    cfg = dr_check.load_yaml(SOCIAL_CFG.read_text(encoding="utf-8")) or {}
    seeds = dr_check.load_yaml(SEEDS.read_text(encoding="utf-8")) or {}
    parts = [f"SOCIAL POST PACK INPUTS | {datetime.now().strftime('%Y-%m-%d %H:%M')} | "
             f"ledger: {SOCIAL_LEDGER}"]

    parts.append("\n===== BRAND AND RULES (social-config.yaml) =====\n" + json.dumps(cfg, indent=1))
    parts.append("\n===== PROOF POINTS fernway MAY CLAIM =====\n- " + "\n- ".join(seeds.get("proof_points") or []))

    path, note = newest_report("growth-scout", "Daily Fernway growth radar")
    parts.append(f"\n===== GROWTH SCOUT LATEST RADAR ({note}) =====")
    parts.append(ascii_safe(response_of(path))[:14_000] if path else "none")

    path, note = newest_report("blog-planner", "Daily blog ideas")
    parts.append(f"\n===== TODAY'S BLOG PLANNER IDEAS ({note}) =====")
    parts.append(ascii_safe(response_of(path))[:6_000] if path else "none yet")

    entries = kb_entries({"local_seo", "seo"})
    parts.append(f"\n===== YOUTUBE WATCHER: SEO / LOCAL SEO LEARNINGS, last 21 days ({len(entries)}) =====")
    parts.append(ascii_safe("\n\n".join(entries))[:8_000] if entries else "none in window")

    recent = recent_ledger()
    parts.append(f"\n===== ANGLES ALREADY POSTED, last 14 days ({len(recent)}) - do not repeat =====")
    parts.append("\n".join(recent) if recent else "none")

    parts.append("\n===== REMINDERS =====\n- Every claim names its source. No invented numbers, customers, or results.\n"
                 "- Group post: no product name, no link. Page and LinkedIn: one link, from link_targets.\n"
                 "- If nothing fresh exists, use an evergreen fundamental and say so in the pack header.\n"
                 f"- Append one line per post to {SOCIAL_LEDGER} in the format: YYYY-MM-DD | platform | angle | source")

    out = "\n".join(parts)
    print(out[:MAX_CHARS] + ("\n...[truncated]" if len(out) > MAX_CHARS else ""))


if __name__ == "__main__":
    main()
