"""Inject Blog Planner's newest draft into its fact-check job."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import latest_draft

if __name__ == "__main__":
    print(latest_draft.draft_for("blog-planner", "Daily blog ideas"))
