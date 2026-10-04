"""Content Desk collector: demand signals and state for the daily blog brief.

US audience only: autocomplete expansions naming a non-US place or currency are dropped
(see NON_US) and reported under dropped_non_us_expansions.

Pulls real Google autocomplete expansions for every seed (forced to the US
English market), the live sitemap (so published posts are never re-proposed),
the pipeline queue, the ledger, and which lane is due this week. Prints JSON
for injection into the brief job's prompt. Read-only apart from its own cache.
"""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dr_check  # noqa: E402  (same folder)

BASE = dr_check.BASE
SEEDS = BASE / "content-seeds.yaml"
LEDGER = BASE / "content-ledger.md"
PIPELINE = BASE / "content-pipeline.md"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
AUTOCOMPLETE = "https://suggestqueries.google.com/complete/search"

# The audience is US businesses only. Google's gl=us is only a hint for autocomplete (the server is
# in Germany and SEO searches from India dominate many seeds), so expansions naming a place outside
# the USA, or using non-US money words, are dropped here before the planner ever sees them.
NON_US = [
    # countries and regions
    "india", "indian", "bangladesh", "pakistan", "nepal", "sri lanka", "uk", "u.k.", "united kingdom", "britain", "england",
    "scotland", "wales", "ireland", "australia", "aus", "new zealand", "nz", "canada", "uae", "dubai", "abu dhabi", "saudi",
    "qatar", "kuwait", "oman", "bahrain", "singapore", "malaysia", "philippines", "indonesia", "vietnam", "thailand",
    "nigeria", "kenya", "ghana", "south africa", "egypt", "germany", "france", "spain", "italy", "netherlands", "europe",
    # cities and states that show up in SEO autocomplete
    "kolkata", "calcutta", "delhi", "ncr", "noida", "gurgaon", "gurugram", "mumbai", "bombay", "bangalore", "bengaluru",
    "chennai", "hyderabad", "pune", "ahmedabad", "jaipur", "lucknow", "kochi", "cochin", "chandigarh", "indore", "nagpur",
    "surat", "bhopal", "patna", "coimbatore", "kerala", "punjab", "gujarat", "maharashtra", "tamil nadu", "karnataka",
    "west bengal", "telangana", "dhaka", "chittagong", "karachi", "lahore", "islamabad", "kathmandu", "colombo",
    "london", "manchester", "birmingham uk", "glasgow", "sydney", "melbourne", "brisbane", "perth", "adelaide",
    "auckland", "wellington", "toronto", "vancouver", "calgary", "montreal", "ottawa",
    # money and units that signal another market
    "lakh", "crore", "rupee", "rupees", "inr", "rs.", "taka", "bdt", "pkr", "gbp price", "aud", "cad",
]
_NON_US_RX = re.compile(r"(?<![a-z])(" + "|".join(re.escape(w) for w in sorted(NON_US, key=len, reverse=True)) + r")(?![a-z])|[₹৳£]", re.I)


def non_us(phrase: str) -> str | None:
    """The non-US place or market word in a query, if any ('gbp' alone is Google Business Profile, not pounds)."""
    m = _NON_US_RX.search(phrase)
    return m.group(0).lower() if m else None


def fetch(url: str, timeout: int = 20) -> str:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def autocomplete(query: str, gl: str, hl: str) -> list[str]:
    url = AUTOCOMPLETE + "?" + urllib.parse.urlencode(
        {"client": "firefox", "q": query, "gl": gl, "hl": hl})
    try:
        data = json.loads(fetch(url))
        return [s for s in data[1] if isinstance(s, str)]
    except Exception:
        return []


def published_from_sitemap(url: str) -> list[str]:
    try:
        body = fetch(url, timeout=25)
    except Exception:
        return []
    return [u for u in re.findall(r"<loc>([^<]+)</loc>", body) if "/blog/" in u and not u.rstrip("/").endswith("/blog")]


def table_rows(markdown: str, heading: str) -> list[list[str]]:
    """Rows of the first pipe table under a '## heading'."""
    section = re.split(r"^## ", markdown, flags=re.M)
    for part in section:
        if part.startswith(heading):
            rows = []
            for line in part.splitlines():
                if line.startswith("|") and not re.match(r"^\|[\s\-|]+\|$", line):
                    cells = [c.strip() for c in line.strip("|").split("|")]
                    rows.append(cells)
            return rows[1:] if rows else []
    return []


