"""Relay Code Health Watcher's newest weekly report (synced from the PC)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import relay_pc_report

if __name__ == "__main__":
    relay_pc_report.main("code-health", "CODE HEALTH")
