"""Hermes cron entry for Code Health Watcher: run the collector kept in the project folder."""
import os
import runpy
import sys

# Hermes runs a copy of this file from the profile; CODE_HEALTH_DIR points back at the agent folder.
COLLECTOR = os.path.join(os.environ.get("CODE_HEALTH_DIR") or os.path.dirname(os.path.abspath(__file__)), "code_health_collector.py")
sys.stdout.reconfigure(encoding="utf-8")
sys.argv = [COLLECTOR]
runpy.run_path(COLLECTOR, run_name="__main__")
