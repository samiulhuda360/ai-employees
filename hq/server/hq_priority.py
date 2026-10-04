"""Priority scoring for HQ ideas.

Each idea gets a 0-100 score from the signals its agent already writes (verdicts, SERP
checks, severities, fit scores, rank gaps, evidence grades), plus freshness and repeat
mentions. Every point comes with a plain-English reason so the ranking can be checked.
No model is involved; the same idea always scores the same on the same day.

Tiers: P1 (70+) do first, P2 (50-69) worth doing. Lower scores are counted, not shown.
"""

from __future__ import annotations

import re
from datetime import date

P1, P2 = 70, 50

# Points lost per day of age, by how quickly the idea goes stale.
DECAY = {
    "social": 4, "prospect": 2, "customer_risk": 2, "customer_lead": 2, "customer_win": 2,
    "saas": 1.5, "quick_win": 1.5, "cash_build": 1.2, "blog": 0.7, "feature": 0.7, "code_fix": 0.3, "learning": 0.5,
    "agent_idea": 0, "upgrade": 0, "proposal": 0.5,
}
# Each run of these re-checks everything, so only the latest run's findings are still true.
SNAPSHOT = {"code_fix", "customer_risk", "customer_lead", "customer_win"}
# Chief-assistant's page shows the ideas backlog.
AGENT_ALIAS = {"chief-assistant": "backlog"}


def _num(pattern: str, text: str) -> float | None:
    m = re.search(pattern, text or "", re.I)
    return float(m.group(1).replace(",", "")) if m else None


def _signals(i: dict) -> tuple[float, list[str]]:
    t = i["type"]
    title, summ, ev, det = i["title"] or "", i["summary"] or "", i["evidence"] or "", i.get("detail") or ""
    text = f"{title}\n{summ}\n{ev}\n{det}"
    s, why = 0.0, []

    def add(pts: float, reason: str) -> None:
        nonlocal s
        s += pts
        why.append(reason)

    if t == "blog":
        add(35, "blog idea")
        if "SERP-checked" in ev:
            add(25, "top 10 checked and beatable")
        if summ.startswith("Tier A"):
            add(20, "Tier A")
        elif summ.startswith("Tier B"):
            add(5, "Tier B (quick lead)")
        if re.search(r"\bWEAK\b", det):
            add(10, "weak sites ranking now")
    elif t == "social":
        add(40, "post idea")
        if "Blog Planner" in ev:
            add(15, "pairs with a planned blog post")
        if "Growth Scout" in ev or "Business Profile Help" in ev:
            add(10, "backed by an official source")
        if "Local Search Forum" in ev or "YouTube Watcher" in ev:
            add(8, "live community topic")
    elif t == "feature":
        verdict = (re.search(r"verdict (\w+)", ev) or re.search(r"^(\w+):", summ))
        v = verdict.group(1).upper() if verdict else ""
        pts = {"BUILD": 85, "EXPERIMENT": 62, "WATCH": 25}.get(v, 40)
        add(pts, f"Growth Scout says {v or 'no verdict'}")
    elif t == "quick_win":
        add(45, "Growth Scout quick win")
        if title.lower().startswith("why not"):
            add(-25, "a note on what not to do")
    elif t == "saas":
        add(30, "SaaS opportunity")
        if "Best bet today" in det:
            add(30, "scout's best bet that day")
        if re.search(r"payment-provider-verified|verified", summ, re.I):
            add(12, "revenue verified")
        mrr = _num(r"(?:EUR|USD|\$)\s?([\d,]+)\s*MRR", summ)
        if mrr and mrr >= 1000:
            add(8, f"about {int(mrr):,} MRR proven")
    elif t == "cash_build":
        add(40, "cash build")
        days = _num(r"build:\s*([\d.]+)\s*days?", summ)
        if days is not None:
            add(max(0, 25 - int(days * 5)), f"{days:g} day{'s' if days != 1 else ''} to build")
        sc = _num(r"score\s*([\d.]+)\s*/", summ)
        if sc is not None:
            add(min(20, sc * 0.6), f"scout score {sc:g}/32.5")
        proof = re.search(r"proof:\s*(.*?)\s*-\s*sell via", summ, re.I)
        if proof and re.search(r"paid|pays|paying|USD|\$|customers|sales|MRR", proof.group(1), re.I):
            add(12, "people already pay for this")
        if "weekly pick" in summ:
            add(15, "this week's pick")
        if "existing asset" in ev:
            add(10, "you already own it")
        if "#1" in ev:
            add(5, "top pick of the day")
    elif t == "proposal":
        add(97, "proposed change to how this agent works")
    elif t == "prospect":
        add(30, "prospect")
        rank = _num(r"#(\d+)", title)
        if rank is not None:
            if rank <= 6:
                add(25, f"#{int(rank)} on Maps, close to the top 3")
            elif rank <= 10:
                add(15, f"#{int(rank)} on Maps")
            else:
                add(5, f"#{int(rank)} on Maps, far off")
        gaps = [g for g in re.split(r";\s*", summ) if g and "outside the top-3" not in g]
        if gaps:
            add(min(24, 12 * len(gaps)), f"{len(gaps)} fixable gap{'s' if len(gaps) > 1 else ''}")
    elif t == "customer_risk":
        add(60, "customer at risk")
        idle = _num(r"No login for (\d+) days", summ)
        if idle:
            add(15 if idle >= 30 else 8, f"no login for {int(idle)} days")
        if re.search(r"\b0 credits\b", summ):
            add(12, "out of credits")
        slip = _num(r"slipped ([\d.]+) positions", summ)
        if slip:
            add(min(15, slip * 6), f"rank slipped {slip:g}")
    elif t == "customer_lead":
        add(52, "upgrade lead")
        camps = _num(r"(\d+) campaign", summ)
        if camps:
            add(min(15, camps * 5), f"{int(camps)} campaign{'s' if camps > 1 else ''} set up")
    elif t == "customer_win":
        add(38, "customer win to celebrate")
    elif t == "code_fix":
        sev = re.search(r"\b(CRITICAL|HIGH|MEDIUM|MODERATE|LOW)\b", title)
        v = sev.group(1) if sev else ""
        pts = {"CRITICAL": 92, "HIGH": 72, "MEDIUM": 52, "MODERATE": 52, "LOW": 30}.get(v, 45)
        add(pts, f"{v.lower()} severity" if v else "repo hygiene")
        n = _num(r"(\d+) uncommitted", title)
        if n:
            add(min(12, n / 2), f"{int(n)} uncommitted files")
        if re.search(r"fernway", title, re.I):
            add(10, "Fernway code")
    elif t == "learning":
        grade = next((g for g in ("VERIFIED", "DEMONSTRATED", "CLAIMED") if g in ev), "")
        add({"VERIFIED": 60, "DEMONSTRATED": 55, "CLAIMED": 30}.get(grade, 35), f"evidence {grade.lower() or 'ungraded'}")
        if "applies to" in ev:
            add(10, "applies to your fleet")
    elif t in ("agent_idea", "upgrade"):
        add(40, "backlog idea")
        if re.search(r"\|\s*NEXT\s*\|", det):
            add(25, "marked NEXT")
        if i.get("status") == "approved":
            add(25, "you approved it")
    else:
        add(40, t)

    if i.get("status") == "approved" and t not in ("agent_idea", "upgrade"):
        add(15, "you approved it")
    return s, why


