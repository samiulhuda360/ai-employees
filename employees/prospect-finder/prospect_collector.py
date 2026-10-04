"""Prospect Finder collector: local businesses with weak Google Business Profiles.

For a few niche + city pairs not searched recently, it pulls the Google Maps
results from DataForSEO (the same provider Fernway's rank tracker uses),
benchmarks every business against the local top 3, and keeps the ones with
clear, fixable gaps: unclaimed profile, far fewer reviews than the leaders, low
rating, few photos, or outside the map pack. For the best candidates it looks
for a public contact email on their website.

Every number in the output comes from DataForSEO or the business's own site, so
the agent can quote it. Prints JSON. Needs DATAFORSEO_LOGIN and
DATAFORSEO_PASSWORD in the environment or in prospect-finder/.env (chmod 600).

    python prospect_collector.py            normal run (spends DataForSEO credit)
    python prospect_collector.py --dry-run  show which searches are due, no API calls
"""

from __future__ import annotations

import base64
import csv
import json
import os
import re
import statistics
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

HOME = Path.home()
BASE = HOME / "agents" / "prospect-finder"
if not BASE.exists():  # running from the PC project folder
    BASE = Path(__file__).resolve().parent
CONFIG = BASE / "prospect-config.yaml"
STATE = BASE / "search-state.json"
LEDGER = BASE / "prospects.csv"
ENV_FILE = BASE / ".env"
DFS_URL = "https://api.dataforseo.com/v3/serp/google/maps/live/advanced"
DEPTH = 20
UA = "Mozilla/5.0 (compatible; FernwayProspectCheck/1.0)"
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
BAD_EMAIL = re.compile(r"(example|sentry|wixpress|domain\.com|email\.com|\.png|\.jpg|\.webp|\.gif|@2x)", re.I)
LEDGER_FIELDS = ["date", "business", "niche", "city", "rank", "rating", "reviews", "claimed",
                 "photos", "website", "phone", "email", "signals", "cid", "status"]


def load_yaml(text: str):
    try:
        import yaml  # type: ignore
        return yaml.safe_load(text)
    except ImportError:
        from ruamel.yaml import YAML  # type: ignore
        return YAML(typ="safe").load(text)


def dfs_creds() -> tuple[str, str] | None:
    login, password = os.environ.get("DATAFORSEO_LOGIN"), os.environ.get("DATAFORSEO_PASSWORD")
    if (not login or not password) and ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            key, _, val = line.partition("=")
            if key.strip() == "DATAFORSEO_LOGIN":
                login = val.strip().strip("'\"")
            elif key.strip() == "DATAFORSEO_PASSWORD":
                password = val.strip().strip("'\"")
    return (login, password) if login and password else None


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"searched": {}, "spend": []}


def known_prospects() -> set[str]:
    if not LEDGER.exists():
        return set()
    with LEDGER.open(encoding="utf-8", newline="") as handle:
        return {row["cid"] for row in csv.DictReader(handle) if row.get("cid")}


def due_searches(cfg: dict, state: dict) -> list[tuple[str, str]]:
    """Niche + city pairs not searched within recheck_days, spread across niches and cities."""
    cutoff = (datetime.now() - timedelta(days=cfg.get("recheck_days", 45))).strftime("%Y-%m-%d")
    niches, cities = list(cfg["niches"]), list(cfg["cities"])
    pairs = [(n, c) for c in cities for n in niches
             if state["searched"].get(f"{n}|{c}", "") < cutoff]
    # Round-robin so one run covers different niches and cities.
    offset = len(state["searched"])
    pairs = pairs[offset % max(len(pairs), 1):] + pairs[: offset % max(len(pairs), 1)]
    picked, used_n, used_c = [], set(), set()
    for n, c in pairs:
        if n in used_n or c in used_c:
            continue
        picked.append((n, c))
        used_n.add(n)
        used_c.add(c)
        if len(picked) >= cfg.get("searches_per_run", 4):
            break
    return picked


