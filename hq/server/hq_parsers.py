"""Turn each agent's report into idea rows for Hermes HQ.

Pure functions, no model. Each parser knows the structure one agent writes
(headings, numbered lists, "Best bet today:" lines) and copies text exactly.
When a report does not match, it yields nothing; the full report is still stored
in the runs table and can be re-parsed later.

Idea types: saas, feature, quick_win, blog, social, prospect, customer_win,
customer_risk, customer_lead, job, code_fix, agent_idea, upgrade.
"""

from __future__ import annotations

import re
from typing import Iterator

URL_RE = re.compile(r"https?://[^\s)\]>\"']+")
NUM_ITEM_RE = re.compile(r"^\s*(\d+)\.\s+(.+)$")


def first_url(text: str) -> str | None:
    m = URL_RE.search(text or "")
    return m.group(0).rstrip(".,;") if m else None


def clean(text: str) -> str:
    text = re.sub(r"\*\*|__|`", "", text or "")
    return re.sub(r"\s+", " ", text).strip(" -:")


def numbered_blocks(lines: list[str]) -> Iterator[tuple[str, list[str]]]:
    """Yield (first line, continuation lines) for each '1. ...' item."""
    head, rest = None, []
    for ln in lines:
        m = NUM_ITEM_RE.match(ln)
        if m:
            if head is not None:
                yield head, rest
            head, rest = m.group(2), []
        elif head is not None:
            if ln.strip() == "" and rest and rest[-1].strip() == "":
                continue
            if re.match(r"^[A-Z][A-Z /()&-]{3,}$", ln.strip()):  # next section heading
                yield head, rest
                head, rest = None, []
                continue
            rest.append(ln)
    if head is not None:
        yield head, rest


def sections(body: str, names: list[str]) -> dict[str, list[str]]:
    """Split a report into its ALL-CAPS sections (TIER A, FACEBOOK PAGE, ...)."""
    out: dict[str, list[str]] = {}
    current = None
    for ln in body.splitlines():
        s = ln.strip()
        hit = next((n for n in names if s.upper().startswith(n)), None)
        if hit:
            current = hit
            out.setdefault(current, [])
            continue
        if current:
            out[current].append(ln)
    return out


# ------------------------------------------------------------------ per agent

CASH_ITEM = re.compile(r"^\s*(\d)\.\s+(.+?)\s+-\s+build:\s*([\d.]+)\s*days?\s*-\s*(.*)$", re.M)


def cash_builds(job: str, body: str) -> Iterator[dict]:
    """Cash Builds daily list (2026-09-28 format) and the Monday 'build this one'."""
    if "CASH BUILDS" in body:
        text = body[body.index("CASH BUILDS"):]
        blocks = re.split(r"^(?=\s*\d\.\s)", text, flags=re.M)
        for blk in blocks:
            m = CASH_ITEM.search(blk)
            if not m:
                continue
            rank, name, days, rest = m.group(1), clean(m.group(2)), m.group(3), clean(m.group(4))
            ask = re.search(r"Ask:\s*(.+)", blk)
            yield {"type": "cash_build", "title": name, "summary": f"build: {days} days - {rest}",
                   "detail": blk.strip()[:3000], "source_url": first_url(blk),
                   "evidence": f"Cash Builds #{rank}" + (f" - {clean(ask.group(1))[:200]}" if ask else "")}
        sell = re.search(r"SELL WHAT YOU HAVE:\s*(.+)", text)
        if sell:
            line = clean(sell.group(1))
            yield {"type": "cash_build", "title": "Sell what you have: " + line.split(" - ")[0], "summary": line,
                   "detail": line, "source_url": None, "evidence": "Cash Builds - existing asset"}
    m = re.search(r"BUILD THIS ONE\s*-\s*[\d-]+\s*-\s*(.+?)\s*-\s*([\d.]+)\s*days?\s*-\s*(USD\s*[\d.]+)", body)
    if m:
        yield {"type": "cash_build", "title": "Build this week: " + clean(m.group(1)), "summary": f"build: {m.group(2)} days - {m.group(3)} - weekly pick",
               "detail": body[:6000], "source_url": first_url(body), "evidence": "Cash Builds weekly pick"}


def opportunity(job: str, body: str) -> Iterator[dict]:
    if "CASH BUILDS" in body or "BUILD THIS ONE" in body:
        yield from cash_builds(job, body)
        return
    m = re.search(r"Best bet today:\s*(.+)", body)
    if m:
        line = clean(m.group(1))
        title = line.split(" - ")[0]
        yield {"type": "saas", "title": title, "summary": line, "detail": body[:6000],
               "source_url": first_url(body), "evidence": "Opportunity Scout daily radar"}
    m = re.search(r"^#+\s*(?:Weekly )?(?:build )?thesis[:\s]*(.+)$", body, re.I | re.M)
    if job.lower().startswith("weekly") and m:
        yield {"type": "saas", "title": clean(m.group(1)), "summary": clean(m.group(1)),
               "detail": body[:6000], "source_url": first_url(body), "evidence": "Weekly build thesis"}


