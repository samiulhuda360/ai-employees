"""Build a demo home for Hermes HQ: the files a real fleet writes, filled with made-up data.

Everything here is fictional (Fernway is not a real company; businesses, people and links
are invented). The layout is the real one, and the real ingest (hq_ingest.py) turns it
into hq.db, so the demo exercises the same parsers, scoring and API as production.

    python hq/demo/seed.py            # writes hq/demo/home, then runs the ingest
    python hq/demo/seed.py --out DIR  # somewhere else

Dates are relative to today, so the dashboard always looks current.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
import shutil
import sqlite3
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
SERVER = REPO / "hq" / "server"
NOW = datetime.now().astimezone().replace(second=0, microsecond=0)
TODAY = NOW.date()
AGENT_ID = {"claudia": "chief-assistant"}
rng = random.Random(7)


def day(n: int) -> date:
    return TODAY - timedelta(days=n)


def job_id(agent: str, name: str) -> str:
    return hashlib.sha1(f"{agent}|{name}".encode()).hexdigest()[:12]


# ------------------------------------------------------------------ cron arithmetic (enough for these schedules)

def _field(spec: str, lo: int, hi: int) -> set[int]:
    out: set[int] = set()
    for part in spec.split(","):
        if part == "*":
            out |= set(range(lo, hi + 1))
        elif "-" in part:
            a, b = part.split("-")
            out |= set(range(int(a), int(b) + 1))
        else:
            out.add(int(part))
    return out


def cron_times(expr: str, start: datetime, forward: bool, limit: int = 1) -> list[datetime]:
    """The next (or previous) `limit` times an expression fires, from `start`."""
    minute, hour, dom, month, dow = expr.split()
    mins, hours = sorted(_field(minute, 0, 59)), sorted(_field(hour, 0, 23))
    doms, months, dows = _field(dom, 1, 31), _field(month, 1, 12), _field(dow, 0, 6)
    found: list[datetime] = []
    for offset in range(0, 70):
        d = start.date() + timedelta(days=offset if forward else -offset)
        if d.day not in doms or d.month not in months or (d.weekday() + 1) % 7 not in dows:
            continue
        times = [start.replace(year=d.year, month=d.month, day=d.day, hour=h, minute=m) for h in hours for m in mins]
        for t in (times if forward else reversed(times)):
            if (t > start) if forward else (t <= start):
                found.append(t)
                if len(found) == limit:
                    return found
    return found


# ------------------------------------------------------------------ report text

def report(name: str, jid: str, expr: str, when: datetime, body: str, failed: bool = False, script: str = "") -> str:
    out = [f"# Cron Job: {name}{' (FAILED)' if failed else ''}", "", f"**Job ID:** {jid}",
           f"**Run Time:** {when:%Y-%m-%d %H:%M:%S}", f"**Schedule:** {expr}", "", "## Prompt", "",
           "(the job's prompt, as in fleet/jobs.yaml)", ""]
    if script:
        out += ["## Script Output", "", "```", f"(output of {script})", "```", ""]
    return "\n".join(out + ["## Response", "", body.strip(), ""])


CASH = [
    ("Review reply template pack", 2, "USD 19 one-off", "Gumroad", "owners ask for wording that does not sound canned"),
    ("Google Business Profile post scheduler (Chrome extension)", 5, "USD 6/mo", "Chrome Web Store", "native posting has no scheduling"),
    ("Review QR code card generator", 1, "USD 9 one-off", "Gumroad", "printable cards are the top request in owner forums"),
    ("Local citation checklist for trades", 2, "USD 15 one-off", "Lemon Squeezy", "agencies resell checklists to clients"),
    ("Holiday hours bulk updater", 4, "USD 29 lifetime", "Gumroad", "multi-location owners update hours one by one"),
    ("Review response tone checker", 3, "USD 5/mo", "Chrome Web Store", "replies that read as defensive cost new customers"),
    ("Service-area page generator for WordPress", 5, "USD 39 one-off", "WordPress.org plugin directory", "thin city pages are a common agency task"),
    ("Monthly review report PDF for agencies", 4, "USD 12/mo", "Fernway customers", "Pro customers export by hand every month"),
]

GROWTH = [
    ("EXPERIMENT", "Competitor review tracker",
     "Track the review count and rating of three nearby competitors and show the gap on the dashboard. Customers already ask for it, and the data comes from the same place as their own reviews."),
    ("BUILD", "Review widget for websites",
     "A small script tag that shows a business's latest five-star reviews on its own site, styled to match. It turns reviews the customer already collected into sales on their site."),
    ("CONTENT", "How long Google takes to show a new review",
     "Owners keep asking why a review has not appeared. A short guide with Fernway's own timing data answers it and ranks for a question with little competition."),
    ("WATCH", "Profile verification by video",
     "More owners report being asked for a video during verification. Too early to build for; worth a help article if the reports keep coming."),
    ("EXPERIMENT", "Agency multi-location roll-up",
     "One view of every client location's rating, reviews and rank for agencies on Pro. Five agencies asked this quarter; a read-only version reuses existing data."),
]

QUICK_WINS = [
    "Add a 'copy review link' button next to every location",
    "Show the date of the last review on the dashboard card",
    "Send the review request reminder at 10:00 local time instead of 08:00",
    "Link the profile check results straight to the matching help article",
]

BLOG_A = [
    ("How to ask customers for Google reviews without breaking the rules", "how to ask for google reviews"),
    ("Why your Google review disappeared (and how to get it back)", "google review disappeared"),
    ("How many Google reviews does a local business need?", "how many google reviews do i need"),
    ("What to reply to a one-star review: 7 examples for trades", "reply to one star review examples"),
    ("Google Business Profile categories: how to choose the primary one", "google business profile primary category"),
    ("How to add service areas to Google Business Profile", "add service area google business profile"),
    ("Should you reply to every Google review?", "should you reply to every google review"),
    ("Review request text message templates that get replies", "review request text message template"),
]

BLOG_B = [
    ("Google Business Profile photos: what to upload first", "google business profile photos tips"),
    ("How long does Google take to publish a review?", "how long for google review to show"),
    ("Local SEO for plumbers: the first five fixes", "local seo for plumbers"),
    ("QR codes for reviews: do they work?", "qr code for google reviews"),
]

SOCIAL = {
    "FACEBOOK PAGE": [
        ("Three review replies we would never send", "Show bad replies and the fixed versions; owners share what they recognise"),
        ("Your profile has a photo problem if...", "Checklist post with one before and after"),
    ],
    "LINKEDIN": [
        ("Agencies: the monthly report your clients actually read", "One-page report example; ask agencies what they send"),
        ("What 1,000 review requests taught us about timing", "Timing data from the review request feature, framed as a lesson"),
    ],
    "X (TWITTER)": [
        ("Local SEO is mostly boring consistency", "Short thread: five dull habits that move rankings"),
        ("A one-star review is a sales page", "Reply example and why prospects read the owner's answer"),
    ],
}

PROSPECTS = [
    ("Harbourline Dental", "dentist", "Auckland", 5, 4.1, 23, "yes", 4, "Few photos; 6 unanswered reviews; no booking link"),
    ("Kowhai Plumbing & Gas", "plumber", "Hamilton", 11, 3.8, 9, "yes", 2, "Only 9 reviews; hours missing on Sundays"),
    ("Ridgeview Physio", "physiotherapist", "Tauranga", 17, 4.6, 31, "no", 0, "Profile not claimed; no photos"),
    ("Southpoint Auto Care", "mechanic", "Christchurch", 6, 4.0, 18, "yes", 5, "Owner never replies to reviews"),
    ("Tidewater Cafe", "cafe", "Wellington", 21, 4.4, 64, "yes", 12, "Wrong primary category (Restaurant)"),
    ("Ferncliff Electrical", "electrician", "Auckland", 4, 4.7, 12, "yes", 1, "No services listed; one photo"),
    ("Bluegum Landscaping", "landscaper", "Nelson", 19, 4.2, 7, "no", 0, "Not claimed; only 7 reviews"),
    ("Summit Roofing Co", "roofer", "Dunedin", 12, 3.6, 15, "yes", 3, "Rating under 4.0; two recent one-star reviews unanswered"),
    ("Clearwater Pools", "pool service", "Auckland", 16, 4.3, 11, "yes", 2, "No website link; description empty"),
    ("Riverbend Vet Clinic", "veterinarian", "Palmerston North", 10, 4.5, 41, "yes", 6, "Holiday hours not set; Q&A unanswered"),
    ("Northshore Locksmiths", "locksmith", "Auckland", 18, 4.0, 8, "yes", 0, "No photos; only 8 reviews"),
    ("Greenway Cleaning", "cleaning service", "Hamilton", 15, 4.8, 19, "no", 1, "Not claimed; service area missing"),
]

CUSTOMERS = {
    "WINNING": ["Coastline Dental Group: rating up 4.3 to 4.6 after 28 new reviews in four weeks",
                "Brightside Electrical: moved from position 9 to 3 for 'electrician near me' in its area",
                "Totara Physio: profile check score up 61 to 84 after fixing categories and hours"],
    "AT RISK": ["Ironbark Builders: No login for 34 days (last seen in August); map rank slipped 2 positions since the previous scan",
                "Lakeside Florist: Campaign 'Spring reviews' is paused for lack of credits (40 sent); 0 credits"],
    "NOT YET PAYING": ["Westgate Auto: free account, 3 campaigns set up and 40 review requests sent in its first week",
                       "Pohutukawa Dental: 1 campaign set up; ran the profile check three times this week"],
}

CODE_FIX = [
    ("fernway-app: image library has a known heap overflow advisory - upgrade to the patched minor release",
     "OSV lists a high-severity advisory for the installed version. The fix is a minor upgrade with no API change."),
    ("fernway-site: two packages are a major version behind and unmaintained",
     "Replace the date picker and the markdown sanitiser; both have open advisories and no release in 18 months."),
]

LEARNINGS = [
    ("Ask for the review in the same visit, not by email a week later", "local_seo", "blog-planner, social-planner, growth-scout",
     "Owners who ask in person and hand over a link get several times more reviews than delayed emails; the video showed the owner's dashboard.", "DEMONSTRATED"),
    ("Agent loops need a stop rule, not just a goal", "agents", "chief-assistant",
     "Give every scheduled agent a condition for saying nothing new ([SILENT]); quiet days stop repeated advice.", "VERIFIED"),
    ("Price small tools by the job they replace", "saas", "opportunity-scout",
     "One-off tools priced against the hour of manual work they save sold better than monthly plans in the creator's tests.", "CLAIMED"),
    ("Pin dependency updates to a weekly window", "tooling", "code-health, chief-assistant",
     "Batching upgrades weekly with a lock-file diff made breakages traceable to one change.", "DEMONSTRATED"),
    ("Category beats keywords in the business name", "local_seo", "blog-planner, prospect-finder, growth-scout",
     "Fixing the primary category moved more profiles into the map pack than adding keywords to the name.", "DEMONSTRATED"),
]

TUNEUP = [
    ("blog-planner", "Skip 'best X in <city>' listicles; propose how-to guides instead.",
     "The founder rejected all four listicle ideas in the last 21 days with the note 'not our audience'."),
    ("opportunity-scout", "Only list Chrome extensions when the build is 3 days or less.",
     "Two approved builds were extensions; both longer ones were parked as too big for a week."),
    ("social-planner", "Lead LinkedIn ideas with Fernway's own numbers, not industry statistics.",
     "Approved LinkedIn ideas all used first-party data; rejected ones quoted third-party surveys."),
]


def cash_body(n: int) -> str:
    picks = rng.sample(CASH, 3)
    lines = ["CASH BUILDS", ""]
    for i, (name, days, price, via, why) in enumerate(picks, 1):
        lines += [f"{i}. {name} - build: {days} days - price: {price} - sell via: {via} - why: {why}",
                  f"   Evidence: owner forum thread, https://example.com/forum/{n}{i}",
                  "   Ask: would you pay for this today?", ""]
    lines.append("SELL WHAT YOU HAVE: Fernway profile check - sell a branded PDF version to agencies at USD 49 a month")
    return "\n".join(lines)


def growth_body(n: int) -> str:
    verdict, title, what = GROWTH[n % len(GROWTH)]
    return "\n".join([
        f"Decision today: {verdict} - {title}", "", "### What it is", what, "",
        "1. What changed in the market today",
        f"   - Owners discuss the topic in a forum thread: https://example.com/thread/{n}",
        "2. The recommendation: see the brief above.",
        "3. Runner-ups: none strong enough today.", "",
        "Quick win this week",
        f"- {QUICK_WINS[n % len(QUICK_WINS)]}",
    ])


def blog_body(n: int) -> str:
    a = rng.sample(BLOG_A, 3)
    b = rng.sample(BLOG_B, 2)
    lines = ["BLOG IDEAS", "", "TIER A (checked against the search results, winnable)"]
    for i, (title, q) in enumerate(a, 1):
        lines += [f"{i}. {title} - query: {q}", f"   Why winnable: the top results are forum threads and thin pages. https://example.com/serp/{n}{i}"]
    lines += ["", "TIER B (quick leads, not checked yet)"]
    for i, (title, q) in enumerate(b, 1):
        lines += [f"{i}. {title} - query: {q}"]
    lines += ["", "SKIPPED", "- Two ideas already covered on the blog."]
    return "\n".join(lines)


def social_body(n: int) -> str:
    lines = ["SOCIAL IDEAS", ""]
    for platform, ideas in SOCIAL.items():
        hook, angle = ideas[n % len(ideas)]
        lines += [platform, f'1. Hook: "{hook}"', f"   Angle: {angle}", f"   Source: https://example.com/post/{n}", ""]
    lines += ["BEST TIMES", "- Weekdays 07:30 and 12:15 local time."]
    return "\n".join(lines)


def prospect_body(batch: list[tuple]) -> str:
    lines = ["PROSPECTS - today's businesses with fixable gaps", ""]
    for i, p in enumerate(batch, 1):
        lines += [f"{i}. {p[0]} - {p[1]}, {p[2]}   Maps #{p[3]} for \"{p[1]} {p[2].lower()}\"",
                  f"   Gaps: {p[8]}", f"   Profile: https://example.com/maps/{p[0].lower().replace(' ', '-')}"]
    return "\n".join(lines)


def client_body() -> str:
    lines = ["CLIENT WINS - week ending " + f"{TODAY:%Y-%m-%d}", ""]
    for section, items in CUSTOMERS.items():
        lines += [section] + [f"- {i}" for i in items] + [""]
    lines += ["UPSELL", "- Coastline Dental Group runs 4 locations on Starter; Pro adds the roll-up report.", "",
              "TREND", "- Paying accounts up 2 this week."]
    return "\n".join(lines)


def code_body() -> str:
    lines = ["CODE HEALTH - weekly audit of 3 repositories", "", "FIX THIS WEEK"]
    for i, (title, why) in enumerate(CODE_FIX, 1):
        lines += [f"{i}. {title}", f"   {why}"]
    lines += ["", "LATER", "- fernway-api: Node 20 reaches end of life in April; plan the move to 22.", "",
              "CHANGED SINCE LAST WEEK", "- fernway-api: the token-logging finding is fixed."]
    return "\n".join(lines)


def tuneup_body() -> str:
    lines = ["TUNE-UP PROPOSALS", ""]
    for i, (agent, rule, why) in enumerate(TUNEUP, 1):
        lines += [f"{i}. [{agent}] RULE: {rule}", f"   Why: {why}", ""]
    return "\n".join(lines)


def fleet_body() -> str:
    return "\n".join([
        f"Fleet report, {TODAY:%A %d %B}", "",
        "All server agents ran on time. YouTube Watcher missed last night's run (the PC was off).",
        "Blog Planner used the backup model once this week; its fact check passed.", "",
        "Worth your attention:",
        "- Code Health: one high-severity dependency upgrade in fernway-app.",
        "- Client Wins: Lakeside Florist's payment failed twice.",
    ])


# ------------------------------------------------------------------ writers

def write_jobs(home: Path, jobs: list[dict]) -> dict[str, dict]:
    """jobs.json per server agent and _pc_jobs_status.json per PC agent, like Hermes writes them."""
    per_agent: dict[str, list[dict]] = {}
    by_name: dict[str, dict] = {}
    for j in jobs:
        agent = AGENT_ID.get(j["agent"], j["agent"])
        jid = job_id(agent, j["name"])
        expr = j["schedule"]
        last = cron_times(expr, NOW, forward=False)
        nxt = cron_times(expr, NOW, forward=True)
        entry = {
            "id": jid, "name": j["name"], "script": j.get("script"), "no_agent": j["model"].startswith("none"),
            "schedule": {"kind": "cron", "expr": expr, "display": expr}, "schedule_display": expr,
            "enabled": not j["paused"], "state": "paused" if j["paused"] else "scheduled",
            "last_run_at": None if j["paused"] or not last else last[0].isoformat(timespec="seconds"),
            "last_status": None if j["paused"] else "ok",
            "next_run_at": None if j["paused"] or not nxt else nxt[0].isoformat(timespec="seconds"),
            "deliver": j["deliver"], "model": None, "model_provider": None, "last_error": None,
        }
        if j["name"] == "Daily YouTube learning run":  # the PC was off last night: shows as late
            entry["next_run_at"] = (NOW - timedelta(hours=2)).isoformat(timespec="seconds")
            entry["last_run_at"] = (NOW - timedelta(days=1, hours=2)).isoformat(timespec="seconds")
        per_agent.setdefault(agent, []).append(entry)
        by_name[j["name"]] = {**entry, "agent": agent, "runs_on": j["runs_on"]}
    for agent, entries in per_agent.items():
        if by_name[entries[0]["name"]]["runs_on"] == "server":
            path = home / ".hermes" / "profiles" / agent / "cron" / "jobs.json"
        else:
            path = home / "agents" / agent / "pc-reports" / "_pc_jobs_status.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"jobs": entries}, indent=1), encoding="utf-8")
    return by_name


def write_run(home: Path, job: dict, when: datetime, body: str, failed: bool = False) -> None:
    text = report(job["name"], job["id"], job["schedule"]["expr"], when, body, failed, job.get("script") or "")
    if job["runs_on"] == "server":
        path = home / ".hermes" / "profiles" / job["agent"] / "cron" / "output" / job["id"] / f"{when:%Y-%m-%d_%H-%M-%S}.md"
    else:
        path = home / "agents" / job["agent"] / "pc-reports" / f"{job['id']}__{when:%Y-%m-%d_%H-%M-%S}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def runs_for(job: dict, days: int) -> list[datetime]:
    if job["state"] == "paused":
        return []
    times = cron_times(job["schedule"]["expr"], NOW, forward=False, limit=60)
    return [t for t in times if t.date() >= day(days)] or times[:1]  # weekly jobs: at least the last run


def write_reports(home: Path, jobs: dict[str, dict]) -> None:
    bodies = {
        "Daily cash builds": lambda n: cash_body(n),
        "Daily Fernway growth radar": lambda n: growth_body(n),
        "Daily blog ideas": lambda n: blog_body(n),
        "Daily social ideas": lambda n: social_body(n),
        "Weekly client wins": lambda n: client_body(),
        "Weekly code health": lambda n: code_body(),
        "Weekly agent tune-up": lambda n: tuneup_body(),
        "Daily fleet report": lambda n: fleet_body(),
        "Blog ideas fact check": lambda n: "Fact check: 3 of 3 Tier A ideas confirmed against today's results.",
        "Social ideas fact check": lambda n: "Fact check: sources open and dated; one hook reworded.",
        "Weekly Fernway product strategy": lambda n: "Weekly strategy: ship the review link button, test the competitor tracker with five customers.",
        "Weekly: build this one": lambda n: f"BUILD THIS ONE - {TODAY:%Y-%m-%d} - Review QR code card generator - 1 days - USD 9 one-off",
        "Morning check-in": lambda n: "Morning. Three things today: the image library upgrade, reply to Lakeside Florist, approve the blog batch.",
        "Evening check-in": lambda n: "How did today go? Reply with what got done and I will log it.",
        "Relay blog ideas": lambda n: "BLOG IDEAS sent: 3 Tier A, 2 Tier B.",
        "Relay social ideas": lambda n: "SOCIAL IDEAS sent: 3 platforms.",
        "Relay prospects": lambda n: "PROSPECTS sent: 4 businesses.",
        "Relay client wins": lambda n: "CLIENT WINS sent: 3 winning, 2 at risk.",
        "Fleet guardian": lambda n: "[SILENT]",
        "Daily YouTube learning run": lambda n: "Learned 1 new technique; added to the knowledge base.",
    }
    prospect_days = 0
    for name, job in jobs.items():
        make = bodies.get(name)
        if name == "Daily prospects":
            for t in runs_for(job, 6):
                batch = PROSPECTS[(prospect_days * 4) % len(PROSPECTS):][:4] or PROSPECTS[:4]
                prospect_days += 1
                write_run(home, job, t, prospect_body(batch))
            continue
        if not make:
            continue
        for n, t in enumerate(runs_for(job, 6)):
            body = make(n)
            if name == "Daily blog ideas" and t.date() == day(1):
                body = "Provider fallback: primary-model unavailable; using backup-model for this response.\n\n" + body
            write_run(home, job, t, body)
    # One failed run three days ago, so the history shows what a failure looks like.
    fc = jobs["Social ideas fact check"]
    write_run(home, fc, datetime.combine(day(3), datetime.min.time()).astimezone().replace(hour=11, minute=0, second=4),
              "The draft file was missing: the morning run had not finished.", failed=True)


def write_side_files(home: Path) -> None:
    agents = home / "agents"
    # Prospect Finder's ledger
    pf = agents / "prospect-finder"
    pf.mkdir(parents=True, exist_ok=True)
    with (pf / "prospects.csv").open("w", encoding="utf-8", newline="") as h:
        w = csv.writer(h)
        w.writerow(["cid", "date", "business", "niche", "city", "rank", "rating", "reviews", "claimed", "photos",
                    "website", "phone", "email", "signals", "status"])
        statuses = ["suggested"] * 8 + ["contacted", "contacted", "replied", "no fit"]
        for i, p in enumerate(PROSPECTS):
            slug = p[0].lower().replace(" & ", "-").replace(" ", "-")
            w.writerow([f"demo-{i:03d}", day(i // 4).isoformat(), p[0], p[1], p[2], p[3], p[4], p[5], p[6], p[7],
                        f"https://{slug}.example.com", f"+64 9 000 0{100 + i}", "", p[8], statuses[i]])
    # Client Wins: weekly customer pulse
    cw = agents / "client-wins"
    cw.mkdir(parents=True, exist_ok=True)
    weeks = []
    for k in range(10):
        d = TODAY - timedelta(days=7 * (9 - k))
        weeks.append({"date": d.isoformat(), "accounts": 38 + 3 * k, "paying": 8 + k // 2 + (1 if k > 6 else 0),
                      "revenue_usd": 1800 + 260 * k, "winning": 2 + k // 3, "at_risk": 1 + (k % 3 == 0)})
    (cw / "pulse-history.json").write_text(json.dumps(weeks, indent=1), encoding="utf-8")
    # Claudia: journal, inbox, the founder profile, records, backlog
    ca = agents / "chief-assistant"
    (ca / "records").mkdir(parents=True, exist_ok=True)
    journal = ["# Journal", ""]
    plans = ["Ship the review link button", "Upgrade the image library", "Write the reviews guide", "Call two at-risk customers"]
    for n in range(6, -1, -1):
        journal += [f"## {day(n).isoformat()}", f"Plan: {plans[n % len(plans)]}",
                    "Done: " + ("" if n == 0 else f"{plans[(n + 1) % len(plans)]}"), "Notes:", ""]
    (ca / "journal.md").write_text("\n".join(journal), encoding="utf-8")
    (ca / "task-inbox.md").write_text("# Task inbox\n\n- [ ] Renew the domain before the end of the month\n"
                                      "- [ ] Reply to the agency that asked about the roll-up report\n"
                                      "- [x] Move the backups to the new bucket\n", encoding="utf-8")
    (ca / "about-me.md").write_text("# About the founder (demo)\n\nA made-up founder of Fernway, a made-up company.\n\n"
                                    "- Builds the product alone; mornings are for code, afternoons for customers.\n"
                                    "- Prefers short messages: the answer first, then the reason.\n"
                                    "- Weekly review on Friday afternoon.\n", encoding="utf-8")
    (ca / "records" / f"week-{TODAY - timedelta(days=TODAY.weekday() + 1):%Y-%m-%d}.md").write_text(
        "# Weekly record\n\n- 5 of 7 days planned; 4 plans done.\n- 9 ideas approved, 6 rejected.\n"
        "- Shipped: review link button.\n", encoding="utf-8")
    (ca / "ideas-backlog.md").write_text(f"""# Ideas backlog

