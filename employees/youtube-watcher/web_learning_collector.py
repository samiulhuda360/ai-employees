"""Web leads for the fleet teacher: forum threads, blog posts and news for each agent.

Reads study-lists.yaml, pulls each agent's feeds, subreddits and Hacker News searches,
ranks the items by that agent's keywords, and returns a few unseen leads per agent.
No model, no login, public pages only. YouTube Watcher's collector calls leads() and
adds the result to its output; the agent then reads the pages it chooses before
recording anything.

Run directly to see what it finds:  python web_learning_collector.py
"""

from __future__ import annotations

import html
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

BASE = Path(__file__).resolve().parent
CANONICAL = Path(__file__).resolve().parent
if (CANONICAL / "study-lists.yaml").exists():
    BASE = CANONICAL
STUDY = BASE / "study-lists.yaml"
SEEN = BASE / "knowledge" / "web_seen.json"
UA = "Hermes-Fleet-Teacher/1.0"
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128 Safari/537.36"
REDDIT_GAP_S = 20


def fetch(url: str, timeout: int = 20) -> bytes:
    """Our own user-agent first (Reddit wants one); some blogs only answer a browser."""
    last: Exception | None = None
    for ua in (UA, BROWSER_UA):
        req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept": "application/rss+xml, application/atom+xml, application/xml, application/json, */*"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code == 429 and "reddit.com" in url:
                time.sleep(45)
                continue
            if exc.code not in (403, 406):
                raise
    raise last  # type: ignore[misc]


def clean(s: str, limit: int = 260) -> str:
    s = html.unescape(re.sub(r"<[^>]+>", " ", s or ""))
    return re.sub(r"\s+", " ", s).strip()[:limit]


def parse_date(value: str) -> datetime | None:
    value = (value or "").strip()
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%f%z"):
        try:
            d = datetime.strptime(value, fmt)
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def feed_items(url: str) -> list[dict]:
    root = ET.fromstring(fetch(url))
    out = []
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1].lower() not in ("item", "entry"):
            continue
        get = lambda *names: next((c.text or "" for c in node if c.tag.rsplit("}", 1)[-1].lower() in names and c.text), "")  # noqa: E731
        link = get("link") or next((c.attrib.get("href", "") for c in node if c.tag.rsplit("}", 1)[-1].lower() == "link"), "")
        title = clean(get("title"), 200)
        if title and link and title != "Welcome to Reddit":
            out.append({"title": title, "url": link.strip(), "summary": clean(get("description", "summary", "content", "encoded")),
                        "published": parse_date(get("pubdate", "published", "updated"))})
    return out


def hn_items(query: str, days: int) -> list[dict]:
    since = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
    url = "https://hn.algolia.com/api/v1/search?" + urllib.parse.urlencode(
        {"query": query, "tags": "story", "hitsPerPage": 15, "numericFilters": f"created_at_i>{since}"})
    out = []
    for h in json.loads(fetch(url))["hits"]:
        if h.get("title"):
            out.append({"title": clean(h["title"], 200), "url": h.get("url") or f"https://news.ycombinator.com/item?id={h['objectID']}",
                        "summary": f"{h.get('points', 0)} points, {h.get('num_comments', 0)} comments on Hacker News",
                        "published": parse_date(h.get("created_at", "")), "points": h.get("points") or 0})
    return out


def leads() -> dict:
    cfg = yaml.safe_load(STUDY.read_text(encoding="utf-8")) or {}
    days = cfg.get("lookback_days", 7)
    per_agent = cfg.get("max_leads_per_agent", 4)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    try:
        seen = json.loads(SEEN.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        seen = {}
    today = datetime.now().strftime("%Y-%m-%d")
    seen = {u: d for u, d in seen.items() if d >= (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")}

    result, errors, last_reddit = {}, [], 0.0
    for agent, spec in (cfg.get("agents") or {}).items():
        items: list[dict] = []
        sources = [("feed", u) for u in spec.get("feeds") or []]
        subs = spec.get("subreddits") or []
        if subs:
            sources.append(("reddit", f"https://www.reddit.com/r/{'+'.join(subs)}/top/.rss?t=week&limit=40"))
        sources += [("hn", q) for q in spec.get("hn") or []]
        for kind, src in sources:
            try:
                if kind == "reddit":  # Reddit throttles by IP: space the requests
                    wait = REDDIT_GAP_S - (time.time() - last_reddit)
                    if wait > 0:
                        time.sleep(wait)
                    last_reddit = time.time()
                got = hn_items(src, days) if kind == "hn" else feed_items(src)
            except Exception as exc:  # noqa: BLE001  a dead feed must not stop the run
                errors.append({"agent": agent, "source": src[:80], "error": f"{type(exc).__name__}: {str(exc)[:60]}"})
                continue
            for it in got:
                it["via"] = "Hacker News" if kind == "hn" else urllib.parse.urlparse(it["url"]).netloc.replace("www.", "") if kind == "feed" else "Reddit"
            items += got
        kws = [str(k).lower() for k in spec.get("keywords") or []]
        skip = [str(k).lower() for k in spec.get("skip") or []]
        ranked = []
        for it in items:
            if it["url"] in seen or (it["published"] and it["published"] < cutoff):
                continue
            text = f"{it['title']} {it['summary']}".lower()
            hits = [k for k in kws if k in text]
            # Reddit and Hacker News are noisy: one loose word is not enough there.
            need = 2 if it["via"] in ("Reddit", "Hacker News") and not any(" " in h for h in hits) else 1
            if len(hits) < need or any(s in it["title"].lower() for s in skip):
                continue
            score = len(hits) * 2 + min(3, it.get("points", 0) / 50) + (1 if it["via"] not in ("Reddit", "Hacker News") else 0)
            ranked.append((score, it, hits))
        ranked.sort(key=lambda r: r[0], reverse=True)
        picked, titles = [], set()
        for score, it, hits in ranked:
            key = re.sub(r"\W+", " ", it["title"].lower())[:60]
            if key in titles:
                continue
            titles.add(key)
            picked.append({"title": it["title"], "url": it["url"], "via": it["via"], "summary": it["summary"],
                           "published": it["published"].strftime("%Y-%m-%d") if it["published"] else "", "matched": hits[:5]})
            seen[it["url"]] = today
            if len(picked) >= per_agent:
                break
        result[agent] = {"need": " ".join(str(spec.get("need", "")).split()), "leads": picked}
    try:
        SEEN.parent.mkdir(parents=True, exist_ok=True)
        SEEN.write_text(json.dumps(seen, indent=0), encoding="utf-8")
    except OSError:
        pass
    return {"study_lists": result, "web_source_errors": errors}


if __name__ == "__main__":
    out = leads()
    for agent, d in out["study_lists"].items():
        print(f"== {agent}: {len(d['leads'])} leads")
        for lead in d["leads"]:
            print(f"   [{lead['via']}] {lead['title'][:90]}  ({', '.join(lead['matched'][:3])})")
    print("errors:", out["web_source_errors"])
