"""Seven-day fleet snapshot for Chief Assistant's weekly growth review."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import fleet_status_collector as fleet  # noqa: E402

fleet.EXCERPT_CHARS = 600  # more reports per agent, shorter excerpt each
fleet.LIST_HEADLINES = True

if __name__ == "__main__":
    fleet.main(7)
