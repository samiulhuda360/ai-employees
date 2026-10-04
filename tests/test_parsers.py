"""Each agent's report format -> idea rows. The parsers copy text; they never invent it."""

from __future__ import annotations

import hq_ingest
import hq_parsers
from hq_parsers import extract, extract_backlog, extract_knowledge


def test_blog_tiers():
    body = """BLOG IDEAS

TIER A (checked against the search results, winnable)
1. How to ask customers for Google reviews - query: how to ask for google reviews
   Why winnable: forum threads rank. https://example.com/serp/1
2. Why your Google review disappeared - query: google review disappeared

TIER B (quick leads, not checked yet)
1. QR codes for reviews: do they work? - query: qr code for google reviews

SKIPPED
- One idea already covered."""
    ideas = extract("blog-planner", "Daily blog ideas", body)
    assert [i["title"] for i in ideas] == ["How to ask customers for Google reviews", "Why your Google review disappeared",
                                           "QR codes for reviews: do they work?"]
    assert ideas[0]["summary"] == "Tier A. Query: how to ask for google reviews"
    assert ideas[0]["source_url"] == "https://example.com/serp/1"
    assert ideas[0]["evidence"] == "SERP-checked, winnable"
    assert ideas[2]["evidence"] == "Quick lead, not SERP-checked"


def test_social_platforms():
    body = """SOCIAL IDEAS

FACEBOOK PAGE
1. Hook: "Three review replies we would never send"
   Angle: show the fixed versions
   Source: https://example.com/post/1

LINKEDIN
1. Hook: "What 1,000 review requests taught us"
   Angle: first-party timing data

X (TWITTER)
1. Hook: "Local SEO is mostly boring consistency"
"""
    ideas = extract("social-planner", "Daily social ideas", body)
    assert [i["title"] for i in ideas] == ["Facebook Page: Three review replies we would never send",
                                           "Linkedin: What 1,000 review requests taught us",
                                           "X (Twitter): Local SEO is mostly boring consistency"]
    assert ideas[0]["summary"] == "show the fixed versions"


def test_cash_builds_and_sell_what_you_have():
    body = """CASH BUILDS

1. Review reply template pack - build: 2 days - price: USD 19 one-off - sell via: Gumroad - why: owners ask
   Ask: would you pay for this today?
2. Holiday hours bulk updater - build: 4 days - price: USD 29 lifetime - sell via: Gumroad - why: one by one

SELL WHAT YOU HAVE: Fernway profile check - sell a branded PDF to agencies"""
    ideas = extract("opportunity-scout", "Daily cash builds", body)
    assert [i["type"] for i in ideas] == ["cash_build"] * 3
    assert ideas[0]["title"] == "Review reply template pack"
    assert ideas[0]["summary"].startswith("build: 2 days - price: USD 19 one-off")
    assert ideas[0]["evidence"] == "Cash Builds #1 - would you pay for this today?"
    assert ideas[2]["title"] == "Sell what you have: Fernway profile check"


def test_growth_verdict_and_quick_win():
    body = """Decision today: EXPERIMENT - Competitor review tracker

### What it is
Track three nearby competitors.

Quick win this week
- Add a copy review link button"""
    feature, quick = extract("growth-scout", "Daily Fernway growth radar", body)
    assert (feature["type"], feature["title"], feature["summary"]) == (
        "feature", "Competitor review tracker", "EXPERIMENT: Track three nearby competitors.")
    assert (quick["type"], quick["title"]) == ("quick_win", "Add a copy review link button")


def test_prospects_skip_setup_messages():
    body = """PROSPECTS - today

1. Harbourline Dental - dentist, Auckland   Maps #5 for "dentist auckland"
   Gaps: Few photos; 6 unanswered reviews"""
    (p,) = extract("prospect-finder", "Daily prospects", body)
    assert p["type"] == "prospect" and p["summary"] == "Few photos; 6 unanswered reviews"
    assert extract("prospect-finder", "Daily prospects", "PROSPECTS - setup needed: add credentials") == []


