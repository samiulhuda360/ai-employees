"""Cash Builds crawler: where people are asking for a tool, saying they would pay, or
proving a small tool earns. Runs before each Cash Builds job and prints one JSON
document the agent judges from. No model, no login, public pages only.

Sources (all verified reachable from the server on 2026-09-28):
  Reddit          subreddit "new" feeds and search feeds, RSS with our own user-agent,
                  one request every 8 s (Reddit throttles by IP; bursts get 429)
  Hacker News     Algolia full-text search, last 30 days
  Local Search Forum, Product Hunt, Google News   RSS
  Stack Exchange  public API (webmasters, wordpress)
  WordPress.org   plugin search API: a search whose best plugins are old or badly
                  rated is a gap someone will pay to fill
  Quora           no feed and a bot wall. We ask a web search engine for question
                  titles (the title is the ask) and never open Quora itself. If no
                  engine answers from this IP, Quora is reported as unavailable, or
                  fetched through Apify when APIFY_TOKEN and apify.quora_actor are set.
  Promo inbox     the founder's Gmail Promotions (last 48 h), read on the PC through Composio
                  by promo_inbox_collector.py and uploaded to inbox/promo_inbox.json nightly

Usage: python3 cash_signal_collector.py            (reads cash-config.yaml next to it)
"""

from __future__ import annotations

import concurrent.futures
import html
import json
import os
import re
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import yaml
except ImportError:  # Hermes' bundled Python has ruamel
    from ruamel.yaml import YAML
    yaml = None

# Hermes runs jobs from ~/agents with a copy of this script in the profile's scripts
# folder; the config and .env stay in ~/agents/opportunity-scout.
_CANDIDATES = [Path.cwd() / "opportunity-scout", Path.home() / "agents" / "opportunity-scout", Path(__file__).resolve().parent]
BASE = next((c for c in _CANDIDATES if (c / "cash-config.yaml").exists()), _CANDIDATES[-1])
CONFIG_PATH = BASE / "cash-config.yaml"
ENV_PATH = BASE / ".env"
UA = "Hermes-Opportunity-Scout/2.0"
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128 Safari/537.36"
HN_SEARCH = "https://hn.algolia.com/api/v1/search"
REDDIT_GAP_S = 15
QUORA_ENGINES = [
    ("startpage", "https://www.startpage.com/do/search?q={q}"),
    ("marginalia", "https://search.marginalia.nu/search?query={q}"),
    ("ddg-lite", "https://lite.duckduckgo.com/lite/?q={q}"),
    ("bing", "https://www.bing.com/search?q={q}"),
]


def load_config() -> dict:
    text = CONFIG_PATH.read_text(encoding="utf-8")
    if yaml:
        return yaml.safe_load(text) or {}
    return YAML(typ="safe").load(text) or {}


def load_env() -> dict:
    out = dict(os.environ)
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                out.setdefault(k.strip(), v.strip().strip('"'))
    return out


def fetch(url: str, ua: str = UA, timeout: int = 25, accept: str = "*/*") -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept": accept, "Accept-Language": "en"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def clean(s: str, limit: int = 300) -> str:
    s = html.unescape(re.sub(r"<[^>]+>", " ", s or ""))
    return re.sub(r"\s+", " ", s).strip()[:limit]


def score(text: str, cfg: dict) -> dict:
    low = text.lower()
    total, hits = 0.0, {}
    for name, block in (cfg.get("signals") or {}).items():
        n = sum(1 for t in block.get("terms", []) if str(t).lower().strip('"') in low)
        if n:
            total += block.get("weight", 1) * min(n, 3)
            hits[name] = n
    demote = cfg.get("demote") or {}
    if any(str(t).lower().strip('"') in low for t in demote.get("terms", [])):
        total += demote.get("weight", -5)
        hits["demote"] = 1
    kind = "paying" if "paying" in hits else "proof" if "proof" in hits else "asking" if "asking" in hits or "pain" in hits else "chatter"
    return {"signal_score": round(total, 1), "hits": hits, "ask_type": kind}


# ------------------------------------------------------------------ RSS / Atom

