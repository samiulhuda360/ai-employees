"""Relays: the fact-checked version wins; a failed or missing check is labelled, never hidden."""

from __future__ import annotations

import os
import sys
import time

import pytest
from conftest import ROOT

sys.path.insert(0, str(ROOT / "employees" / "claudia" / "scripts"))
sys.path.insert(0, str(ROOT / "employees" / "content-scout"))

import latest_draft  # noqa: E402
import relay_report  # noqa: E402


@pytest.fixture()
def profiles(tmp_path, monkeypatch):
    out = tmp_path / "blog-planner" / "cron"
    (out / "output" / "d1").mkdir(parents=True)
    (out / "output" / "f1").mkdir(parents=True)
    (out / "jobs.json").write_text('{"jobs": [{"id": "d1", "name": "Daily blog ideas"}, {"id": "f1", "name": "Blog ideas fact check"}]}',
                                   encoding="utf-8")
    monkeypatch.setattr(relay_report, "PROFILES", tmp_path)
    monkeypatch.setattr(latest_draft, "PROFILES", tmp_path)
    return out / "output"


def write(path, body, failed=False, age_s=0):
    path.write_text(f"# Cron Job: x{' (FAILED)' if failed else ''}\n\n## Response\n\n{body}\n", encoding="utf-8")
    t = time.time() - age_s
    os.utime(path, (t, t))


def test_checked_version_wins(profiles):
    write(profiles / "d1" / "a.md", "BLOG IDEAS\n1. Draft title", age_s=60)
    write(profiles / "f1" / "b.md", "Checked.\n\nBLOG IDEAS\n1. Checked title")
    text = relay_report.latest_report("blog-planner", "Daily blog ideas", "BLOG IDEAS", "Blog ideas fact check")
    assert text.startswith("BLOG IDEAS") and "Checked title" in text and "Draft title" not in text


def test_unchecked_draft_is_labelled(profiles):
    write(profiles / "f1" / "b.md", "BLOG IDEAS\n1. Old check", age_s=3600)
    write(profiles / "d1" / "a.md", "BLOG IDEAS\n1. Fresh draft")
    text = relay_report.latest_report("blog-planner", "Daily blog ideas", "BLOG IDEAS", "Blog ideas fact check")
    assert text.startswith("[unchecked:") and "Fresh draft" in text


def test_backup_model_runs_are_flagged(profiles):
    write(profiles / "d1" / "a.md", "Provider fallback: main unavailable; using backup-model for this response.\n\nBLOG IDEAS\n1. T")
    text = relay_report.latest_report("blog-planner", "Daily blog ideas", "BLOG IDEAS")
    assert text.startswith("[backup model backup-model wrote this") and "Provider fallback" not in text


def test_failed_run_is_reported_not_relayed(profiles):
    write(profiles / "d1" / "a.md", "BLOG IDEAS\n1. T", failed=True)
    assert "failed or produced no list" in relay_report.latest_report("blog-planner", "Daily blog ideas", "BLOG IDEAS")


def test_fact_check_gets_the_newest_draft(profiles):
    write(profiles / "d1" / "old.md", "BLOG IDEAS\n1. Old", age_s=600)
    write(profiles / "d1" / "new.md", "BLOG IDEAS\n1. New")
    text = latest_draft.draft_for("blog-planner", "Daily blog ideas")
    assert text.startswith("DRAFT FILE: new.md") and "1. New" in text
    assert latest_draft.draft_for("blog-planner", "No such job").startswith("NO DRAFT")
