"""Promo inbox collector (PC): read the last day's Gmail *Promotions* through Composio and send a
compact digest to the server, where Opportunity Scout's Cash Builds run reads it as a lane.

    python promo_inbox_collector.py            # fetch, write pc-out/promo_inbox.json, upload
    python promo_inbox_collector.py --no-upload

Read-only: one GMAIL_FETCH_EMAILS call (category:promotions). Composio is installed and logged
in inside WSL Ubuntu, so the call runs there. Only marketing content is kept: sender name and
domain, subject, send time and a short preview. Recipient addresses and full bodies are dropped.
Runs from the Windows task "Hermes Promo Inbox" (daily 20:40, hidden), before the server's
08:00 Cash Builds job.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "pc-out" / "promo_inbox.json"
LOG = HERE / "pc-out" / "promo_inbox.log"
SERVER = "hermes@hq.example.com"
REMOTE = "agents/opportunity-scout/inbox/promo_inbox.json"
SSH_EXE = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "OpenSSH" / "ssh.exe"
SSH_KEY = Path.home() / ".ssh" / "id_ed25519"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
QUERY = "category:promotions newer_than:2d"  # overlaps daily runs so nothing falls between them
MAX_RESULTS = 120

# Runs inside WSL: call Composio, read its output file, print compact JSON lines.
WSL_SCRIPT = r'''
composio execute GMAIL_FETCH_EMAILS -d '{"query": "%QUERY%", "max_results": %MAX%, "include_payload": false, "verbose": false}' 2>&1 | python3 -c '
import sys, json
raw = sys.stdin.read()
d = json.loads(raw[raw.find("{"):])
if d.get("successful") is False and not d.get("outputFilePath"):
    print(json.dumps({"error": str(d.get("error"))[:300]})); sys.exit()
p = d.get("outputFilePath") or (d.get("data") or {}).get("outputFilePath")
out = json.load(open(p)) if p else d
out = out.get("data", out)
for m in out.get("messages") or []:
    prev = m.get("preview")
    body = prev.get("body", "") if isinstance(prev, dict) else str(prev or "")
    print(json.dumps({"sender": m.get("sender", ""), "subject": m.get("subject", ""), "ts": m.get("messageTimestamp", ""), "preview": (body or m.get("messageText") or "")[:600]}))
'
'''

THEMES = {
    "discount": r"\d+\s?% off|\bsale\b|save \$?\d|\bdeal\b|discount|clearance|cashback",
    "free_offer": r"\bfree\b|free trial|complimentary|gift",
    "launch_or_new": r"\bnew\b|introducing|launch|just dropped|now available|meet the",
    "ai": r"\bAI\b|artificial intelligence|\bGPT\b|copilot|agent",
    "urgency": r"last chance|ends (today|tonight|soon)|today only|hours left|final day|don.t miss|limited time",
    "event": r"webinar|event|live\b|register|summit|workshop",
    "content_or_tips": r"how to|tips|guide|ideas|trending|inspiration|newsletter",
    "subscription_or_pricing": r"plan|subscription|upgrade|pricing|renew|membership|annual",
}
INVISIBLE = re.compile(r"[\u034f\u200b-\u200f\u2028-\u202f\u2060-\u206f\ufeff\u00ad]+")


def log(msg: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M} {msg}\n")


def clean(text: str, limit: int) -> str:
    text = INVISIBLE.sub(" ", text or "")
    return re.sub(r"\s+", " ", text).strip()[:limit]


def split_sender(sender: str) -> tuple[str, str]:
    m = re.match(r'\s*"?([^"<]*)"?\s*<([^>]+)>', sender or "")
    name, addr = (m.group(1).strip(), m.group(2)) if m else ("", sender or "")
    domain = addr.split("@")[-1].lower().strip() if "@" in addr else ""
    parts = domain.split(".")
    root = ".".join(parts[-3:]) if len(parts) > 2 and parts[-2] in ("co", "com", "org", "net", "ac", "gov") else ".".join(parts[-2:])
    return name or root, root


def fetch() -> list[dict]:
    script = WSL_SCRIPT.replace("%QUERY%", QUERY).replace("%MAX%", str(MAX_RESULTS))
    run = subprocess.run(["wsl.exe", "-d", "Ubuntu", "--", "bash", "-ls"], input=script, capture_output=True,
                         text=True, encoding="utf-8", errors="replace", timeout=240, creationflags=NO_WINDOW)
    rows = []
    for line in run.stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    if rows and "error" in rows[0]:
        raise RuntimeError(rows[0]["error"])
    return rows


def digest(rows: list[dict]) -> dict:
    items, by_domain, themes = [], Counter(), Counter()
    for r in rows:
        name, domain = split_sender(r.get("sender", ""))
        subject, preview = clean(r.get("subject", ""), 160), clean(r.get("preview", ""), 220)
        text = f"{subject} {preview}"
        tags = [t for t, rx in THEMES.items() if re.search(rx, text, re.I)]
        themes.update(tags)
        by_domain[domain] += 1
        items.append({"brand": name[:60], "domain": domain, "subject": subject, "preview": preview, "sent": r.get("ts", "")[:16], "themes": tags})
    return {
        "collected_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "query": QUERY,
        "emails": len(items),
        "senders": len(by_domain),
        "top_senders": dict(by_domain.most_common(25)),
        "themes": dict(themes.most_common()),
        "items": items[:MAX_RESULTS],
        "note": "Marketing emails received by the founder in the last 48 h (Gmail Promotions). Sender, subject, time, short preview only.",
    }


def upload(path: Path) -> None:
    cmd = [str(SSH_EXE if SSH_EXE.exists() else "ssh"), "-i", str(SSH_KEY), "-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
           "-o", "StrictHostKeyChecking=accept-new", SERVER, f"mkdir -p $(dirname {REMOTE}) && cat > {REMOTE}"]
    res = subprocess.run(cmd, input=path.read_bytes(), capture_output=True, timeout=120, creationflags=NO_WINDOW)
    if res.returncode != 0:
        raise RuntimeError(f"upload failed: {res.stderr.decode(errors='replace')[:300]}")


def main() -> int:
    try:
        data = digest(fetch())
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        if "--no-upload" not in sys.argv:
            upload(OUT)
        log(f"ok: {data['emails']} emails from {data['senders']} senders" + ("" if "--no-upload" in sys.argv else ", uploaded"))
        print(f"{data['emails']} promotional emails from {data['senders']} senders; themes {data['themes']}")
        return 0
    except Exception as exc:  # noqa: BLE001
        log(f"FAILED: {type(exc).__name__}: {exc}")
        print(f"FAILED: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