def score(i: dict, repeats: int = 1, today: date | None = None) -> dict:
    s, why = _signals(i)
    today = today or date.today()
    try:
        age = max(0, (today - date.fromisoformat(i["date"][:10])).days)
    except (TypeError, ValueError):
        age = 0
    rate = DECAY.get(i["type"], 1)
    if age and rate:
        s -= age * rate
        why.append(f"{age} day{'s' if age > 1 else ''} old")
    elif not age:
        why.append("new today")
        if i["type"] == "social":
            s += 10  # a post idea is worth most on the day it is written
    if repeats > 1:
        s += min(15, 5 * (repeats - 1))
        why.append(f"raised {repeats} times")
    s = max(0, min(100, round(s)))
    tier = "P1" if s >= P1 else "P2" if s >= P2 else "P3"
    return {**{k: i[k] for k in ("id", "agent", "date", "type", "title", "summary", "status", "source_url")},
            "score": s, "tier": tier, "why": why}


def _key(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()[:80]


def rank(ideas: list[dict], today: date | None = None) -> list[dict]:
    """Score open ideas, merging repeats of the same title (keeps the newest)."""
    latest: dict[str, str] = {}
    for i in ideas:
        if i["type"] in SNAPSHOT:
            k = f"{i['agent']}|{i['type']}"
            latest[k] = max(latest.get(k, ""), i["date"] or "")
    groups: dict[str, list[dict]] = {}
    for i in ideas:
        if i["type"] in SNAPSHOT and (i["date"] or "") < latest[f"{i['agent']}|{i['type']}"]:
            continue
        if i.get("status") in ("done", "rejected", "parked"):
            continue
        groups.setdefault(f"{i['type']}|{_key(i['title'])}", []).append(i)
    out = []
    for same in groups.values():
        newest = max(same, key=lambda x: (x["date"] or "", x["id"]))
        out.append(score(newest, repeats=len({x["date"] for x in same}), today=today))
    return sorted(out, key=lambda x: (x["score"], x["date"]), reverse=True)
