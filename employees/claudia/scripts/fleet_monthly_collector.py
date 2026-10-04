"""Thirty-day fleet snapshot for Chief Assistant's monthly review.

Adds this month's weekly records so the monthly record can show trends.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import fleet_status_collector as fleet  # noqa: E402

RECORDS = Path.home() / "agents" / "chief-assistant" / "records"

fleet.EXCERPT_CHARS = 400
fleet.LIST_HEADLINES = True

if __name__ == "__main__":
    fleet.main(30)
    weekly = sorted(RECORDS.glob("weekly-*.md"))[-5:] if RECORDS.exists() else []
    print(f"\n===== WEEKLY RECORDS ({len(weekly)}) | {datetime.now():%Y-%m-%d} =====")
    for path in weekly:
        print(f"\n--- {path.name} ---\n{path.read_text(encoding='utf-8')[:3000]}")
    if not weekly:
        print("none yet")
