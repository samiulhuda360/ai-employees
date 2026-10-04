"""Candidate-video collector for the YouTube Watcher.

Reads watchlist.yaml, resolves channel handles to channel ids (cached), pulls
each channel's public RSS feed, and prints a ranked JSON shortlist of new
videos. Public feeds only - no API key, no login, no downloads.

The JSON output is injected into the agent's scheduled prompt. The agent does
the transcribing and the thinking; this script only decides what is worth a
look.
"""

from __future__ import annotations

import concurrent.futures
import html
import json
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml


SCRIPT_BASE = Path(__file__).resolve().parent
CANONICAL_BASE = Path(__file__).resolve().parent
# Cron may run the synced script with either the workspace or the profile's
# scripts directory as its cwd. Resolve the canonical watchlist independently
# of cwd so scheduled runs use the same watchlist and seen-video ledger.
BASE = CANONICAL_BASE if (CANONICAL_BASE / "watchlist.yaml").exists() else SCRIPT_BASE
WATCHLIST = BASE / "watchlist.yaml"
KNOWLEDGE = BASE / "knowledge"
TRANSCRIPTS = KNOWLEDGE / "transcripts"
ID_CACHE = KNOWLEDGE / "channel_ids.json"
LEDGER = KNOWLEDGE / "seen_videos.json"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/140.0 Safari/537.36"
)
FEED_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
CHANNEL_URL = "https://www.youtube.com/@{handle}"
CHANNEL_VIDEOS_URL = "https://www.youtube.com/@{handle}/videos"
# The channel page mentions many channel ids (sidebar, featured, related), so
# resolve through the page's own RSS alternate link, then its canonical link.
# Matching a bare "channelId" silently returns a neighbouring channel.
CHANNEL_ID_RE = re.compile(r'rel="alternate"[^>]*href="[^"]*channel_id=(UC[\w-]{22})"')
CANONICAL_ID_RE = re.compile(
    r'<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[\w-]{22})"'
)
LEDGER_KEEP_DAYS = 45


