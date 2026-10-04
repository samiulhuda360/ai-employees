"""Client Wins collector: which Fernway customers are winning, and which are at risk.

Fetches the read-only customer pulse from Fernway
(GET /api/export/customer-pulse, token-protected) and applies fixed rules so the
agent never has to guess:

  WIN      map rank improved (>= 2 positions since first scan, or >= 1 since the
           previous scan), or profile check score up >= 5
  AT RISK  no login for 21+ days, a campaign failed or paused for credits,
           auto-reload failing, map rank slipped >= 1.5 since the previous scan,
           or credits at 0 with no purchase in 45 days
  UPSELL   a win on an account tracking one business and running at most one campaign

Every flag carries the numbers that triggered it. Prints JSON.
Config in ~/agents/client-wins/.env (chmod 600): CUSTOMER_PULSE_URL, CUSTOMER_PULSE_TOKEN.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

BASE = Path.home() / "agents" / "client-wins"
if not BASE.exists():
    BASE = Path(__file__).resolve().parent
ENV_FILE = BASE / ".env"
HISTORY = BASE / "pulse-history.json"
EXCLUDE = BASE / "exclude-accounts.txt"  # internal / team accounts, one account name per line
PUSHED = BASE / "pulse.json"  # written daily by the Fernway server over a locked-down SSH key
RECENT_SCAN_DAYS = 45  # a map-rank change counts only if its latest scan is this recent
DEFAULT_URL ="https://app.fernway.example/api/export/customer-pulse?days=30"


def env() -> dict:
    values = {k: os.environ[k] for k in ("CUSTOMER_PULSE_URL", "CUSTOMER_PULSE_TOKEN") if k in os.environ}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            key, _, val = line.partition("=")
            if key.strip() and key.strip() not in values:
                values[key.strip()] = val.strip().strip("'\"")
    return values


def days_since(iso: str | None) -> int | None:
    if not iso:
        return None
    return (date.today() - date.fromisoformat(iso[:10])).days


def flags_for(acct: dict) -> dict:
    wins, risks = [], []
    for t in acct.get("rankTracking", []):
        label = f'"{t["keyword"]}" for {t["business"]}'
        first, latest = t.get("first") or {}, t.get("latest") or {}
        # Only recent scans are news; an old trend would repeat every week.
        if (days_since(latest.get("date")) or 999) > RECENT_SCAN_DAYS:
            continue
        if (t.get("rankGainSinceFirst") or 0) >= 2:
            wins.append(f"Map rank for {label} improved from {first.get('avgRank')} to {latest.get('avgRank')} "
                        f"({first.get('date')} to {latest.get('date')}, {t['scans']} scans)")
        elif (t.get("rankGainSincePrevious") or 0) >= 1:
            wins.append(f"Map rank for {label} improved {t['rankGainSincePrevious']} positions since the previous scan "
                        f"(now {latest.get('avgRank')}, {latest.get('date')})")
        if (t.get("rankGainSincePrevious") or 0) <= -1.5:
            risks.append(f"Map rank for {label} slipped {abs(t['rankGainSincePrevious'])} positions since the previous scan "
                         f"(now {latest.get('avgRank')}, {latest.get('date')})")
    for a in acct.get("audits", []):
        if a.get("latestScore") is not None and a.get("previousScore") is not None \
                and a["latestScore"] - a["previousScore"] >= 5:
            wins.append(f"Profile check score for {a['business']} rose from {a['previousScore']} to {a['latestScore']} "
                        f"({a['previousDate']} to {a['latestDate']})")

    seen = days_since(acct.get("lastSeen"))
    if seen is not None and seen >= 21:
        risks.append(f"No login for {seen} days (last seen {acct['lastSeen']})")
    for c in acct.get("campaigns", []):
        name = c.get("client") or c.get("name")
        if c["status"] == "FAILED":
            risks.append(f"Campaign '{name}' failed ({c['progress']} posted)")
        elif c["status"] == "PAUSED_NO_CREDITS":
            risks.append(f"Campaign '{name}' is paused for lack of credits ({c['progress']} sent)")
    reload = acct.get("autoReload") or {}
    if reload.get("failures"):
        risks.append(f"Auto-reload has failed {reload['failures']} time(s) (last: {reload.get('lastFailureCode')})")
    last_buy = days_since((acct.get("purchases") or {}).get("last"))
    if acct.get("credits", 0) <= 0 and last_buy is not None and last_buy >= 45:
        risks.append(f"0 credits and no purchase for {last_buy} days")

    businesses = {t["business"] for t in acct.get("rankTracking", [])}
    active = [c for c in acct.get("campaigns", []) if c["status"] not in ("COMPLETED", "FAILED")]
    upsell = []
    if wins and not risks and len(businesses) <= 1 and len(active) <= 1:
        upsell.append("Winning on a single business or campaign: offer a second keyword, city or campaign")
    return {"wins": wins, "risks": risks, "upsell": upsell}


def pushed_pulse(max_age_hours: float = 36) -> dict | None:
    """The copy the Fernway server pushes daily (Cloudflare blocks pulling from here)."""
    if not PUSHED.exists():
        return None
    age_h = (datetime.now().timestamp() - PUSHED.stat().st_mtime) / 3600
    if age_h > max_age_hours:
        return None
    try:
        return json.loads(PUSHED.read_text(encoding="utf-8"))
    except ValueError:
        return None


def main() -> None:
    cfg = env()
    out = {"generated_at": datetime.now().isoformat(timespec="minutes")}
    pulse = pushed_pulse()
    if pulse is not None:
        out["source"] = f"pushed by the Fernway server at {datetime.fromtimestamp(PUSHED.stat().st_mtime):%Y-%m-%d %H:%M}"
        summarise(pulse, out)
        return
    token = cfg.get("CUSTOMER_PULSE_TOKEN")
    if not token:
        out["error"] = ("NO_DATA: no fresh push from the Fernway app server (daily) and no token for a direct pull")
        emit(out)
        return
    req = urllib.request.Request(cfg.get("CUSTOMER_PULSE_URL") or DEFAULT_URL,
                                 headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            pulse = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        hint = {404: "the export is not deployed yet, or CUSTOMER_PULSE_TOKEN is not set on the Fernway server",
                401: "the token on the Fernway server and on Hermes do not match"}.get(exc.code, "")
        out["error"] = f"EXPORT_HTTP_{exc.code}: {hint}"
        emit(out)
        return
    except (urllib.error.URLError, OSError, ValueError) as exc:
        out["error"] = f"EXPORT_UNREACHABLE: {exc}"
        emit(out)
        return
    out["source"] = "pulled directly from the export"
    summarise(pulse, out)


def summarise(pulse: dict, out: dict) -> None:
    excluded = {line.strip().lower() for line in EXCLUDE.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.startswith("#")} if EXCLUDE.exists() else set()
    accounts = [a for a in pulse.get("accounts", []) if (a.get("account") or "").lower() not in excluded]
    rows = []
    for acct in accounts:
        f = flags_for(acct)
        paying = bool((acct.get("purchases") or {}).get("count"))
        rows.append({"account": acct.get("account"), "email": acct.get("email"), "paying": paying,
                     "last_seen": acct.get("lastSeen"),
                     "customer_since": acct.get("since"), "credits": acct.get("credits"),
                     "spent_usd": (acct.get("purchases") or {}).get("totalUsd"),
                     "campaigns": [f"{c.get('client') or c.get('name')} ({c['status']}, {c['progress']})"
                                   for c in acct.get("campaigns", [])[:5]],
                     **f})
    out.update({
        "window_days": pulse.get("windowDays"),
        "accounts_checked": len(accounts),
        "paying_accounts": sum(1 for a in accounts if (a.get("purchases") or {}).get("count")),
        "revenue_all_time_usd": round(sum((a.get("purchases") or {}).get("totalUsd") or 0 for a in accounts), 2),
        "winning": [r for r in rows if r["wins"]],
        # Churn risk only means something for customers who have paid.
        "at_risk": [r for r in rows if r["paying"] and r["risks"]],
        # Signed up and used the app but never bought: conversion leads, not churn.
        "not_yet_paying": [{k: r[k] for k in ("account", "email", "customer_since", "last_seen", "campaigns")}
                           for r in rows if not r["paying"]],
        "quiet": sum(1 for r in rows if r["paying"] and not r["wins"] and not r["risks"]),
    })
    # Keep a small weekly history so the agent can say what changed.
    try:
        history = json.loads(HISTORY.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        history = []
    today = date.today().isoformat()
    history = [h for h in history if h.get("date") != today]  # one entry per day, reruns replace it
    history.append({"date": today, "accounts": len(accounts),
                    "paying": out["paying_accounts"], "revenue_usd": out["revenue_all_time_usd"],
                    "winning": len(out["winning"]), "at_risk": len(out["at_risk"])})
    HISTORY.write_text(json.dumps(history[-26:], indent=1), encoding="utf-8")
    out["history"] = history[-6:]
    emit(out)


def emit(out: dict) -> None:
    print(render(out) if "--report" in sys.argv else json.dumps(out, indent=1))


def win_action(wins: list[str]) -> str:
    rank_wins = [w for w in wins if w.startswith("Map rank")]
    if len(wins) >= 2 and rank_wins:
        return "Ask if you can use their results as a case study"
    if rank_wins:
        return "Ask for a short testimonial or a Google review of Fernway"
    return "Offer a second keyword or city"


def risk_action(risks: list[str]) -> str:
    text = " ".join(risks)
    if "failed" in text and "Campaign" in text:
        return "Fix or restart the failed campaign with them"
    if "Auto-reload" in text:
        return "Ask them to update their card"
    if "paused for lack of credits" in text:
        return "Offer to restart the paused campaign"
    if "slipped" in text:
        return "Review the slipping keyword with them"
    if "No login" in text:
        return "Send a short personal check-in"
    return "Offer next month's plan"


def risk_draft(r: dict) -> str:
    """A short, human follow-up the owner can paste into an email. Built only from the risk facts."""
    name = first_name(r["account"])
    text = " ".join(r["risks"])
    if "Auto-reload" in text:
        return (f"Hi {name}, a quick heads-up: your Fernway auto top-up did not go through, so your campaigns "
                "may pause. Updating the card under Billing sorts it. Happy to help if anything looks off.")
    if "paused for lack of credits" in text:
        return (f"Hi {name}, your campaign is paused because the credits ran out. If you want it running again, "
                "a top-up restarts it where it stopped. Want me to check the remaining plan with you first?")
    if "failed" in text and "Campaign" in text:
        return (f"Hi {name}, one of your campaigns hit a problem on our side and stopped. I am looking into it now "
                "and will get it moving again. Sorry for the hassle.")
    if "slipped" in text:
        return (f"Hi {name}, I noticed one of your tracked keywords dipped on the last map scan. Worth a quick look "
                "together at what changed nearby, and what we can do next. Do you have ten minutes this week?")
    return (f"Hi {name}, it has been a little while since you logged in to Fernway. Is there anything I can "
            "help with, or anything that is not working the way you hoped?")


def lead_draft(r: dict) -> str:
    name = first_name(r["account"])
    if r["campaigns"]:
        return (f"Hi {name}, I saw you set up a campaign in Fernway but it has not launched yet. If it helps, "
                "I can look over the setup with you and get the first links live. What held you back?")
    return (f"Hi {name}, thanks for signing up to Fernway. If you tell me the business and city you want to "
            "rank in, I can suggest the best first campaign for it.")


def first_name(account: str) -> str:
    word = (account or "").split()[0] if account else ""
    return word[:1].upper() + word[1:] if word and "@" not in word and "*" not in word else "there"


def render(out: dict) -> str:
    if out.get("error"):
        return f"CLIENT WINS - setup needed: {out['error']}"
    lines = [f"CLIENT WINS - {date.today():%Y-%m-%d}   ({out['accounts_checked']} accounts, "
             f"{out['paying_accounts']} paying, lifetime revenue ${out['revenue_all_time_usd']:,.0f})", ""]
    if out["winning"]:
        lines.append("WINNING - thank them, ask for a testimonial or offer more")
        for r in out["winning"]:
            lines.append(f"- {r['account']}: {'; '.join(r['wins'])}")
            lines.append(f"  Do: {win_action(r['wins'])}")
            win = r["wins"][0]
            lines.append(f"  Note to send: \"Hi {first_name(r['account'])}, a quick good-news update: "
                         f"{win[0].lower() + win[1:]}. Thanks for trusting us with it.\"")
        lines.append("")
    if out["at_risk"]:
        lines.append("AT RISK - paying customers, step in this week")
        order = lambda r: (0 if any("failed" in x or "Auto-reload" in x for x in r["risks"]) else 1)
        for r in sorted(out["at_risk"], key=order):
            lines.append(f"- {r['account']}: {'; '.join(r['risks'])}")
            lines.append(f"  Do: {risk_action(r['risks'])}")
            lines.append(f"  Draft: \"{risk_draft(r)}\"")
        lines.append("")
    upsell = [r for r in out["winning"] if r.get("upsell")]
    if upsell:
        lines.append("UPSELL")
        lines += [f"- {r['account']}: {r['upsell'][0]}" for r in upsell]
        lines.append("")
    leads = sorted(out["not_yet_paying"], key=lambda r: r.get("last_seen") or "", reverse=True)[:10]
    if leads:
        lines.append(f"NOT YET PAYING - signed up, never bought ({len(out['not_yet_paying'])} total, most recent first)")
        for r in leads:
            camps = f"{len(r['campaigns'])} campaign(s) set up" if r["campaigns"] else "no campaign yet"
            lines.append(f"- {r['account']}: joined {r['customer_since']}, last seen {r['last_seen'] or 'never'}, {camps}")
            lines.append("  Do: " + ("Offer to launch the campaign they set up" if r["campaigns"]
                                     else "Offer to set up their first campaign"))
            lines.append(f"  Draft: \"{lead_draft(r)}\"")
        lines.append("")
    if not (out["winning"] or out["at_risk"]):
        lines.append("No paying customer is clearly winning or at risk this week.")
    hist = out.get("history", [])
    prev = next((h for h in reversed(hist[:-1]) if h["date"] <= (date.today() - timedelta(days=5)).isoformat()), None)
    now = hist[-1] if hist else {}
    if prev:
        lines.append(f"TREND vs {prev['date']}: paying {prev['paying']} -> {now.get('paying')}, "
                     f"winning {prev['winning']} -> {now.get('winning')}, at risk {prev['at_risk']} -> {now.get('at_risk')}")
    else:
        lines.append("TREND: first report, comparisons start next week.")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