def dfs_maps(creds: tuple[str, str], keyword: str, location: str) -> tuple[list[dict], float]:
    body = json.dumps([{"keyword": keyword, "location_name": location,
                        "language_code": "en", "depth": DEPTH}]).encode()
    auth = "Basic " + base64.b64encode(f"{creds[0]}:{creds[1]}".encode()).decode()
    req = urllib.request.Request(DFS_URL, data=body, headers={
        "Authorization": auth, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as resp:
        data = json.loads(resp.read())
    cost = float(data.get("cost") or 0)
    task = (data.get("tasks") or [{}])[0]
    if task.get("status_code") != 20000:
        raise RuntimeError(f"DataForSEO task {task.get('status_code')}: {task.get('status_message')}")
    items = ((task.get("result") or [{}])[0] or {}).get("items") or []
    return [i for i in items if i.get("type") in ("maps_search", None) and i.get("title")], cost


def row_of(item: dict) -> dict:
    rating = item.get("rating") or {}
    return {
        "rank": item.get("rank_group"),
        "name": item.get("title", "").strip(),
        "rating": rating.get("value"),
        "reviews": rating.get("votes_count") or 0,
        "claimed": item.get("is_claimed"),
        "photos": item.get("total_photos"),
        "category": item.get("category"),
        "website": (item.get("url") or (f"https://{item['domain']}" if item.get("domain") else "")).split("?")[0] or None,
        "phone": item.get("phone"),
        "address": item.get("address"),
        "cid": str(item.get("cid") or item.get("place_id") or ""),
        "place_id": item.get("place_id"),
    }


TOLL_FREE = re.compile(r"^\+?1?[\s-]*\(?8(00|33|44|55|66|77|88)\)?")
FREE_HOSTS = ("blogspot.", "wixsite.", "business.site", "weebly.", "wordpress.com", "godaddysites.",
              "facebook.com", "yelp.com", "linktr.ee", "sites.google.", "ueniweb.", "square.site",
              "business.page", "my.canva.site", "jimdosite.", "webnode.")
THROWAWAY_TLDS = {"site", "online", "xyz", "top", "click", "website", "space", "store", "services", "cc", "info", "biz"}
GENERIC_WORDS = {"electrician", "electrical", "plumber", "plumbing", "roofing", "roofer", "hvac", "heating",
                 "cooling", "pest", "control", "garage", "door", "restoration", "landscaping", "tree",
                 "service", "services", "contractor", "contractors", "repair", "company", "pros", "best",
                 "reliable", "affordable", "local", "near", "me", "24/7", "emergency", "window", "treatments"}


def real_business(biz: dict, city: str) -> bool:
    """Skip lead-gen and spam listings: they are not businesses that buy SEO.

    Keeps businesses with their own website (not a free builder page), a local
    (non toll-free) number, at least a few reviews, and a name that is more than
    "<service> <city>" keyword stuffing.
    """
    site = (biz["website"] or "").lower()
    if not site or any(h in site for h in FREE_HOSTS):
        return False
    host = re.sub(r"^https?://(www\.)?", "", site).split("/")[0]
    if host.rsplit(".", 1)[-1] in THROWAWAY_TLDS:
        return False
    if biz["phone"] and TOLL_FREE.match(biz["phone"].replace(" ", "")):
        return False
    if (biz["reviews"] or 0) < 3:
        return False
    words = re.findall(r"[a-z0-9/]+", biz["name"].lower())
    city_words = set(re.findall(r"[a-z]+", city.split(",")[0].lower()))
    if words and all(w in GENERIC_WORDS or w in city_words or len(w) <= 2 or w in {"llc", "inc"}
                     for w in words):
        return False
    return True


def weak_signals(biz: dict, leaders: dict, cfg: dict) -> list[str]:
    w = cfg["weak_signals"]
    out = []
    if w.get("unclaimed") and biz["claimed"] is False:
        out.append("Profile is unclaimed on Google")
    med = leaders["median_reviews"]
    share = w.get("reviews_below_leader_share", 0.25)
    if med and biz["reviews"] < share * med:
        out.append(f"{biz['reviews']} reviews vs a median of {med} for the top 3")
    if biz["rating"] is not None and biz["rating"] < w.get("rating_below", 4.2):
        out.append(f"{biz['rating']} star rating (top 3 average {leaders['avg_rating']})")
    if biz["photos"] is not None and biz["photos"] < w.get("photos_below", 10):
        out.append(f"only {biz['photos']} photo{'s' if biz['photos'] != 1 else ''} on the profile")
    if w.get("outside_map_pack") and biz["rank"] and biz["rank"] > 3:
        out.append(f"ranks #{biz['rank']} on Google Maps, outside the top-3 map pack")
    return out


def find_email(url: str | None) -> str | None:
    """A public contact email from the homepage or a contact page, if one is listed."""
    if not url:
        return None
    base = url if url.startswith("http") else f"https://{url}"
    base = base.split("?")[0].rstrip("/")
    root = re.match(r"https?://[^/]+", base)
    pages = [base] + ([root.group(0) + p for p in ("/contact", "/contact-us")] if root else [])
    for page in pages:
        try:
            req = urllib.request.Request(page, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=12) as resp:
                html = resp.read(400_000).decode("utf-8", "ignore")
        except (urllib.error.URLError, OSError, ValueError):
            continue
        for mail in re.findall(r"mailto:([^\"'?>\s]+)", html) + EMAIL_RE.findall(html):
            if not BAD_EMAIL.search(mail):
                return mail.lower()
    return None


def opener(c: dict) -> str:
    """One factual, friendly first line built only from the data. No claims about cause."""
    lead = c["leaders"]
    facts = []
    if c["claimed"] is False:
        facts.append("your Google Business Profile shows as unclaimed")
    if lead["median_reviews"] and c["reviews"] < 0.25 * lead["median_reviews"]:
        facts.append(f"it has {c['reviews']} reviews while the top 3 results nearby have around {lead['median_reviews']}")
    elif c["photos"] is not None and c["photos"] < 10:
        facts.append(f"it has {c['photos']} photo{'s' if c['photos'] != 1 else ''}")
    body = " and ".join(facts[:2]) or f"you are #{c['rank']} in the results"
    name = c["name"].rstrip(" .,")
    return (f"Hi {name}, I was looking at the Google Maps results for \"{c['keyword']}\" in {c['city']} "
            f"and noticed {body}. I can send you a free audit that shows what to fix first, if that is useful.")


def render(out: dict) -> str:
    if out.get("error"):
        return f"PROSPECTS - setup needed: {out['error']}"
    if not out["candidates"]:
        return (f"PROSPECTS - {datetime.now():%Y-%m-%d}: no business passed the filters in today's "
                f"{len(out['searches'])} searches. Nothing to do.")
    lines = [f"PROSPECTS - {datetime.now():%Y-%m-%d}   ({len(out['candidates'])} businesses, "
             f"{len(out['searches'])} searches, DataForSEO cost ${out['spend_usd']})", ""]
    for i, c in enumerate(out["candidates"], 1):
        lead = c["leaders"]
        lines += [
            f"{i}. {c['name']} - {c['niche'].replace('_', ' ')}, {c['city']}   Maps #{c['rank']} for \"{c['keyword']}\"",
            f"   Gaps: {'; '.join(c['signals'])}",
            f"   Top 3 nearby: {', '.join(lead['names'])} (median {lead['median_reviews']} reviews)",
            f"   Contact: {c['website']} | {c['phone'] or 'no phone'} | {c.get('email') or 'no public email'}",
            f"   Opener: \"{opener(c)}\"",
            "",
        ]
    lines.append("Next: run the Fernway profile check for the ones you like and send the report link.")
    if any(c.get("email") for c in out["candidates"]):
        lines.append("Email rules: US B2B email is fine with your real name and an opt-out line (CAN-SPAM).")
    lines.append("Mark results in prospects.csv (status column) so the list learns what works.")
    return "\n".join(lines)


def record(candidates: list[dict], today: str) -> None:
    """Append the shortlist to prospects.csv so no business is suggested twice.

    Recorded here, not by the agent, so the ledger never depends on a model
    remembering to write it. You update the status column as you work the list.
    """
    new_file = not LEDGER.exists()
    with LEDGER.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=LEDGER_FIELDS)
        if new_file:
            writer.writeheader()
        for c in candidates:
            writer.writerow({
                "date": today, "business": c["name"], "niche": c["niche"], "city": c["city"],
                "rank": c["rank"], "rating": c["rating"], "reviews": c["reviews"],
                "claimed": c["claimed"], "photos": c["photos"], "website": c["website"] or "",
                "phone": c["phone"] or "", "email": c.get("email") or "",
                "signals": " | ".join(c["signals"]), "cid": c["cid"], "status": "suggested"})


def main() -> None:
    cfg = load_yaml(CONFIG.read_text(encoding="utf-8"))
    state = load_state()
    dry = "--dry-run" in sys.argv
    today = datetime.now().strftime("%Y-%m-%d")
    searches = due_searches(cfg, state)
    out = {"generated_at": datetime.now().isoformat(timespec="minutes"), "market": cfg.get("market"),
           "ledger": str(LEDGER), "searches": [], "candidates": [], "spend_usd": 0.0}
    if dry:
        out["due_searches"] = searches
        print(json.dumps(out, indent=1))
        return
    creds = dfs_creds()
    if not creds:
        out["error"] = ("NO_CREDENTIALS: DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD are not set. "
                        f"Put DATAFORSEO_LOGIN and DATAFORSEO_PASSWORD in {ENV_FILE}.")
        emit(out)
        return

    seen = known_prospects()
    excludes = [e.lower() for e in cfg.get("exclude_name_contains", [])]
    spent = 0.0
    for keyword, city in searches:
        if spent >= cfg.get("max_spend_usd_per_run", 0.10):
            out["searches"].append({"keyword": keyword, "city": city, "skipped": "spend cap reached"})
            continue
        try:
            items, cost = dfs_maps(creds, keyword, city)
        except Exception as exc:  # one failed search must not sink the run
            out["searches"].append({"keyword": keyword, "city": city, "error": str(exc)[:200]})
            continue
        spent += cost
        state["searched"][f"{keyword}|{city}"] = today
        rows = [row_of(i) for i in items]
        top3 = [r for r in rows if r["rank"] and r["rank"] <= 3]
        leaders = {
            "median_reviews": int(statistics.median([r["reviews"] for r in top3])) if top3 else 0,
            "avg_rating": round(statistics.mean([r["rating"] for r in top3 if r["rating"]]), 1)
            if any(r["rating"] for r in top3) else None,
            "names": [r["name"] for r in top3],
        }
        out["searches"].append({"keyword": keyword, "niche": cfg["niches"][keyword], "city": city,
                                "results": len(rows), "leaders": leaders, "cost_usd": round(cost, 4)})
        per_search = 0
        for r in sorted(rows, key=lambda x: (x["claimed"] is not False, x["rank"] or 99)):
            if r["cid"] in seen or any(e in r["name"].lower() for e in excludes):
                continue
            if not real_business(r, city):
                continue
            if r["rank"] and r["rank"] <= 3:  # already in the map pack: not the easiest sale
                continue
            signals = weak_signals(r, leaders, cfg)
            if len(signals) >= cfg.get("min_signals", 2) and per_search < cfg.get("max_per_search", 3):
                per_search += 1
                out["candidates"].append({**r, "keyword": keyword, "niche": cfg["niches"][keyword],
                                          "city": city.split(",")[0], "signals": signals,
                                          "leaders": leaders})

    # Strongest first: unclaimed, then most signals, then those closest to the pack.
    out["candidates"].sort(key=lambda c: (c["claimed"] is not False, -len(c["signals"]), c["rank"] or 99))
    shortlist = out["candidates"][: cfg.get("shortlist_size", 8) + 4]
    for c in shortlist:
        c["email"] = find_email(c["website"])
    out["candidates"] = shortlist[: cfg.get("shortlist_size", 8)]
    record(out["candidates"], today)
    out["spend_usd"] = round(spent, 4)
    state["spend"] = (state.get("spend", []) + [[today, round(spent, 4)]])[-90:]
    STATE.write_text(json.dumps(state, indent=1), encoding="utf-8")
    emit(out)


def emit(out: dict) -> None:
    if "--report" in sys.argv:
        print(render(out))
    else:
        print(json.dumps(out, indent=1, default=str))


if __name__ == "__main__":
    main()
