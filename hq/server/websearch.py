"""Free web search for collectors: `websearch.py "query" [max]` prints JSON results.

Uses the ddgs library (Brave / Bing / DuckDuckGo behind one call), installed in the HQ
venv so Hermes' own Python stays untouched. Collectors call it as a subprocess:

    ~/hq/venv/bin/python ~/hq/server/websearch.py "site:quora.com is there a tool" 10
"""

from __future__ import annotations

import json
import sys


def main() -> None:
    query = sys.argv[1]
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    from ddgs import DDGS
    results, errors = [], []
    for backend in ("brave", "bing", "auto"):
        try:
            results = DDGS().text(query, max_results=limit, backend=backend)
            if results:
                break
        except Exception as exc:  # noqa: BLE001  try the next engine
            errors.append(f"{backend}: {type(exc).__name__}")
    print(json.dumps({"query": query, "results": [{"title": r.get("title", ""), "url": r.get("href", ""),
                                                   "snippet": r.get("body", "")} for r in results or []],
                      "errors": errors}, ensure_ascii=True))


if __name__ == "__main__":
    main()
