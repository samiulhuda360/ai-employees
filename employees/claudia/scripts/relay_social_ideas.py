"""Relay Social Planner's latest idea menu for Chief to forward."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import relay_report

if __name__ == "__main__":
    relay_report.main("social-planner", "Daily social ideas", "SOCIAL IDEAS", "Social ideas fact check")
