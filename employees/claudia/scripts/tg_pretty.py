"""Turn the agents' plain-text reports into short, readable Telegram messages.

The reports themselves stay as they are (HQ parses them). This only reshapes what
Claudia relays: a clear title, the best few items, one or two lines each, and a pointer
to HQ for the rest. Nothing is invented: every word comes from the report. If a report
does not look the way a formatter expects, the original text is sent unchanged.
"""

from __future__ import annotations

import re
from datetime import datetime

HQ = "https://hq.example.com"


def nice_date(iso: str) -> str:
    try:
        d = datetime.strptime(iso, "%Y-%m-%d")
        return f"{d:%a} {d.day} {d:%b}"
    except ValueError:
        return iso


def first_sentence(text: str, limit: int = 170) -> str:
    text = " ".join(text.split())
    m = re.match(r"(.+?[.!?])(\s|$)", text)
    s = m.group(1) if m and len(m.group(1)) >= 40 else text
    return s if len(s) <= limit else s[: limit - 1].rsplit(" ", 1)[0] + "…"


def split_notes(text: str) -> tuple[list[str], str]:
    """Leading [relay]/[unchecked]/[backup model] lines are warnings: keep them on top."""
    notes, lines = [], text.strip().splitlines()
    while lines and (lines[0].startswith("[") or not lines[0].strip()):
        if lines[0].strip():
            notes.append("⚠️ " + lines[0].strip().strip("[]"))
        lines.pop(0)
    return notes, "\n".join(lines)


def blocks(section: str) -> list[list[str]]:
    """Numbered items ("1. ...") with their indented lines."""
    out: list[list[str]] = []
    for ln in section.splitlines():
        if re.match(r"^\d+\.\s", ln):
            out.append([re.sub(r"^\d+\.\s+", "", ln).strip()])
        elif out and ln.strip():
            out[-1].append(ln.strip())
    return out


def field(block: list[str], name: str) -> str:
    for ln in block[1:]:
        if ln.lower().startswith(name.lower() + ":"):
            return ln.split(":", 1)[1].strip()
    return ""


def domain(url: str) -> str:
    return re.sub(r"^https?://(www\.)?", "", url).split("/")[0]


# ------------------------------------------------------------------ prospects

def prospects(text: str) -> str:
    m = re.search(r"PROSPECTS - (\S+)\s+\((\d+) businesses", text)
    items = blocks(text.split("\n", 2)[-1].split("\nNext:")[0])
    if not m or not items:
        raise ValueError("unexpected prospects report")
    out = [f"🎯 **{m.group(2)} new prospects** · {nice_date(m.group(1))}", ""]
    for n, b in enumerate(items, 1):
        h = re.match(r"(.+?) - (.+?)\s{2,}Maps #(\d+) for \"(.+?)\"", b[0])
        name, where, rank = (h.group(1), h.group(2), h.group(3)) if h else (b[0], "", "")
        gaps = []
        for g in field(b, "Gaps").split(";"):
            g = g.strip()
            if not g or "outside the top-3" in g:
                continue
            g = re.sub(r"Profile is unclaimed on Google", "profile is unclaimed", g)
            g = re.sub(r"(\S+) reviews? vs a median of (\d+) for the top 3", r"\1 reviews (top 3 have ~\2)", g)
            g = re.sub(r"only (\d+) photos? on the profile", lambda x: f"{x.group(1)} photo{'s' if x.group(1) != '1' else ''}", g)
            g = re.sub(r"(\S+) star rating \(top 3 average (\S+)\)", r"\1★ (top 3 average \2★)", g)
            gaps.append(g)
        contact = [c.strip() for c in field(b, "Contact").split("|") if c.strip() and "no public" not in c]
        contact = [domain(c) if c.startswith("http") else c for c in contact]
        head = f"**{n}. {name}**" + (f" — {where}, Maps #{rank}" if where else "")
        if n <= 3:
            out += [head, "Gaps: " + " · ".join(gaps), " · ".join(contact)]
            opener = field(b, "Opener").strip('"')
            if opener:
                out.append(f"Opener: “{opener}”")
            out.append("")
        else:
            out.append(head + (f" · {contact[0]}" if contact else ""))
    out += ["", f"Mark who you contact in HQ: {HQ}/prospects"]
    return "\n".join(out)


# ------------------------------------------------------------------ blog ideas