Last updated: {day(5).isoformat()}

## 3. New agents

### 3.1 Renewals and money watcher (`NEXT`)
- **What:** Watches domains, certificates and subscriptions and warns two weeks before anything renews or expires.
- **Sources:** two missed renewals last year

### 3.2 Support inbox triage (`PROPOSED`)
- **What:** Sorts support email into bug, billing and how-to, and drafts answers from the help centre for the founder to send.
- **Sources:** support volume doubled since launch

## 4. Upgrades

| Upgrade | Agent | Source | Status |
|---|---|---|---|
| Remember rejected ideas for 30 days | Growth Scout | weekly tune-up | BUILT |
| Check the search results before Tier A | Blog Planner | fact-check failures | BUILT |
| Ask the PC for a direct recheck | Code Health | HQ recheck button | NEXT |

## 5. Done
""", encoding="utf-8")
    # YouTube Watcher's knowledge base (mirrored from the PC in the real fleet)
    yw = agents / "youtube-watcher"
    yw.mkdir(parents=True, exist_ok=True)
    kb = ["# Knowledge base", "", "## Entries", ""]
    for i, (title, lane, for_, what, evidence) in enumerate(LEARNINGS):
        kb += [f"### {title}", f"- date: {day(i).isoformat()} lane: {lane}", f"- what: {what}", f"- for: {for_}",
               f"- evidence: {evidence}", f"- source: https://example.com/video/{i + 1}", ""]
    (yw / "knowledge-base.md").write_text("\n".join(kb), encoding="utf-8")


def write_souls(home: Path) -> None:
    for soul in (REPO / "employees").glob("*/SOUL.md"):
        agent = AGENT_ID.get(soul.parent.name, soul.parent.name)
        if agent in ("youtube-watcher", "code-health"):
            dest = home / "agents" / agent / "pc-profile" / "SOUL.md"
        else:
            dest = home / ".hermes" / "profiles" / agent / "SOUL.md"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(soul, dest)
    memory = {
        "chief-assistant": ["The founder wants the answer first, then the reason.", "Quiet hours are 23:00 to 07:00: nothing is sent then."],
        "blog-planner": ["The blog targets owners of local service businesses, not agencies.", "Listicles were rejected four times."],
        "growth-scout": ["Competitor review tracking is the most requested missing feature."],
    }
    for agent, entries in memory.items():
        mem = home / ".hermes" / "profiles" / agent / "memories" / "MEMORY.md"
        mem.parent.mkdir(parents=True, exist_ok=True)
        mem.write_text("\n§\n".join(entries) + "\n", encoding="utf-8")


def decide(home: Path) -> None:
    """Decisions the founder made in HQ, so boards, the tune-up and the audit log have history."""
    con = sqlite3.connect(home / "hq" / "hq.db")
    stamp = lambda n: (NOW - timedelta(days=n, hours=3)).isoformat(timespec="seconds")  # noqa: E731
    ids = lambda sql: [r[0] for r in con.execute(sql)]  # noqa: E731
    with con:
        for n, i in enumerate(ids("SELECT id FROM ideas WHERE type='cash_build' ORDER BY id LIMIT 4")):
            status, note = [("approved", "Small enough for a weekend"), ("rejected", "Needs an audience I do not have"),
                            ("parked", "Good, but after the widget"), ("done", "Shipped on Gumroad")][n]
            con.execute("UPDATE ideas SET status=?, note=?, decided_at=? WHERE id=?", (status, note, stamp(n + 1), i))
        for n, i in enumerate(ids("SELECT id FROM ideas WHERE type='blog' ORDER BY id LIMIT 3")):
            con.execute("UPDATE ideas SET status=?, decided_at=? WHERE id=?", (["approved", "rejected", "done"][n], stamp(n + 2), i))
        first = ids("SELECT id FROM ideas WHERE type='proposal' AND agent='blog-planner' LIMIT 1")
        for i in first:
            con.execute("UPDATE ideas SET status='approved', decided_at=? WHERE id=?", (stamp(1), i))
            soul = home / ".hermes" / "profiles" / "blog-planner" / "SOUL.md"
            rule = TUNEUP[0][1]
            soul.write_text(soul.read_text(encoding="utf-8").rstrip() + f"\n\n- {day(1).isoformat()} [hq-{i}] {rule}\n", encoding="utf-8")
        con.executemany("INSERT INTO audit(at,who,action,target,args,result) VALUES(?,?,?,?,?,?)", [
            (stamp(2), "owner", "login", "hq", "", "ok"),
            (stamp(2), "owner", "job.pause", "opportunity-scout/Fast-growth signal pulse", "", "paused"),
            (stamp(1), "owner", "idea", "blog", '{"status": "approved"}', "ok"),
            (stamp(1), "owner", "soul.rule", "blog-planner", "", "added"),
        ])
    con.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parent / "home")
    home = ap.parse_args().out.resolve()
    if home.exists():
        shutil.rmtree(home)
    jobs = yaml.safe_load((REPO / "fleet" / "jobs.yaml").read_text(encoding="utf-8"))["jobs"]
    by_name = write_jobs(home, jobs)
    write_reports(home, by_name)
    write_side_files(home)
    write_souls(home)
    env = {**os.environ, "HQ_HOME": str(home)}
    subprocess.run([sys.executable, str(SERVER / "hq_ingest.py")], check=True, env=env)
    decide(home)
    print(f"demo home ready: {home}")


if __name__ == "__main__":
    main()
