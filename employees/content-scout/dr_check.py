"""Domain Rating lookup with a 30-day cache, for the Content Desk winnability gate.

Usage (the agent runs this on the domains it sees in a live SERP):

    python dr_check.py brightlocal.com reddit.com somesmallblog.com
    python dr_check.py --json domain1 domain2

Prints one line per domain: DR, class, and what the class means for the gate.
Uses the free Ahrefs DR endpoint (no API units). Attribution: Domain Rating by
Ahrefs (https://ahrefs.com/).
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

HOME = Path.home()
BASE = HOME / "agents" / "content-scout" if (HOME / "agents" / "content-scout").exists() \
    else Path(__file__).resolve().parent
CACHE = BASE / "knowledge" / "dr_cache.json"
SEEDS = BASE / "content-seeds.yaml"
CACHE_DAYS = 30
ENDPOINT = "https://api.ahrefs.com/v3/public/domain-rating-free"
ENV_FILES = [
    HOME / ".hermes" / "profiles" / "chief-assistant" / ".env",
    HOME / ".hermes" / "profiles" / "growth-scout" / ".env",
    Path(os.environ.get("LOCALAPPDATA", "")) / "hermes" / "profiles" / "growth-scout" / ".env",
]


def load_yaml(text: str):
    try:
        import yaml
        return yaml.safe_load(text)
    except ImportError:
        from ruamel.yaml import YAML
        return YAML(typ="safe").load(text)


def api_key() -> str:
    key = os.environ.get("AHREFS_API_KEY", "").strip()
    if key:
        return key
    for env in ENV_FILES:
        try:
            for line in env.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("AHREFS_API_KEY="):
                    return line.split("=", 1)[1].strip()
        except OSError:
            continue
    return ""


def normalise(domain: str) -> str:
    domain = domain.strip().lower()
    domain = re.sub(r"^https?://", "", domain).split("/")[0]
    return domain[4:] if domain.startswith("www.") else domain


def load_cache() -> dict:
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_cache(cache: dict) -> None:
    try:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(cache, indent=1, sort_keys=True), encoding="utf-8")
    except OSError:
        pass


def fetch_dr(domain: str, key: str) -> float | None:
    url = ENDPOINT + "?" + urllib.parse.urlencode({"target": domain, "output": "json"})
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return float(json.loads(resp.read())["domain_rating"]["domain_rating"])
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                time.sleep(10 * (attempt + 1))
                continue
            return None
        except Exception:
            return None
    return None


def domain_class(domain: str, dr: float | None, seeds: dict) -> tuple[str, str]:
    """(class, meaning-for-the-gate)."""
    classes = seeds.get("domain_classes", {}) or {}
    gate = seeds.get("gate", {}) or {}
    for name, members in classes.items():
        if domain in (members or []) or any(domain.endswith("." + m) for m in (members or [])):
            meaning = {
                "ugc": "weakness signal - ignore its DR, the page has little authority",
                "video": "video result - weak for a text query",
                "google": "Google's own page - unbeatable on its topic",
                "publisher": "the wall - cannot outrank on authority",
                "competitor": "tool vendor - beatable only on a narrow, specific query",
            }.get(name, name)
            return name.upper(), meaning
    if dr is None:
        return "UNKNOWN", "no DR data - judge by page quality"
    ours = float(gate.get("our_dr", 51))
    if dr >= float(gate.get("wall_at", 85)):
        return "PUBLISHER", "the wall - cannot outrank on authority"
    if dr < float(gate.get("weak_below", 41)):
        return "WEAK", "clear target"
    if abs(dr - ours) <= float(gate.get("peer_band", 15)):
        return "PEER", "beatable with a better page"
    return "STRONG", "above our band - needs a much better page"


def main(argv: list[str]) -> int:
    as_json = "--json" in argv
    domains = [normalise(a) for a in argv if not a.startswith("--") and a.strip()]
    if not domains:
        print(__doc__)
        return 2
    try:
        seeds = load_yaml(SEEDS.read_text(encoding="utf-8")) or {}
    except OSError:
        seeds = {}
    key = api_key()
    cache = load_cache()
    now = time.time()
    rows = []
    for domain in dict.fromkeys(domains):
        entry = cache.get(domain)
        if entry and now - entry.get("at", 0) < CACHE_DAYS * 86400:
            dr, source = entry.get("dr"), "cache"
        elif key:
            dr, source = fetch_dr(domain, key), "live"
            cache[domain] = {"dr": dr, "at": now}
            time.sleep(1.2)
        else:
            dr, source = None, "no-key"
        cls, meaning = domain_class(domain, dr, seeds)
        rows.append({"domain": domain, "dr": dr, "class": cls, "meaning": meaning, "source": source})
    save_cache(cache)
    if as_json:
        print(json.dumps(rows, indent=1))
    else:
        print(f"{'domain':<32} {'DR':>5}  {'class':<10} meaning")
        for r in rows:
            dr = "-" if r["dr"] is None else f"{r['dr']:.0f}"
            print(f"{r['domain']:<32} {dr:>5}  {r['class']:<10} {r['meaning']}")
        if not key:
            print("\nNOTE: no AHREFS_API_KEY found; DR values unavailable, classes from lists only.")
        print("\nDomain Rating by Ahrefs (https://ahrefs.com/)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