def parse_feed(name: str, url: str, cfg: dict, ua: str = UA) -> dict:
    try:
        root = ET.fromstring(fetch(url, ua=ua, accept="application/rss+xml, application/atom+xml, application/xml, text/xml"))
    except urllib.error.HTTPError as e:
        return {"source": name, "url": url, "error": f"HTTP {e.code}", "items": []}
    except Exception as e:  # noqa: BLE001
        return {"source": name, "url": url, "error": type(e).__name__, "items": []}
    items = []
    for node in root.iter():
        tag = node.tag.rsplit("}", 1)[-1].lower()
        if tag not in ("item", "entry"):
            continue
        get = lambda *names: next((c.text or "" for c in node if c.tag.rsplit("}", 1)[-1].lower() in names and c.text), "")  # noqa: E731
        title = clean(get("title"), 200)
        if not title or title == "Welcome to Reddit":
            continue
        link = get("link") or next((c.attrib.get("href", "") for c in node if c.tag.rsplit("}", 1)[-1].lower() == "link"), "")
        summary = clean(get("description", "summary", "content", "encoded"), 320)
        item = {"title": title, "summary": summary, "url": link.strip(), "published": clean(get("pubdate", "published", "updated"), 40)}
        item.update(score(f"{title} {summary}", cfg))
        items.append(item)
        if len(items) >= cfg.get("max_per_source", 25):
            break
    return {"source": name, "url": url, "error": None, "items": items}


def reddit_jobs(cfg: dict) -> list[tuple[str, str]]:
    """Reddit throttles by IP, so subreddits are combined into multireddit feeds:
    six requests instead of thirty, each spaced REDDIT_GAP_S apart."""
    jobs = []
    subs = cfg.get("subreddits") or {}
    for group, names in subs.items():
        names = list(names or [])
        for i in range(0, len(names), 6):
            chunk = names[i:i + 6]
            jobs.append((f"reddit.{group}.{'+'.join(chunk)}", f"https://www.reddit.com/r/{'+'.join(chunk)}/new/.rss?limit=50"))
    asks = " OR ".join(cfg.get("ask_phrases") or [])
    everything = "+".join((subs.get("asks") or []) + (subs.get("builders") or []))
    if asks and everything:
        q = urllib.parse.quote(asks)
        jobs.append(("reddit.search.asks.month", f"https://www.reddit.com/r/{everything}/search.rss?q={q}&restrict_sr=on&sort=new&t=month"))
        jobs.append(("reddit.search.asks.top", f"https://www.reddit.com/r/{everything}/search.rss?q={q}&restrict_sr=on&sort=top&t=year"))
    return jobs


# ------------------------------------------------------------------ Hacker News, Stack Exchange, WordPress

def hn(query: str, cfg: dict) -> dict:
    since = int((datetime.now(timezone.utc) - timedelta(days=30)).timestamp())
    url = HN_SEARCH + "?" + urllib.parse.urlencode({"query": query, "hitsPerPage": 20, "numericFilters": f"created_at_i>{since}"})
    try:
        hits = json.loads(fetch(url))["hits"]
    except Exception as e:  # noqa: BLE001
        return {"source": f"hn.{query}", "url": url, "error": type(e).__name__, "items": []}
    items = []
    for h in hits:
        title = clean(h.get("title") or h.get("story_title") or "", 200)
        if not title:
            continue
        text = clean(h.get("story_text") or h.get("comment_text") or "", 320)
        item = {"title": title, "summary": text, "url": h.get("url") or f"https://news.ycombinator.com/item?id={h.get('objectID')}",
                "published": (h.get("created_at") or "")[:10], "points": h.get("points"), "comments": h.get("num_comments")}
        item.update(score(f"{title} {text}", cfg))
        item["signal_score"] += min(3, (h.get("points") or 0) / 40)
        items.append(item)
    return {"source": f"hn.{query}", "url": url, "error": None, "items": items}