def lane_due(seeds: dict, delivered: list[list[str]]) -> dict:
    cutoff = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    counts = {lane: 0 for lane in (seeds.get("lanes") or {})}
    for row in delivered:
        if len(row) >= 4 and row[0] >= cutoff and row[3] in counts:
            counts[row[3]] += 1
    quotas = {lane: int(spec.get("per_week", 0)) for lane, spec in (seeds.get("lanes") or {}).items()}
    remaining = {lane: max(quotas[lane] - counts.get(lane, 0), 0) for lane in quotas}
    due = max(remaining, key=lambda k: remaining[k]) if remaining else None
    return {"this_week": counts, "quota": quotas, "remaining": remaining, "lane_due_today": due}


def main() -> None:
    seeds = dr_check.load_yaml(SEEDS.read_text(encoding="utf-8")) or {}
    site = seeds.get("site", {}) or {}
    gl, hl = site.get("autocomplete_gl", "us"), site.get("autocomplete_hl", "en")

    expansions, dropped = {}, {}
    for seed in seeds.get("seeds") or []:
        raw = autocomplete(seed, gl, hl)
        expansions[seed] = [q for q in raw if not non_us(q)]
        gone = [f"{q} [{non_us(q)}]" for q in raw if non_us(q)]
        if gone:
            dropped[seed] = gone
        time.sleep(0.4)

    ledger_text = LEDGER.read_text(encoding="utf-8") if LEDGER.exists() else ""
    pipeline_text = PIPELINE.read_text(encoding="utf-8") if PIPELINE.exists() else ""
    published_rows = table_rows(ledger_text, "Published")
    delivered_rows = table_rows(ledger_text, "Briefs delivered")
    rejected_rows = table_rows(ledger_text, "Rejected")
    live_posts = published_from_sitemap(site.get("sitemap", ""))
    ledger_urls = {r[2] for r in published_rows if len(r) > 2}
    unlisted = [u for u in live_posts if u not in ledger_urls]

    # Only entries under "## Queue" count; the format example above it is fenced.
    queue_section = pipeline_text.split("## Queue", 1)[1] if "## Queue" in pipeline_text else ""
    queue = re.findall(r"^### (.+)$", queue_section, flags=re.M)
    our_dr = dr_check.load_cache().get(site.get("domain", ""), {}).get("dr")
    if our_dr is None:
        key = dr_check.api_key()
        if key:
            our_dr = dr_check.fetch_dr(site.get("domain", ""), key)
            cache = dr_check.load_cache()
            cache[site.get("domain", "")] = {"dr": our_dr, "at": time.time()}
            dr_check.save_cache(cache)

    output = {
        "collected_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "market": {"gl": gl, "hl": hl, "audience": "United States only"},
        "our_domain": site.get("domain"),
        "our_dr": our_dr,
        "gate": seeds.get("gate"),
        "lanes": lane_due(seeds, delivered_rows),
        "published_posts_live": live_posts,
        "published_but_not_in_ledger": unlisted,
        "briefs_delivered_not_published": delivered_rows,
        "rejected_topics": rejected_rows,
        "pipeline_queue": queue,
        "pipeline_queue_length": len(queue),
        "pipeline_needs_topup": len(queue) < 10,
        "autocomplete_expansions": expansions,
        "expansion_count": sum(len(v) for v in expansions.values()),
        "dropped_non_us_expansions": dropped,
        "cta_targets": seeds.get("cta_targets"),
        "proof_points": seeds.get("proof_points"),
        "brief_rules": seeds.get("brief_rules"),
        "tools": {
            "dr_check": f"python {BASE / 'dr_check.py'} <domain> <domain> ...   (DR + class for SERP domains, cached)",
            "pipeline_file": str(PIPELINE),
            "ledger_file": str(LEDGER),
        },
        "reminders": [
            "Autocomplete expansions are real query phrasings, but they are demand PROXIES - never state a search volume number.",
            "AUDIENCE IS THE USA ONLY. Never propose a topic, query or example that names a place, currency or market outside the "
            "United States (dropped_non_us_expansions shows what was filtered). Location angles must use US cities or states.",
            "Read the live top 10 for the chosen query yourself, then run dr_check.py on those domains before calling it winnable.",
            "A query is only winnable if you can name the specific URL you would displace and why your page beats it.",
            "Every brief carries one first-party Fernway data angle and one CTA target.",
            "Never propose anything in published_posts_live, rejected_topics, or already in briefs_delivered_not_published.",
        ],
    }
    print(json.dumps(output, ensure_ascii=True, separators=(",", ":"), sort_keys=True))


def _learnings(agent: str) -> str:
    """What the fleet teacher learned for this agent (see ~/hq/server/learnings.py)."""
    try:
        import sys as _sys
        from pathlib import Path as _Path
        _sys.path.insert(0, str(_Path.home() / "hq" / "server"))
        import learnings
        return learnings.block(agent)
    except Exception as exc:  # never break the collector
        return "(learnings unavailable: " + str(exc) + ")"


if __name__ == "__main__":
    main()
    print(_learnings("blog-planner"))