def blog(text: str) -> str:
    m = re.search(r"BLOG IDEAS - (\S+)", text)
    if not m or "TIER A" not in text:
        raise ValueError("unexpected blog report")
    a_part = text.split("TIER A", 1)[1].split("TIER B")[0].split("SKIPPED")[0]
    b_part = text.split("TIER B", 1)[1].split("SKIPPED")[0] if "TIER B" in text else ""
    tier_a, tier_b = blocks(a_part), blocks(b_part)
    out = [f"✍️ **Blog ideas** · {nice_date(m.group(1))}",
           f"{len(tier_a)} checked and winnable, {len(tier_b)} quick leads", ""]
    for n, b in enumerate(tier_a[:3], 1):
        query = field(b, "Query").split("|")[0].strip()
        why = field(b, "Why it can win")
        beat = re.search(r"displaces (\S+) \(DR (\d+), (\w+)\)", why)
        out.append(f"**{n}. {b[0]}**")
        out.append(f"Search: “{query}”" + (f" · can beat {domain(beat.group(1))} (DR {beat.group(2)})" if beat else ""))
        angle = field(b, "Angle")
        if angle:
            out.append(first_sentence(angle))
        out.append("")
    if len(tier_a) > 3:
        out += [f"+ {len(tier_a) - 3} more checked ideas", ""]
    if tier_b:
        out.append("**Quick leads** (not checked yet)")
        for b in tier_b[:4]:
            out.append("• " + re.split(r"\s+-\s+query:", b[0])[0].strip())
        if len(tier_b) > 4:
            out.append(f"• and {len(tier_b) - 4} more")
        out.append("")
    out.append(f"Outlines and the ranked list: {HQ}/write")
    return "\n".join(out)


# ------------------------------------------------------------------ social ideas

PLATFORM = {"FACEBOOK PAGE": "Facebook Page", "FACEBOOK GROUPS": "Facebook Groups", "LINKEDIN": "LinkedIn", "X (TWITTER)": "X"}


def social(text: str) -> str:
    m = re.search(r"SOCIAL IDEAS - (\S+)\s+\(theme of the day: (.+?)\)?\s*$", text, re.M)
    if not m:
        raise ValueError("unexpected social report")
    out = [f"📣 **Social ideas** · {nice_date(m.group(1))}", f"Theme: {m.group(2).rstrip(')')}", ""]
    parts = re.split(r"^(" + "|".join(re.escape(k) for k in PLATFORM) + r")\s*$", text, flags=re.M)
    found = 0
    for i in range(1, len(parts) - 1, 2):
        items = blocks(parts[i + 1])
        if not items:
            continue
        found += 1
        out.append(f"**{PLATFORM[parts[i]]}**")
        for b in items[:2]:
            if b[0].lower().startswith("hook:"):
                line = b[0].split(":", 1)[1].strip().strip('"')
                fmt = field(b, "Format")
                out.append(f"• “{line}”" + (f" ({fmt})" if fmt else ""))
            else:  # X: "Thread: 3 parts" followed by "- post" lines
                posts = [ln.lstrip("- ").strip() for ln in b[1:] if ln.startswith("-")]
                if posts:
                    kind = "Thread" if b[0].lower().startswith("thread") else "Post"
                    out.append(f"• {kind}: “{posts[0]}”")
        if len(items) > 2:
            out.append(f"  + {len(items) - 2} more")
        out.append("")
    if not found:
        raise ValueError("no platforms found")
    out.append(f"Angles, sources and every post: {HQ}/agents/social-planner#ideas")
    return "\n".join(out)


# ------------------------------------------------------------------ generic (weekly reports)

ICON = {"CLIENT WINS": "🤝", "CODE HEALTH": "🛠️", "FLEET GUARDIAN": "🚨"}


def generic(text: str) -> str:
    lines = text.strip().splitlines()
    m = re.match(r"([A-Z][A-Z ]+?) - (\d{4}-\d\d-\d\d)\s*(.*)", lines[0])
    if not m:
        raise ValueError("no title")
    title = m.group(1).title()
    out = [f"{ICON.get(m.group(1), '📋')} **{title}** · {nice_date(m.group(2))}"]
    if m.group(3).strip():
        out.append(m.group(3).strip().strip("()"))
    for ln in lines[1:]:
        h = re.match(r"^([A-Z][A-Z ]{3,})( - .+|:.*)?$", ln)
        if h:
            rest = (h.group(2) or "").lstrip(" -:").strip()
            out.append(f"**{h.group(1).capitalize()}**" + (f" — {rest}" if rest else ""))
        else:
            out.append(re.sub(r"^- ", "• ", ln))
    return "\n".join(out)


FORMATTERS = [("PROSPECTS - ", prospects), ("BLOG IDEAS - ", blog), ("SOCIAL IDEAS - ", social)]


def pretty(text: str) -> str:
    """Best-effort: a nicer message, or the original text if anything looks off."""
    if not text or not text.strip() or text.lstrip().startswith("[relay]") and "\n" not in text.strip():
        return text
    try:
        notes, body = split_notes(text)
        fn = next((f for key, f in FORMATTERS if key in body[:200]), generic)
        msg = fn(body)
        if len(msg) < 40:
            return text
        return "\n".join(notes + ([""] if notes else []) + [msg])
    except Exception:  # noqa: BLE001  never lose a report over formatting
        return text