def fetch(url: str, timeout: int = 20) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/atom+xml, application/xml, text/xml, text/html, */*",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def clean(value: str, limit: int = 500) -> str:
    value = html.unescape(re.sub(r"<[^>]+>", " ", value or ""))
    value = re.sub(r"\s+", " ", value).strip()
    return value[:limit]


def load_json(path: Path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return fallback


def save_json(path: Path, payload) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    except OSError:
        pass


def local_tag(node: ET.Element) -> str:
    return node.tag.rsplit("}", 1)[-1].lower()


def find_text(node: ET.Element, name: str) -> str:
    for child in node.iter():
        if local_tag(child) == name and child.text:
            return child.text.strip()
    return ""


def parse_published(value: str) -> datetime | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_view_count(value: str) -> int:
    text = (value or "").lower()
    words = {"thousand": "k", "million": "m", "billion": "b"}
    for word, suffix in words.items():
        text = text.replace(f" {word}", suffix)
    match = re.search(r"([\d,.]+)\s*([kmb]?)\s*(?:views?)?$", text.strip())
    if not match or not re.search(r"\d", match.group(1)):
        return 0
    try:
        number = float(match.group(1).replace(",", ""))
    except ValueError:
        return 0
    multiplier = {"": 1, "k": 1_000, "m": 1_000_000, "b": 1_000_000_000}
    return int(number * multiplier[match.group(2)])


def parse_relative_published(value: str, now: datetime) -> datetime | None:
    text = (value or "").strip().lower()
    text = re.sub(r"^(streamed|premiered)\s+", "", text)
    compact = {"m": "minute", "min": "minute", "h": "hour", "d": "day", "w": "week", "mo": "month", "y": "year"}
    match = re.search(r"(\d+)\s*(mo|min|m|h|d|w|y)\s+ago", text)
    if match:
        text = f"{match.group(1)} {compact[match.group(2)]} ago"
    match = re.search(r"(\d+)\s+(minute|hour|day|week|month|year)s?\s+ago", text)
    if not match:
        return None
    count = int(match.group(1))
    unit = match.group(2)
    days = {"day": 1, "week": 7, "month": 30, "year": 365}
    if unit == "minute":
        delta = timedelta(minutes=count)
    elif unit == "hour":
        delta = timedelta(hours=count)
    else:
        delta = timedelta(days=count * days[unit])
    return now - delta


def extract_initial_data(page: str) -> dict:
    for marker in ("var ytInitialData = ", 'window["ytInitialData"] = ', "ytInitialData = "):
        start = page.find(marker)
        if start >= 0:
            payload, _ = json.JSONDecoder().raw_decode(page[start + len(marker) :])
            return payload
    raise ValueError("ytInitialData not found")


def parse_channel_page(body: bytes, channel: dict, per_channel_limit: int, now: datetime) -> list[dict]:
    data = extract_initial_data(body.decode("utf-8", "replace"))
    lockups = []

    def walk(node) -> None:
        if isinstance(node, dict):
            if "lockupViewModel" in node:
                lockups.append(node["lockupViewModel"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(data)
    videos = []
    seen = set()
    for item in lockups:
        video_id = item.get("contentId", "")
        if not video_id or video_id in seen:
            continue
        metadata = item.get("metadata", {}).get("lockupMetadataViewModel", {})
        title = metadata.get("title", {}).get("content", "")
        if not title:
            continue
        parts = []
        views = 0
        published = None
        rows = metadata.get("metadata", {}).get("contentMetadataViewModel", {}).get("metadataRows", [])
        for row in rows:
            for part in row.get("metadataParts", []):
                for value in (part.get("accessibilityLabel", ""), part.get("text", {}).get("content", "")):
                    if not value:
                        continue
                    parts.append(value)
                    if published is None and "ago" in value.lower():
                        published = parse_relative_published(value, now)
                    if not views and "view" in value.lower():
                        views = parse_view_count(value)
        if published is None:
            continue
        seen.add(video_id)
        videos.append(
            {
                "video_id": video_id,
                "title": clean(title, 240),
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "published": published.isoformat(),
                "description": "",
                "views": views,
            }
        )
        if len(videos) >= per_channel_limit:
            break
    return videos


def read_channel_page(channel: dict, per_channel_limit: int) -> dict:
    handle = (channel.get("handle") or "").lstrip("@")
    if not handle:
        return {"channel": channel, "error": "channel page fallback requires handle", "videos": []}
    url = CHANNEL_VIDEOS_URL.format(handle=handle)
    try:
        videos = parse_channel_page(fetch(url), channel, per_channel_limit, utc_now())
    except urllib.error.HTTPError as exc:
        return {"channel": channel, "error": f"channel page HTTP {exc.code}", "videos": []}
    except Exception as exc:
        return {"channel": channel, "error": f"channel page {type(exc).__name__}", "videos": []}
    if not videos:
        return {"channel": channel, "error": "channel page returned no dated videos", "videos": []}
    return {
        "channel": channel,
        "feed_title": channel.get("name") or handle,
        "error": None,
        "videos": videos,
        "source": "channel_page",
    }


def resolve_handle(handle: str, timeout: int) -> str:
    """Resolve an @handle to a UC... channel id via the public channel page."""
    try:
        body = fetch(CHANNEL_URL.format(handle=handle.lstrip("@")), timeout=timeout)
    except Exception:
        return ""
    page = body.decode("utf-8", "replace")
    match = CHANNEL_ID_RE.search(page) or CANONICAL_ID_RE.search(page)
    return match.group(1) if match else ""


def read_feed(channel: dict, per_channel_limit: int) -> dict:
    channel_id = channel.get("id", "")
    url = FEED_URL.format(channel_id=channel_id)
    try:
        root = ET.fromstring(fetch(url))
    except urllib.error.HTTPError:
        return read_channel_page(channel, per_channel_limit)
    except Exception:
        return read_channel_page(channel, per_channel_limit)

    feed_title = ""
    for child in root:
        if local_tag(child) == "title" and child.text:
            feed_title = child.text.strip()
            break

    videos = []
    for entry in root:
        if local_tag(entry) != "entry":
            continue
        video_id = find_text(entry, "videoid")
        title = ""
        link = ""
        published = ""
        description = ""
        views = 0
        for child in entry:
            tag = local_tag(child)
            if tag == "title" and child.text and not title:
                title = child.text.strip()
            elif tag == "link" and not link:
                link = child.attrib.get("href", "")
            elif tag == "published" and child.text:
                published = child.text.strip()
        for node in entry.iter():
            tag = local_tag(node)
            if tag == "description" and node.text and not description:
                description = node.text.strip()
            elif tag == "statistics":
                try:
                    views = int(node.attrib.get("views", "0"))
                except ValueError:
                    views = 0
        if not video_id or not title:
            continue
        videos.append(
            {
                "video_id": video_id,
                "title": clean(title, 240),
                "url": link or f"https://www.youtube.com/watch?v={video_id}",
                "published": published,
                "description": clean(description, 400),
                "views": views,
            }
        )
        if len(videos) >= per_channel_limit:
            break
    return {"channel": channel, "feed_title": feed_title, "error": None, "videos": videos, "source": "rss"}


def score_video(video: dict, channel: dict, config: dict, age_days: float) -> dict:
    title_blob = f"{video['title']} {video['description']}".lower()
    lane_keywords = config.get("lane_keywords", {}) or {}
    channel_lanes = channel.get("lanes") or list(lane_keywords)

    hits: dict[str, list[str]] = {}
    for lane, words in lane_keywords.items():
        matched = [word for word in (words or []) if word.lower() in title_blob]
        if matched:
            hits[lane] = matched

    # Keyword relevance, weighted toward the lanes this channel is watched for.
    relevance = 0.0
    for lane, matched in hits.items():
        weight = 1.5 if lane in channel_lanes else 0.8
        relevance += weight * min(len(matched), 3)

    tier = int(channel.get("tier", 2) or 2)
    tier_bonus = 3.0 if tier == 1 else 0.0

    # Recency: full credit today, decaying across the lookback window.
    lookback = max(float(config.get("defaults", {}).get("lookback_days", 3)), 1.0)
    recency = max(0.0, 3.0 * (1.0 - (age_days / lookback)))

    demotions = [
        pattern
        for pattern in (config.get("demote_patterns") or [])
        if pattern.lower() in title_blob
    ]
    penalty = 4.0 * len(demotions)

    score = round(relevance + tier_bonus + recency - penalty, 2)
    return {
        "score": score,
        "lane_hits": hits,
        "lanes": sorted(set(channel_lanes) | set(hits)),
        "demoted_for": demotions,
    }


def fleet_study() -> dict:
    """Each agent's study list with this week's forum, blog and news leads (see
    web_learning_collector.py). Never lets a web problem break the video run."""
    try:
        sys.path.insert(0, str(BASE))
        import web_learning_collector
        return web_learning_collector.leads()
    except Exception as exc:  # noqa: BLE001
        return {"study_lists": {}, "web_source_errors": [{"error": f"{type(exc).__name__}: {exc}"}]}


def main() -> None:
    if not WATCHLIST.exists():
        print(json.dumps({"fatal": f"watchlist not found: {WATCHLIST}"}))
        sys.exit(1)

    config = yaml.safe_load(WATCHLIST.read_text(encoding="utf-8")) or {}
    defaults = config.get("defaults", {}) or {}
    lookback_days = float(defaults.get("lookback_days", 3))
    per_channel_limit = int(defaults.get("per_channel_limit", 6))
    max_candidates = int(defaults.get("max_candidates", 40))
    resolve_timeout = int(defaults.get("resolve_timeout_seconds", 20))

    channels = [dict(row) for row in (config.get("channels") or []) if isinstance(row, dict)]
    id_cache = load_json(ID_CACHE, {})
    resolved_now = []
    unresolved = []

    for channel in channels:
        if channel.get("id"):
            continue
        handle = (channel.get("handle") or "").lstrip("@")
        if not handle:
            unresolved.append(channel.get("name") or "<unnamed>")
            continue
        cached = id_cache.get(handle.lower())
        if cached:
            channel["id"] = cached
            continue
        channel_id = resolve_handle(handle, resolve_timeout)
        if channel_id:
            channel["id"] = channel_id
            id_cache[handle.lower()] = channel_id
            resolved_now.append({"handle": handle, "channel_id": channel_id})
        else:
            unresolved.append(f"@{handle}")

    if resolved_now:
        save_json(ID_CACHE, id_cache)

    live = [channel for channel in channels if channel.get("id")]
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda row: read_feed(row, per_channel_limit), live))

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=lookback_days)
    ledger = load_json(LEDGER, {})
    transcript_ids = set()
    if TRANSCRIPTS.exists():
        transcript_ids = {path.stem.split("__")[0] for path in TRANSCRIPTS.glob("*.txt")}

    candidates = []
    feed_errors = []
    renamed = []
    for result in results:
        channel = result["channel"]
        if result.get("error"):
            feed_errors.append(
                {
                    "name": channel.get("name"),
                    "channel_id": channel.get("id"),
                    "error": result["error"],
                }
            )
            continue
        feed_title = result.get("feed_title") or ""
        expected = channel.get("name") or ""
        if feed_title and expected and feed_title.lower() != expected.lower():
            renamed.append(
                {"watchlist_name": expected, "feed_name": feed_title, "channel_id": channel.get("id")}
            )
        for video in result["videos"]:
            published = parse_published(video["published"])
            if published is None or published < cutoff:
                continue
            age_days = max((now - published).total_seconds() / 86400.0, 0.0)
            ranking = score_video(video, channel, config, age_days)
            entry = ledger.get(video["video_id"]) or {}
            first_seen = entry.get("first_seen") or now.isoformat(timespec="seconds")
            ledger[video["video_id"]] = {
                "first_seen": first_seen,
                "title": video["title"],
                "channel": channel.get("name"),
            }
            candidates.append(
                {
                    "video_id": video["video_id"],
                    "title": video["title"],
                    "url": video["url"],
                    "channel": feed_title or channel.get("name"),
                    "channel_id": channel.get("id"),
                    "tier": int(channel.get("tier", 2) or 2),
                    "published": video["published"],
                    "age_days": round(age_days, 2),
                    "views_at_collection": video["views"],
                    "description": video["description"],
                    "already_transcribed": video["video_id"] in transcript_ids,
                    "first_seen": first_seen,
                    **ranking,
                }
            )

    horizon = (now - timedelta(days=LEDGER_KEEP_DAYS)).isoformat(timespec="seconds")
    ledger = {key: row for key, row in ledger.items() if (row.get("first_seen") or "") >= horizon}
    save_json(LEDGER, ledger)

    candidates.sort(key=lambda row: (row["score"], -row["age_days"]), reverse=True)
    shortlist = candidates[:max_candidates]

    output = {
        "collected_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "watchlist": str(WATCHLIST),
        "knowledge_base": str(BASE / "knowledge-base.md"),
        "transcript_cache": str(TRANSCRIPTS),
        "transcribe_command": (
            f'uv run python "{BASE / "transcribe_video.py"}" "<VIDEO_URL>" --timestamps'
        ),
        "window_days": lookback_days,
        "channels_polled": len(live),
        "channels_failed": feed_errors,
        "channels_unresolved": unresolved,
        "channels_resolved_this_run": resolved_now,
        "channels_possibly_renamed": renamed,
        "new_videos_found": len(candidates),
        "candidates": shortlist,
        "search_leads": config.get("search_leads") or [],
        **fleet_study(),
        "reminders": [
            "Transcribe before judging. Title and description are marketing.",
            "Read knowledge-base.md first; do not re-learn what is already recorded.",
            "Mine at most five videos per run; depth over coverage.",
            "Label every kept item DEMONSTRATED, CLAIMED, or VERIFIED.",
            "You teach the fleet: study_lists says what each agent needs and gives web leads. Read a page before learning from it.",
            "Every knowledge-base entry needs a '- for:' line naming the agent ids it helps.",
        ],
    }
    print(json.dumps(output, sort_keys=True, separators=(",", ":"), ensure_ascii=True))


if __name__ == "__main__":
    main()