def test_client_wins_sections():
    body = """CLIENT WINS

WINNING
- Coastline Dental: rating up 4.3 to 4.6
AT RISK
- Ironbark Builders: No login for 34 days
NOT YET PAYING
- Westgate Auto: 3 campaigns set up
UPSELL
- Coastline Dental: four locations on Starter"""
    ideas = extract("client-wins", "Weekly client wins", body)
    assert [(i["type"], i["title"]) for i in ideas] == [
        ("customer_win", "Coastline Dental"), ("customer_risk", "Ironbark Builders"), ("customer_lead", "Westgate Auto")]


def test_code_health_only_fix_this_week():
    body = """CODE HEALTH

FIX THIS WEEK
1. fernway-app: HIGH advisory in the image library
   Upgrade to the patched release.

LATER
- Node 20 end of life"""
    (fix,) = extract("code-health", "Weekly code health", body)
    assert fix["title"] == "fernway-app: HIGH advisory in the image library"
    assert fix["summary"] == "Upgrade to the patched release."


def test_tuneup_proposals_belong_to_the_agent_they_change():
    body = """TUNE-UP PROPOSALS

1. [blog-planner] RULE: Skip listicles.
   Why: four rejected.

2. [social-planner] RULE: Lead with first-party numbers.
   Why: approved ideas used them.
"""
    ideas = extract("chief-assistant", "Weekly agent tune-up", body)
    assert [(i["agent"], i["type"], i["title"]) for i in ideas] == [
        ("blog-planner", "proposal", "Skip listicles."), ("social-planner", "proposal", "Lead with first-party numbers.")]


def test_parser_failure_never_raises(monkeypatch):
    def boom(job, body):
        raise ValueError("bad format")
        yield  # pragma: no cover
    monkeypatch.setitem(hq_parsers.PARSERS, "blog-planner", boom)
    assert extract("blog-planner", "Daily blog ideas", "BLOG IDEAS") == []
    assert extract("unknown-agent", "x", "anything") == []


def test_knowledge_base_entries():
    kb = """### <title>
- date: 2026-01-01

### Ask for the review in the same visit
- date: 2026-10-01 lane: local_seo
- what: Ask in person and hand over a link.
- for: blog-planner
- evidence: DEMONSTRATED
- source: https://example.com/video/1
"""
    (e,) = extract_knowledge(kb)
    assert e["title"] == "Ask for the review in the same visit" and e["date"] == "2026-10-01"
    assert e["source_url"] == "https://example.com/video/1"
    assert "lane local_seo" in e["evidence"] and "DEMONSTRATED" in e["evidence"]


def test_backlog_statuses():
    text = """Last updated: 2026-10-01

### 3.1 Renewals watcher (`NEXT`)
- **What:** Warns before renewals.

## 4. Upgrades

| Upgrade | Agent | Source | Status |
|---|---|---|---|
| Remember rejected ideas | Growth Scout | tune-up | BUILT |

## 5. Done
"""
    items = list(extract_backlog(text))
    assert [(i["type"], i["title"], i["status"]) for i in items] == [
        ("agent_idea", "Renewals watcher", "approved"), ("upgrade", "Remember rejected ideas", "done")]


def test_report_parsing_strips_narration_and_flags_backup_model(tmp_path):
    f = tmp_path / "2026-10-04_09-00-00.md"
    f.write_text("""# Cron Job: Daily blog ideas

**Job ID:** abc
**Run Time:** 2026-10-04 09:00:00

## Response

Let me check the search results first.

Provider fallback: primary-model unavailable; using backup-model for this response.

Decision today: WATCH - Video verification
""", encoding="utf-8")
    info = hq_ingest.parse_report(f)
    assert info["job_name"] == "Daily blog ideas" and info["started_at"] == "2026-10-04T09:00:00"
    assert info["used_fallback"] == 1 and info["fallback_model"] == "backup-model"
    assert info["headline"] == "Decision today: WATCH - Video verification"
    assert "Let me check" not in info["body"]


def test_failed_run_detected(tmp_path):
    f = tmp_path / "r.md"
    f.write_text("# Cron Job: Social ideas fact check (FAILED)\n\n## Response\n\nmissing draft\n", encoding="utf-8")
    info = hq_ingest.parse_report(f)
    assert info["status"] == "failed" and info["job_name"] == "Social ideas fact check"