def growth(job: str, body: str) -> Iterator[dict]:
    m = re.search(r"Decision today:\s*\**\s*([A-Z]+)\s*[-:–—]\s*(.+)", body)
    if m:
        verdict, title = m.group(1), clean(m.group(2))
        what = re.search(r"### What it is\s+(.+?)(?:\n\n|\n###)", body, re.S)
        yield {"type": "feature", "title": title, "summary": f"{verdict}: {clean(what.group(1)) if what else title}",
               "detail": body[:6000], "source_url": first_url(body), "evidence": f"Growth Scout verdict {verdict}"}
    q = re.search(r"quick[- ]win[^\n]*\n+(?:[-*]\s*)?(.+)", body, re.I)
    if q:
        yield {"type": "quick_win", "title": clean(q.group(1))[:160], "summary": clean(q.group(1)),
               "detail": "", "source_url": None, "evidence": "Growth Scout quick-win lane"}


def blog(job: str, body: str) -> Iterator[dict]:
    if "BLOG IDEAS" not in body:
        return
    body = body[body.index("BLOG IDEAS"):]
    secs = sections(body, ["TIER A", "TIER B", "SKIPPED"])
    for tier in ("TIER A", "TIER B"):
        for head, rest in numbered_blocks(secs.get(tier, [])):
            text = "\n".join(rest)
            q = re.search(r"[Qq]uery:\s*([^|\n]+)", head + "\n" + text)
            title = clean(head.split(" - query:")[0])
            yield {"type": "blog", "title": title,
                   "summary": f"{tier.title()}. Query: {clean(q.group(1)) if q else ''}".strip(),
                   "detail": (head + "\n" + text).strip(), "source_url": first_url(text),
                   "evidence": "SERP-checked, winnable" if tier == "TIER A" else "Quick lead, not SERP-checked"}


SOCIAL_SECTIONS = ["FACEBOOK PAGE", "FACEBOOK GROUPS", "LINKEDIN", "X (TWITTER)", "X"]


def social(job: str, body: str) -> Iterator[dict]:
    if "SOCIAL IDEAS" not in body:
        return
    body = body[body.index("SOCIAL IDEAS"):]
    secs = sections(body, SOCIAL_SECTIONS + ["BEST TIMES"])
    for platform in SOCIAL_SECTIONS:
        for head, rest in numbered_blocks(secs.get(platform, [])):
            text = "\n".join(rest)
            hook = re.search(r'Hook:\s*"?(.+?)"?\s*$', head) or re.search(r'Hook:\s*"?(.+?)"?\s*$', text, re.M)
            title = clean(hook.group(1)) if hook else clean(head)
            angle = re.search(r"Angle:\s*(.+)", text)
            src = re.search(r"Source:\s*(.+)", text)
            yield {"type": "social", "title": f"{platform.title()}: {title}"[:200],
                   "summary": clean(angle.group(1)) if angle else "", "detail": (head + "\n" + text).strip(),
                   "source_url": first_url(text), "evidence": clean(src.group(1)) if src else ""}


def prospects(job: str, body: str) -> Iterator[dict]:
    if "PROSPECTS" not in body or "setup needed" in body:
        return
    for head, rest in numbered_blocks(body.splitlines()):
        text = "\n".join(rest)
        gaps = re.search(r"Gaps:\s*(.+)", text)
        yield {"type": "prospect", "title": clean(head), "summary": clean(gaps.group(1)) if gaps else "",
               "detail": (head + "\n" + text).strip(), "source_url": first_url(text), "evidence": "DataForSEO Maps"}


def client_wins(job: str, body: str) -> Iterator[dict]:
    if "CLIENT WINS" not in body or "setup needed" in body:
        return
    kind = None
    for ln in body.splitlines():
        s = ln.strip()
        if s.startswith("WINNING"):
            kind = "customer_win"
        elif s.startswith("AT RISK"):
            kind = "customer_risk"
        elif s.startswith("NOT YET PAYING"):
            kind = "customer_lead"
        elif s.startswith(("UPSELL", "TREND")):
            kind = None
        elif kind and s.startswith("- ") and ":" in s:
            account, what = s[2:].split(":", 1)
            yield {"type": kind, "title": clean(account), "summary": clean(what)[:600], "detail": s,
                   "source_url": None, "evidence": "Fernway customer pulse"}


def code_health(job: str, body: str) -> Iterator[dict]:
    if "CODE HEALTH" not in body:
        return
    body = body[body.index("CODE HEALTH"):]
    secs = sections(body, ["FIX THIS WEEK", "LATER", "CHANGED SINCE"])
    for head, rest in numbered_blocks(secs.get("FIX THIS WEEK", [])):
        yield {"type": "code_fix", "title": clean(head), "summary": clean(" ".join(rest))[:600],
               "detail": "\n".join([head] + rest).strip(), "source_url": None, "evidence": "OSV.dev / repo scan"}


