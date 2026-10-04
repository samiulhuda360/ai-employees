"""Novelty memory and daily lane rotation for the two scouts.

Both scouts kept recommending the same thing: Growth Scout four GBP-verification
variants in a row, Opportunity Scout the same CV tool two days running. Their inputs
change slowly and they re-read their own notes, so nothing pushed them elsewhere.

This prints a block the collectors append to their output:
  - TODAY'S LANE: the area today's primary pick must come from (rotates by weekday)
  - ALREADY RECOMMENDED: every primary pick in the last 30 days, from hq.db
  - the rules that make a repeat the exception, not the default
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

DB = Path(os.environ.get("HQ_HOME") or Path.home()) / "hq" / "hq.db"

LANES = {
    "growth-scout": [
        # Monday .. Sunday. Each lane maps to a part of Fernway a customer pays for.
        ("Google Business Profile and Maps", "GBP optimisation, verification, suspensions, categories, reviews, map-pack visibility"),
        ("Citations and NAP", "citation building, directories, NAP consistency, duplicate listings, data aggregators"),
        ("Blog placements and indexing", "niche and city blog placements, link quality, indexing speed, anchor text, the 140-site network"),
        ("Rank tracking and reporting", "geo-grid scans, rank reports, white-label reporting, proof of results for agencies"),
        ("Conversion, pricing and retention", "signup to first purchase, pricing, credits, auto-top-up, churn, the at-risk and not-yet-paying accounts"),
        ("Competitors and AI visibility", "BrightLocal, Local Falcon, Whitespark, Nearby Now, Grid My Business; AI search and LLM visibility"),
        ("Free tools and lead magnets", "free audits, calculators, checkers, embeddable widgets that bring agencies to Fernway"),
    ],
    "opportunity-scout": [
        # Cash Builds (2026-09-28): small tools one developer ships in five days and sells within 30.
        ("Local-SEO and agency tools", "GBP, reviews, citations, rank reports, client reporting: asks from r/localseo, r/SEO, Local Search Forum"),
        ("WordPress plugin gaps", "searches on wordpress.org where the top plugins are stale or badly rated; a paid plugin or add-on"),
        ("Browser tools", "Chrome extensions and bookmarklets people ask for: checkers, extractors, one-click reports"),
        ("Data and reports", "paid lists, one-off PDF reports, white-label reports, spreadsheet templates (sell what you have)"),
        ("Freelancer and agency scripts", "repetitive work agencies and freelancers pay to skip: r/freelance, r/agency, Upwork-style asks"),
        ("Proof of small earners", "Show HN, Indie Hackers and Reddit posts showing a tiny tool earning; copy the shape, not the product"),
        ("Wildcard", "any ask with a price attached not picked in the last 30 days"),
    ],
}

TYPES = {"growth-scout": ("feature", "quick_win"), "opportunity-scout": ("saas", "cash_build")}


def block(agent: str) -> str:
    today = datetime.now()
    lane, scope = LANES[agent][today.weekday()]
    lines = [
        "",
        "===== NOVELTY RULES (from Hermes HQ) =====",
        f"TODAY'S LANE ({today:%A}): {lane} - {scope}",
        "Your primary recommendation today MUST come from this lane. Other urgent signals get one line under 'Also seen'.",
    ]
    recent = []
    if DB.exists():
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        since = (today - timedelta(days=30)).date().isoformat()
        q = f"SELECT date, title, status FROM ideas WHERE agent=? AND type IN ({','.join('?' * len(TYPES[agent]))}) AND date>=? ORDER BY date DESC"
        recent = con.execute(q, (agent, *TYPES[agent], since)).fetchall()
        con.close()
    lines.append(f"ALREADY RECOMMENDED in the last 30 days ({len(recent)}):")
    lines += [f"- {d} | {s} | {t}" for d, t, s in recent] or ["- none recorded yet"]
    lines += [
        "RULES:",
        "1. Do not make any of the above, or a close variant of it (same customer problem under a new name), your primary pick again.",
        "2. Exception: new, dated evidence from the last 7 days that changes the decision. Then add ONE line 'Update on <title>: <what changed>' and still pick something new as primary.",
        "3. If nothing new in today's lane clears your bar, say so plainly in one line and give the best lane candidate as 'Watch', not a repeat.",
        "4. Items the founder marked 'rejected' or 'parked' in HQ are closed unless the founder reopens them.",
    ]
    text = "\n".join(lines)
    try:  # what the fleet teacher learned for this agent
        import learnings
        text += "\n" + learnings.block(agent)
    except Exception:
        pass
    return text


if __name__ == "__main__":
    import sys
    print(block(sys.argv[1] if len(sys.argv) > 1 else "growth-scout"))
