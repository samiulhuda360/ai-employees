"""Print the newest report a PC-only agent synced to ~/agents/<agent>/pc-reports.

Code Health runs on the founder's PC, where the repositories live. sync_to_server.py copies their reports here, and
Chief's --no-agent relay jobs deliver this output to Telegram verbatim.
"""

from __future__ import annotations

from pathlib import Path

AGENTS = Path.home() / "agents"


def latest(agent: str, marker: str, state_name: str) -> str:
    folder = AGENTS / agent / "pc-reports"
    files = sorted(folder.glob("*.md"), key=lambda p: p.stat().st_mtime) if folder.exists() else []
    if not files:
        return ""
    newest = files[-1]
    # The relay runs hourly and sends each report once; empty stdout = silent.
    # A PC that stays off shows up in Chief's daily fleet report instead.
    state = folder / state_name
    if state.exists() and state.read_text(encoding="utf-8").strip() == newest.name:
        return ""
    text = newest.read_text(encoding="utf-8", errors="replace")
    if "(FAILED)" in (text.splitlines() or [""])[0]:
        body = f"[relay] {agent}'s latest run failed on the PC ({newest.name}). Ask Chief to check it."
    else:
        body = text.rsplit("## Response", 1)[-1] if "## Response" in text else text
        body = body[body.index(marker):] if marker in body else body.strip()
    state.write_text(newest.name, encoding="utf-8")
    return body.strip()


def main(agent: str, marker: str) -> None:
    text = latest(agent, marker, ".relayed")
    try:  # shorter, tidier Telegram message; the original is sent if formatting fails
        import tg_pretty
        text = tg_pretty.pretty(text)
    except Exception:  # noqa: BLE001
        pass
    print(text)
