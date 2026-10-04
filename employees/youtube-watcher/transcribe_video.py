"""Transcript fetcher for the YouTube Watcher.

Fetches a video's transcript, caches it under knowledge/transcripts/, and
prints it. Transcripts and metadata only - this script never downloads audio
or video.

Usage:
    uv run python transcribe_video.py "<URL or video id>" [options]

Options:
    --timestamps        prefix each line with mm:ss (default output is plain)
    --json              print the full structured payload instead of text
    --language en,tr    preferred language chain (default: en, then anything)
    --refresh           ignore the cache and refetch
    --meta-only         print title/channel/duration without the transcript
    --max-chars N       truncate printed text at N characters (cache is full)

Exit codes: 0 ok, 2 no transcript available, 3 bad input, 4 missing dependency.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
TRANSCRIPTS = BASE / "knowledge" / "transcripts"
USER_AGENT = "YouTube-Watcher/1.0 (+hermes-agent)"
OEMBED = "https://www.youtube.com/oembed?{query}"

ID_PATTERNS = (
    r"(?:v=|youtu\.be/|shorts/|embed/|live/|/v/)([a-zA-Z0-9_-]{11})",
    r"^([a-zA-Z0-9_-]{11})$",
)


def extract_video_id(value: str) -> str:
    value = (value or "").strip()
    for pattern in ID_PATTERNS:
        match = re.search(pattern, value)
        if match:
            return match.group(1)
    return ""


def timestamp(seconds: float) -> str:
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def slug(value: str, limit: int = 60) -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value or "").strip("-").lower()
    return value[:limit] or "untitled"


def fetch_metadata(video_id: str) -> dict:
    """Title and channel via public oEmbed - no API key, no login."""
    query = urllib.parse.urlencode(
        {"url": f"https://www.youtube.com/watch?v={video_id}", "format": "json"}
    )
    request = urllib.request.Request(
        OEMBED.format(query=query), headers={"User-Agent": USER_AGENT}
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
        return {
            "title": payload.get("title", ""),
            "channel": payload.get("author_name", ""),
            "channel_url": payload.get("author_url", ""),
        }
    except Exception:
        return {"title": "", "channel": "", "channel_url": ""}


def fetch_segments(video_id: str, languages: list[str]) -> tuple[list[dict], str, str]:
    """Return (segments, language, source). Supports v1.x and legacy APIs."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        print(
            "Missing dependency. Run: uv pip install youtube-transcript-api",
            file=sys.stderr,
        )
        sys.exit(4)

    attempts = [languages] if languages else []
    attempts.append([])  # final attempt: whatever the video has

    last_error: Exception | None = None
    for wanted in attempts:
        try:
            if hasattr(YouTubeTranscriptApi, "fetch"):
                api = YouTubeTranscriptApi()
                fetched = (
                    api.fetch(video_id, languages=wanted) if wanted else api.fetch(video_id)
                )
                language = getattr(fetched, "language_code", "") or "unknown"
                segments = [
                    {
                        "text": getattr(item, "text", ""),
                        "start": float(getattr(item, "start", 0.0) or 0.0),
                        "duration": float(getattr(item, "duration", 0.0) or 0.0),
                    }
                    for item in fetched
                ]
            else:  # legacy 0.6.x surface
                raw = YouTubeTranscriptApi.get_transcript(
                    video_id, languages=wanted or ["en"]
                )
                language = (wanted or ["en"])[0]
                segments = [
                    {
                        "text": item.get("text", ""),
                        "start": float(item.get("start", 0.0)),
                        "duration": float(item.get("duration", 0.0)),
                    }
                    for item in raw
                ]
            segments = [item for item in segments if item["text"].strip()]
            if segments:
                return segments, language, "youtube-transcript-api"
        except Exception as exc:  # no transcript in that language, or blocked
            last_error = exc
            continue

    reason = f"{type(last_error).__name__}: {last_error}" if last_error else "no segments"
    print(f"NO_TRANSCRIPT {video_id} - {reason}", file=sys.stderr)
    sys.exit(2)


def cache_path(video_id: str, title: str) -> Path:
    return TRANSCRIPTS / f"{video_id}__{slug(title)}.txt"


def find_cached(video_id: str) -> Path | None:
    if not TRANSCRIPTS.exists():
        return None
    for path in TRANSCRIPTS.glob(f"{video_id}__*.txt"):
        return path
    return None


def main() -> None:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("video", help="YouTube URL or 11-character video id")
    parser.add_argument("--timestamps", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--language", default="en")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--meta-only", action="store_true")
    parser.add_argument("--max-chars", type=int, default=0)
    args = parser.parse_args()

    video_id = extract_video_id(args.video)
    if not video_id:
        print(f"BAD_INPUT could not extract a video id from: {args.video}", file=sys.stderr)
        sys.exit(3)

    cached = None if args.refresh else find_cached(video_id)
    if cached is not None:
        body = cached.read_text(encoding="utf-8", errors="replace")
        if args.meta_only:
            print("\n".join(body.splitlines()[:8]))
            return
        if args.max_chars:
            body = body[: args.max_chars]
        print(body)
        return

    metadata = fetch_metadata(video_id)
    if args.meta_only:
        print(json.dumps({"video_id": video_id, **metadata}, ensure_ascii=True))
        return

    languages = [code.strip() for code in (args.language or "").split(",") if code.strip()]
    segments, language, source = fetch_segments(video_id, languages)

    plain = " ".join(item["text"].strip() for item in segments)
    stamped = "\n".join(f"{timestamp(item['start'])} {item['text'].strip()}" for item in segments)
    fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    duration_guess = segments[-1]["start"] + segments[-1]["duration"] if segments else 0.0

    header = "\n".join(
        [
            f"# {metadata['title'] or video_id}",
            f"channel: {metadata['channel'] or 'unknown'}",
            f"url: https://www.youtube.com/watch?v={video_id}",
            f"language: {language}",
            f"approx_length: {timestamp(duration_guess)}",
            f"segments: {len(segments)}",
            f"characters: {len(plain)}",
            f"fetched_at_utc: {fetched_at}",
            "",
        ]
    )

    path = cache_path(video_id, metadata["title"] or video_id)
    try:
        TRANSCRIPTS.mkdir(parents=True, exist_ok=True)
        path.write_text(header + stamped + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"CACHE_WRITE_FAILED {type(exc).__name__}", file=sys.stderr)

    if args.json:
        payload = {
            "video_id": video_id,
            "title": metadata["title"],
            "channel": metadata["channel"],
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "language": language,
            "source": source,
            "approx_length": timestamp(duration_guess),
            "characters": len(plain),
            "cached_at": str(path),
            "segments": segments,
        }
        print(json.dumps(payload, ensure_ascii=True))
        return

    body = header + (stamped if args.timestamps else plain)
    if args.max_chars:
        body = body[: args.max_chars]
    print(body)


if __name__ == "__main__":
    main()