PROPOSAL = re.compile(r"^\s*\d+\.\s*\[([a-z-]+)\]\s*RULE:\s*(.+?)\s*\n\s*Why:\s*(.+?)\s*(?=\n\s*\d+\.\s*\[|\n\s*\n|\Z)", re.M | re.S)


def chief(job: str, body: str) -> Iterator[dict]:
    """Claudia's weekly tune-up: each proposed rule becomes a 'proposal' on the agent it is for."""
    if "TUNE-UP PROPOSALS" not in body:
        return
    for m in PROPOSAL.finditer(body[body.index("TUNE-UP PROPOSALS"):]):
        rule = clean(m.group(2))
        yield {"agent": m.group(1), "type": "proposal", "title": rule[:200], "summary": "Why: " + clean(m.group(3)),
               "detail": f"Proposed rule for {m.group(1)}:\n\n{rule}\n\nWhy: {clean(m.group(3))}\n\n"
                         "Approve to add it to this agent's instructions; reject or park to leave them as they are.",
               "source_url": None, "evidence": "Weekly agent tune-up"}


PARSERS = {
    "chief-assistant": chief,
    "opportunity-scout": opportunity,
    "growth-scout": growth,
    "blog-planner": blog,
    "social-planner": social,
    "prospect-finder": prospects,
    "client-wins": client_wins,
    "code-health": code_health,
}


def extract(agent: str, job: str, body: str) -> list[dict]:
    parser = PARSERS.get(agent)
    if not parser or not body:
        return []
    try:
        return [i for i in parser(job, body) if i.get("title")]
    except Exception:  # a parser bug must never stop ingest
        return []


# ------------------------------------------------------------------ YouTube Watcher knowledge base

def extract_knowledge(text: str) -> Iterator[dict]:
    """Each '### title' entry with a '- date:' line becomes a learning card."""
    for m in re.finditer(r"^### (.+?)\n(.*?)(?=^### |^## |\Z)", text, re.S | re.M):
        title, block = m.group(1).strip(), m.group(2)
        if title.startswith("<"):
            continue  # the format template
        dm = re.search(r"- date:\s*(\d{4}-\d{2}-\d{2})(?:\s+lane:\s*(\S+))?", block)
        if not dm:
            continue
        field = lambda k: re.search(rf"^- {k}:\s*(.+?)(?=^- |\Z)", block, re.S | re.M)
        what, applies, evidence, source = field("what"), field("applies to"), field("evidence"), field("source")
        yield {
            "date": dm.group(1), "type": "learning", "title": clean(title),
            "summary": clean(what.group(1)) if what else "",
            "detail": block.strip(),
            "source_url": first_url(source.group(1)) if source else None,
            "evidence": " · ".join(x for x in [
                f"lane {dm.group(2)}" if dm.group(2) else "",
                clean(evidence.group(1)) if evidence else "",
                ("for: " + clean(field("for").group(1))[:80]) if field("for") else "",
                ("applies to: " + clean(applies.group(1)))[:160] if applies else ""] if x),
        }


# ------------------------------------------------------------------ backlog

STATUS_MAP = {"BUILT": "done", "NEXT": "approved", "PROPOSED": "new", "BLOCKED": "parked", "SKIP": "rejected"}


def extract_backlog(text: str) -> Iterator[dict]:
    date = re.search(r"Last updated:\s*(\d{4}-\d{2}-\d{2})", text)
    date = date.group(1) if date else "2026-09-26"
    # Section 3: "### 3.4 Renewals & money watcher (`NEXT`)"
    for m in re.finditer(r"^### 3\.\d+ (.+?) \(`([A-Z]+)`[^)]*\)\s*\n(.*?)(?=^### |^## |\Z)", text, re.S | re.M):
        title, status, block = m.group(1), m.group(2), m.group(3)
        what = re.search(r"\*\*What:\*\*\s*(.+?)(?:\n- \*\*|\Z)", block, re.S)
        src = re.search(r"\*\*Sources?:\*\*\s*(.+?)(?:\n- \*\*|\Z)", block, re.S)
        yield {"date": date, "type": "agent_idea", "title": clean(title), "status": STATUS_MAP.get(status, "new"),
               "summary": clean(what.group(1)) if what else "", "detail": block.strip(),
               "evidence": clean(src.group(1)) if src else "ideas backlog"}
    # Section 4: upgrade table rows "| Upgrade | Agent | Source | Status |"
    sec4 = re.search(r"^## 4\..*?(?=^## 5\.)", text, re.S | re.M)
    if sec4:
        for row in sec4.group(0).splitlines():
            cells = [c.strip() for c in row.strip().strip("|").split("|")]
            if len(cells) == 4 and cells[0] not in ("Upgrade", "---") and not set(cells[0]) <= {"-"}:
                status = re.match(r"[A-Z]+", cells[3])
                yield {"date": date, "type": "upgrade", "title": clean(cells[0])[:200],
                       "status": STATUS_MAP.get(status.group(0) if status else "", "new"),
                       "summary": f"For {cells[1]}", "detail": row, "evidence": cells[2]}