def stackexchange(site: str, query: str, cfg: dict) -> dict:
    url = "https://api.stackexchange.com/2.3/search/advanced?" + urllib.parse.urlencode(
        {"order": "desc", "sort": "creation", "q": query, "site": site, "pagesize": 15, "filter": "default"})
    try:
        data = json.loads(fetch(url, accept="application/json"))
    except Exception as e:  # noqa: BLE001
        return {"source": f"se.{site}.{query}", "url": url, "error": type(e).__name__, "items": []}
    items = []
    for q in data.get("items", []):
        title = clean(q.get("title", ""), 200)
        item = {"title": title, "summary": " ".join(q.get("tags", [])), "url": q.get("link", ""),
                "published": datetime.fromtimestamp(q.get("creation_date", 0), timezone.utc).strftime("%Y-%m-%d"),
                "answers": q.get("answer_count"), "views": q.get("view_count")}
        item.update(score(title, cfg))
        if not q.get("is_answered"):
            item["signal_score"] += 1.5  # unanswered = still a gap
        items.append(item)
    return {"source": f"se.{site}.{query}", "url": url, "error": None, "items": items}


def wordpress_gap(search: str) -> dict:
    url = "https://api.wordpress.org/plugins/info/1.2/?" + urllib.parse.urlencode(
        {"action": "query_plugins", "request[search]": search, "request[per_page]": 10, "request[fields][description]": 0})
    try:
        plugins = json.loads(fetch(url, accept="application/json")).get("plugins", [])
    except Exception as e:  # noqa: BLE001
        return {"search": search, "error": type(e).__name__}
    if not plugins:
        return {"search": search, "plugins": 0, "gap": "no plugin at all"}
    now = datetime.now(timezone.utc)
    rows = []
    for p in plugins:
        try:
            upd = datetime.strptime(p.get("last_updated", "")[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            age_days = (now - upd).days
        except ValueError:
            age_days = 9999
        rows.append({"name": clean(p.get("name", ""), 60), "installs": p.get("active_installs", 0), "rating": p.get("rating", 0),
                     "reviews": p.get("num_ratings", 0), "updated_days_ago": age_days, "url": f"https://wordpress.org/plugins/{p.get('slug', '')}/"})
    top = sorted(rows, key=lambda r: r["installs"], reverse=True)[:5]
    stale = sum(1 for r in top if r["updated_days_ago"] > 365)
    weak = sum(1 for r in top if r["rating"] and r["rating"] < 80)
    gap = []
    if stale >= 3:
        gap.append(f"{stale} of the top 5 not updated in a year")
    if weak >= 2:
        gap.append(f"{weak} of the top 5 rated under 80%")
    if all(r["installs"] < 5000 for r in top):
        gap.append("nothing above 5k installs")
    return {"search": search, "plugins": len(plugins), "gap": "; ".join(gap) or None, "top": top}


# ------------------------------------------------------------------ GitHub: what builders make, what users beg for

def gh_get(url: str, env: dict) -> dict:
    headers = {"User-Agent": UA, "Accept": "application/vnd.github+json"}
    if env.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {env['GITHUB_TOKEN']}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read())


def github_sources(cfg: dict, env: dict) -> list[dict]:
    """Three GitHub lanes, spaced for the unauthenticated search limit (10 a minute):
      rising   new repos gaining stars fast in our topics: what builders think is worth making
      demand   open feature-request issues with many thumbs-up: users asking for a paid add-on
      hosted   popular self-hosted projects: people who want it without running a server will pay"""
    g = cfg.get("github") or {}
    if not g:
        return []
    today = datetime.now(timezone.utc).date()
    gap = 2 if env.get("GITHUB_TOKEN") else 7
    out = []

    def run(name: str, url: str, kind: str, mapper) -> None:
        try:
            data = gh_get(url, env)
        except urllib.error.HTTPError as e:
            out.append({"source": name, "url": url, "error": f"HTTP {e.code}", "items": []})
            return
        except Exception as e:  # noqa: BLE001
            out.append({"source": name, "url": url, "error": type(e).__name__, "items": []})
            return
        items = []
        for row in data.get("items", [])[:10]:
            it = mapper(row)
            if not it:
                continue
            it.update(score(f"{it['title']} {it['summary']}", cfg))
            it["ask_type"] = kind
            items.append(it)
        out.append({"source": name, "url": url, "error": None, "items": items})
        time.sleep(gap)

    def repo(row: dict, note: str = "") -> dict:
        stars = row.get("stargazers_count", 0)
        created = (row.get("created_at") or "")[:10]
        return {"title": f"{row.get('full_name')} - {clean(row.get('description') or '', 140)}",
                "summary": f"{stars} stars, {row.get('forks_count', 0)} forks, created {created}, language {row.get('language')}. {note}".strip(),
                "url": row.get("html_url", ""), "published": created, "stars": stars}

    since = (today - timedelta(days=g.get("rising_created_days", 60))).isoformat()
    for topic in g.get("rising_topics", []):
        q = urllib.parse.quote(f"topic:{topic} created:>{since} stars:>{g.get('rising_min_stars', 40)}")
        run(f"github.rising.{topic}", f"https://api.github.com/search/repositories?q={q}&sort=stars&order=desc&per_page=10",
            "proof", lambda r: {**repo(r), "signal_score_boost": 0})

    since_i = (today - timedelta(days=g.get("issue_created_days", 180))).isoformat()
    for term in g.get("issue_terms", []):
        q = urllib.parse.quote(f'"{term}" is:issue is:open label:enhancement,"feature request" reactions:>{g.get("issue_min_reactions", 15)} created:>{since_i}')
        run(f"github.demand.{term}", f"https://api.github.com/search/issues?q={q}&sort=reactions-%2B1&order=desc&per_page=10", "asking",
            lambda r: {"title": clean(r.get("title", ""), 160),
                       "summary": f"{r.get('reactions', {}).get('+1', 0)} thumbs-up, {r.get('comments', 0)} comments on {'/'.join(r.get('repository_url', '').split('/')[-2:])}",
                       "url": r.get("html_url", ""), "published": (r.get("created_at") or "")[:10]})

    pushed = (today - timedelta(days=30)).isoformat()
    # Mid-sized projects only: big infrastructure (databases, container platforms) already has hosted versions.
    q = urllib.parse.quote(f"topic:self-hosted stars:{g.get('selfhosted_min_stars', 2000)}..{g.get('selfhosted_max_stars', 20000)} pushed:>{pushed}")
    run("github.hosted", f"https://api.github.com/search/repositories?q={q}&sort=updated&order=desc&per_page=15", "proof",
        lambda r: repo(r, "Popular self-hosted project: check whether a paid hosted version exists; if not, hosting it is a build."))

    for s in out:  # stars and thumbs-up are the signal on GitHub, not wording
        for it in s["items"]:
            boost = min(4.0, (it.get("stars") or 0) / 150) if s["source"].startswith("github.rising") else 0
            m = re.match(r"(\d+) thumbs-up", it.get("summary", ""))
            if m:
                boost = min(5.0, int(m.group(1)) / 12)
            it["signal_score"] = round(it["signal_score"] + 2 + boost, 1)
            it.pop("signal_score_boost", None)
    return out


# ------------------------------------------------------------------ Quora via a search engine (titles only)

WEBSEARCH = [str(Path.home() / "hq" / "venv" / "bin" / "python"), str(Path.home() / "hq" / "server" / "websearch.py")]


def quora_websearch(query: str, cfg: dict) -> dict | None:
    """Brave/Bing through HQ's websearch helper: Quora question titles, snippets and URLs."""
    import subprocess
    try:
        plain = query.replace('"', "")  # quoted phrases plus site: make engines return Quora's own help pages
        r = subprocess.run(WEBSEARCH + [f"site:quora.com {plain}", "15"], capture_output=True, text=True, timeout=60)
        data = json.loads(r.stdout)
    except Exception:  # noqa: BLE001
        return None
    items = []
    for row in data.get("results", []):
        # Only question pages: quora.com/What-Is-The-Best-Tool-For-X (four or more words in the slug).
        if not re.match(r"https?://(www\.)?quora\.com/[A-Za-z0-9]+(-[A-Za-z0-9]+){3,}/?$", row["url"]):
            continue
        title = clean(re.sub(r"\s*-\s*Quora\s*$", "", row["title"]), 200)
        if len(title) < 15 or title.lower().startswith("quora"):
            # Engines often title the page just "Quora"; the URL slug is the question.
            slug = row["url"].rstrip("/").rsplit("/", 1)[-1]
            title = slug.replace("-", " ") + "?"
        item = {"title": title, "summary": clean(row.get("snippet", ""), 300), "url": row["url"], "published": ""}
        item.update(score(f"{title} {item['summary']}", cfg))
        items.append(item)
    if not items:
        return None
    return {"source": f"quora.{query}", "url": "websearch", "error": None, "engine": "ddgs", "items": items}


def quora(query: str, cfg: dict, env: dict) -> dict:
    found = quora_websearch(query, cfg)
    if found:
        return found
    q = urllib.parse.quote(f"site:quora.com {query}")
    for engine, pattern in QUORA_ENGINES:
        url = pattern.format(q=q)
        try:
            page = fetch(url, ua=BROWSER_UA, timeout=20).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            continue
        found = {}
        for m in re.finditer(r"https?://(?:www\.)?quora\.com/([A-Za-z0-9-]{12,})", html.unescape(urllib.parse.unquote(page))):
            slug = m.group(1).split("?")[0]
            if slug.lower().startswith(("profile", "topic", "unanswered", "search")):
                continue
            found[slug] = f"https://www.quora.com/{slug}"
        if found:
            items = []
            for slug, link in list(found.items())[:15]:
                title = slug.replace("-", " ")
                item = {"title": title, "summary": "", "url": link, "published": ""}
                item.update(score(title, cfg))
                items.append(item)
            return {"source": f"quora.{query}", "url": url, "error": None, "engine": engine, "items": items}
    token, actor = env.get("APIFY_TOKEN"), (cfg.get("apify") or {}).get("quora_actor")
    if token and actor:
        return quora_apify(query, cfg, token, actor)
    return {"source": f"quora.{query}", "url": "", "error": "no search engine answered from this IP; set APIFY_TOKEN + apify.quora_actor to use Apify", "items": []}


def quora_apify(query: str, cfg: dict, token: str, actor: str) -> dict:
    url = f"https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items?token={token}&timeout=120"
    body = json.dumps({"searches": [query], "maxItems": 20}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json", "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=150) as r:
            rows = json.loads(r.read())
    except Exception as e:  # noqa: BLE001
        return {"source": f"quora.{query}", "url": "apify", "error": f"apify {type(e).__name__}", "items": []}
    items = []
    for row in rows[:20]:
        title = clean(row.get("title") or row.get("question") or "", 200)
        if not title:
            continue
        item = {"title": title, "summary": clean(row.get("text") or row.get("answer") or "", 300), "url": row.get("url", ""), "published": ""}
        item.update(score(f"{title} {item['summary']}", cfg))
        items.append(item)
    return {"source": f"quora.{query}", "url": "apify", "error": None, "engine": "apify", "items": items}


# ------------------------------------------------------------------ main

def promo_inbox(max_age_h: int = 60) -> dict:
    """The founder's own Gmail Promotions from the last 48 h, uploaded nightly by the PC
    (promo_inbox_collector.py via Composio). Shows what businesses are paying to push right now."""
    path = BASE / "inbox" / "promo_inbox.json"
    if not path.exists():
        return {"available": False, "reason": "no promo inbox file yet (PC task 'Hermes Promo Inbox' uploads it nightly)"}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"available": False, "reason": f"unreadable: {exc}"}
    collected = datetime.strptime(data.get("collected_at_utc", "1970-01-01T00:00:00Z"), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    age_h = (datetime.now(timezone.utc) - collected).total_seconds() / 3600
    if age_h > max_age_h:
        return {"available": False, "reason": f"stale: collected {age_h:.0f} h ago (PC was probably off)"}
    keep = ("brand", "domain", "subject", "preview", "themes")
    return {"available": True, "collected_hours_ago": round(age_h, 1), "emails": data.get("emails"), "top_senders": data.get("top_senders"),
            "themes": data.get("themes"), "items": [{k: it.get(k) for k in keep} for it in (data.get("items") or [])[:60]]}


def novelty_block() -> str:
    try:
        sys.path.insert(0, str(Path.home() / "hq" / "server"))
        import scout_memory  # type: ignore
        return scout_memory.block("opportunity-scout") if hasattr(scout_memory, "block") else ""
    except Exception:  # noqa: BLE001
        return ""


def main() -> None:
    if not CONFIG_PATH.exists():
        print(json.dumps({"fatal": f"config not found: {CONFIG_PATH}"}))
        sys.exit(1)
    cfg = load_config()
    env = load_env()
    sources: list[dict] = []

    parallel = [("feed", n, u) for n, u in (cfg.get("feeds") or {}).items()]
    parallel += [("hn", q, "") for q in cfg.get("hn_queries") or []]
    se = cfg.get("stackexchange") or {}
    parallel += [("se", f"{s}|{q}", "") for s in se.get("sites", []) for q in se.get("queries", [])]

    def run(job):
        kind, a, b = job
        if kind == "feed":
            return parse_feed(a, b, cfg)
        if kind == "hn":
            return hn(a, cfg)
        if kind == "se":
            site, q = a.split("|", 1)
            return stackexchange(site, q, cfg)
        return quora(a, cfg, env)

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        sources += list(pool.map(run, parallel))

    for q in cfg.get("quora_queries") or []:  # sequential: search engines throttle bursts
        sources.append(quora(q, cfg, env))
        time.sleep(6)

    for name, url in reddit_jobs(cfg):  # sequential: Reddit throttles by IP
        res = parse_feed(name, url, cfg)
        if res.get("error") == "HTTP 429":
            time.sleep(45)
            res = parse_feed(name, url, cfg)
        sources.append(res)
        time.sleep(REDDIT_GAP_S)

    sources += github_sources(cfg, env)

    gaps = [wordpress_gap(s) for s in cfg.get("wordpress_searches") or []]

    pooled = []
    for s in sources:
        for it in s["items"]:
            pooled.append({**it, "source": s["source"]})
    seen, shortlist = set(), {"paying": [], "asking": [], "proof": [], "chatter": []}
    for it in sorted(pooled, key=lambda r: r["signal_score"], reverse=True):
        key = (it.get("url") or it["title"]).lower().rstrip("/")
        if key in seen or it["signal_score"] <= 0:
            continue
        seen.add(key)
        bucket = shortlist[it["ask_type"]]
        if len(bucket) < cfg.get("max_shortlist", 50) // 3 + 5:
            bucket.append(it)

    out = {
        "collected_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "limits": cfg.get("limits"),
        "channels_owned": cfg.get("channels_owned"),
        "assets": cfg.get("assets"),
        "sources_polled": len(sources),
        "source_failures": [{"source": s["source"], "error": s["error"]} for s in sources if s.get("error")],
        "items_collected": len(pooled),
        "by_source": {s["source"]: len(s["items"]) for s in sources},
        "shortlist": shortlist,
        "wordpress_gaps": [g for g in gaps if g.get("gap")],
        "wordpress_checked": [g["search"] for g in gaps],
        "promo_inbox": promo_inbox(),
        "reminders": [
            "Every pick must be buildable by one developer inside limits.max_build_days and sellable inside first_dollar_within_days.",
            "A 'paying' item is someone saying they would pay. A 'proof' item is someone showing a small tool earns. Both beat 'asking'.",
            "Quote the ask in the person's words with the URL. Never invent numbers, prices or customer counts.",
            "Prefer a build that sells through a channel the founder already owns (channels_owned).",
            "Wordpress_gaps: a search whose top plugins are stale or badly rated is a gap someone will pay to fill.",
            "promo_inbox: marketing emails the founder received (last 48 h). Read it as demand and competition signals: what "
            "companies pay to promote, which tools push upgrades or AI features, recurring offers that hint at a gap a small "
            "tool could fill (e.g. many brands selling the same add-on). Name the brand and quote the subject as evidence; never "
            "contact senders, never use the founder's personal details, and do not treat a promotion as proof of customer demand.",
            "GitHub: 'rising' repos show what builders make now; 'demand' issues with many thumbs-up are users asking for an add-on; 'hosted' projects are candidates for a paid hosted version. Check the licence before building on anyone's code.",
        ],
    }
    text = json.dumps(out, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    nb = novelty_block()
    print(text + ("\n\n" + nb if nb else ""))


if __name__ == "__main__":
    main()
