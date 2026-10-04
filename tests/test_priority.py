"""The priority scorer: deterministic, explained, and it forgets stale snapshots."""

from __future__ import annotations

from datetime import date

from hq_priority import rank, score

TODAY = date(2026, 10, 4)


def idea(id_: int, type_: str, title: str, day: str = "2026-10-04", **kw) -> dict:
    return {"id": id_, "agent": kw.pop("agent", "blog-planner"), "date": day, "type": type_, "title": title,
            "summary": kw.pop("summary", ""), "detail": kw.pop("detail", ""), "evidence": kw.pop("evidence", ""),
            "status": kw.pop("status", "new"), "source_url": None, **kw}


def test_tier_a_blog_is_p1_with_reasons():
    s = score(idea(1, "blog", "How to ask for reviews", summary="Tier A. Query: x", evidence="SERP-checked, winnable"), today=TODAY)
    assert s["tier"] == "P1" and s["score"] == 80
    assert s["why"] == ["blog idea", "top 10 checked and beatable", "Tier A", "new today"]


def test_age_decays_by_type():
    fresh = score(idea(1, "social", "Hook"), today=TODAY)["score"]
    old = score(idea(1, "social", "Hook", day="2026-09-30"), today=TODAY)["score"]
    assert fresh - old == 10 + 4 * 4  # same-day bonus, then 4 points a day


def test_repeats_merge_and_add_points():
    ranked = rank([idea(1, "blog", "Reviews guide", day="2026-10-02"), idea(2, "blog", "Reviews guide!", day="2026-10-04")],
                  today=TODAY)
    assert len(ranked) == 1 and ranked[0]["id"] == 2 and "raised 2 times" in ranked[0]["why"]


def test_decided_ideas_drop_out():
    ranked = rank([idea(1, "blog", "a", status="rejected"), idea(2, "blog", "b", status="done"), idea(3, "blog", "c")], today=TODAY)
    assert [r["id"] for r in ranked] == [3]


def test_snapshot_types_keep_only_the_latest_run():
    ranked = rank([idea(1, "code_fix", "HIGH old finding", day="2026-09-28", agent="code-health"),
                   idea(2, "code_fix", "HIGH new finding", day="2026-10-04", agent="code-health")], today=TODAY)
    assert [r["id"] for r in ranked] == [2]


def test_growth_verdicts_order():
    build = score(idea(1, "feature", "w", evidence="Growth Scout verdict BUILD"), today=TODAY)["score"]
    exp = score(idea(2, "feature", "x", evidence="Growth Scout verdict EXPERIMENT"), today=TODAY)["score"]
    watch = score(idea(3, "feature", "y", evidence="Growth Scout verdict WATCH"), today=TODAY)["score"]
    assert build > exp > watch


def test_same_input_same_score():
    i = idea(1, "cash_build", "Tool", summary="build: 2 days - price: USD 9", evidence="Cash Builds #1")
    assert score(i, today=TODAY) == score(dict(i), today=TODAY)
