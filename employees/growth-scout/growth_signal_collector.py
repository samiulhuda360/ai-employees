"""Public signal collector for the Fernway Growth Scout.

The script uses only public, no-login feeds/APIs. Its JSON output is injected
into Hermes scheduled research prompts; the agent performs the deeper analysis.
"""

from __future__ import annotations

from pathlib import Path

import concurrent.futures
import html
import json
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET


USER_AGENT = "Fernway-Growth-Scout/1.0"
HN_SEARCH = "https://hn.algolia.com/api/v1/search_by_date"
KEYWORDS = (
    "local seo", "google business profile", "google maps", "map pack",
    "rank tracker", "geo grid", "citation", "local citation", "reviews",
    "service area", "agency reporting", "ai visibility", "ai search",
    "local search", "gbp", "indexing", "location page",
)

FEEDS = {
    "google_search_central": "https://feeds.feedburner.com/blogspot/amDG",
    "search_engine_roundtable": "https://www.seroundtable.com/index.rdf",
    "whitespark": "https://whitespark.ca/blog/feed/",
    "product_hunt": "https://www.producthunt.com/feed",
    "reddit_localseo": "https://www.reddit.com/r/localseo/new/.rss",
    "reddit_seo": "https://www.reddit.com/r/SEO/new/.rss",
    "reddit_googlebusiness": "https://www.reddit.com/r/GoogleMyBusiness/new/.rss",
    "local_search_forum": "https://localsearchforum.com/forums/-/index.rss",
}


def fetch(url: str, timeout: int = 20) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json, application/xml, text/xml, */*"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def clean(value: str, limit: int = 500) -> str:
    value = html.unescape(re.sub(r"<[^>]+>", " ", value or ""))
    value = re.sub(r"\s+", " ", value).strip()
    return value[:limit]


def child_text(node: ET.Element, names: tuple[str, ...]) -> str:
    for child in list(node):
        local = child.tag.rsplit("}", 1)[-1].lower()
        if local in names and child.text:
            return child.text.strip()
    return ""


def parse_feed(name: str, url: str) -> dict:
    try:
        root = ET.fromstring(fetch(url))
        rows = []
        for node in root.iter():
            local = node.tag.rsplit("}", 1)[-1].lower()
            if local not in {"item", "entry"}:
                continue
            title = child_text(node, ("title",))
            published = child_text(node, ("published", "updated", "pubdate", "date"))
            summary = child_text(node, ("summary", "description", "content"))
            link = child_text(node, ("link",))
            if not link:
                for child in list(node):
                    if child.tag.rsplit("}", 1)[-1].lower() == "link":
                        link = child.attrib.get("href", "")
                        if link:
                            break
            if title and link:
                rows.append(
                    {
                        "title": clean(title, 240),
                        "published": clean(published, 100),
                        "url": link.strip(),
                        "summary": clean(summary),
                    }
                )
            if len(rows) >= 20:
                break
        return {"source": name, "url": url, "items": rows}
    except Exception as exc:
        return {"source": name, "url": url, "source_error": type(exc).__name__, "items": []}


def hn_search() -> list[dict]:
    rows: list[dict] = []
    for query in ("local SEO", "Google Business Profile", "rank tracking", "AI search SEO"):
        try:
            url = HN_SEARCH + "?" + urllib.parse.urlencode({"query": query, "tags": "story", "hitsPerPage": 12})
            payload = json.loads(fetch(url).decode("utf-8"))
            for hit in payload.get("hits", []):
                rows.append(
                    {
                        "title": clean(hit.get("title") or hit.get("story_title") or "", 240),
                        "published": hit.get("created_at", ""),
                        "points": hit.get("points", 0) or 0,
                        "comments": hit.get("num_comments", 0) or 0,
                        "url": hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}",
                        "discussion": f"https://news.ycombinator.com/item?id={hit.get('objectID')}",
                    }
                )
        except Exception:
            continue
    unique = {row["discussion"]: row for row in rows if row.get("title")}
    return sorted(unique.values(), key=lambda row: (row["points"] + row["comments"]), reverse=True)[:25]


def main() -> None:
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        feeds = list(executor.map(lambda item: parse_feed(*item), FEEDS.items()))

    output = {
        "collected_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "feeds": feeds,
        "hacker_news": hn_search(),
        "required_live_research": {
            "official": [
                "https://developers.google.com/search/blog",
                "https://support.google.com/business/",
                "https://status.search.google.com/summary",
            ],
            "competitors": [
                "https://gridmybusiness.com/whats-new",
                "https://help.brightlocal.com/hc/en-us/categories/360003052040-Product-Updates",
                "https://whitespark.ca/blog/",
                "https://www.localfalcon.com/blog",
                "https://www.localviking.com/blog/",
                "https://www.semrush.com/local/",
            ],
            "communities": [
                "https://localsearchforum.com/",
                "https://www.reddit.com/r/localseo/new/",
                "https://www.reddit.com/r/GoogleMyBusiness/new/",
                "https://www.reddit.com/r/SEO/new/",
            ],
            "social_searches": [
                "site:x.com local SEO agency GBP feature",
                "site:linkedin.com/posts local SEO agency Google Business Profile",
                "site:youtube.com local SEO tool review grid rank tracker",
                "site:reddit.com/r/localseo tool pain agency report",
            ],
            "fernway": [
                "https://fernway.example/",
                "https://app.fernway.example/",
            ],
        },
    }
    print(json.dumps(output, sort_keys=True, separators=(",", ":"), ensure_ascii=True))



def _novelty_block() -> str:
    """Append HQ's memory of recent picks and today's lane (see ~/hq/server/scout_memory.py)."""
    try:
        import sys as _sys
        _sys.path.insert(0, str(Path.home() / "hq" / "server"))
        import scout_memory
        return scout_memory.block("growth-scout")
    except Exception as exc:  # never break the collector
        return "(novelty memory unavailable: " + str(exc) + ")"

if __name__ == "__main__":
    main()
    print(_novelty_block())
