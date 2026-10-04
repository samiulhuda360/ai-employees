"""Generate the HQ hologram characters with gpt-image-2 on the owner's ChatGPT plan.

Uses Hermes' own `openai-codex` image provider (the native Codex images endpoint, the
same route the official Codex client uses), so there is no API key and no per-image
cost beyond the ChatGPT Plus plan's own limits. Run with Hermes' Python:

    ~/.hermes/tools/python-*/bin/python3 gen_art.py chief-assistant [more ids...]
    ... gen_art.py all

Images are copied to ~/hq/web/art/<id>.png (served to the deck) and ~/hq/art/<id>.png.
Characters are drawn on pure black: the deck blends them additively, so black becomes
transparent and the figure glows like a projected hologram.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

REPO = Path.home() / ".hermes" / "hermes-agent"
sys.path.insert(0, str(REPO))

STYLE = (
    "Full-body character standing, facing the viewer, relaxed pose, centred, on a pure solid black "
    "background (#000000) with nothing else in the frame. Style: sleek sci-fi hologram projected from "
    "below, the body made of translucent glowing light, fine horizontal scanlines, thin luminous "
    "wireframe edges, soft bloom, subtle chromatic fringe, a faint light cone rising from the feet. "
    "No text, no logos, no insignia, no badges or emblems on the clothing, no franchise-style uniforms, no floor, no border. Clean silhouette readable at small size. One consistent "
    "series: same proportions, line weight and lighting for every crew member. Calm, capable, friendly. "
)

CHARACTERS = {
    "chief-assistant": "Claudia, the chief of staff of a starship crew. Warm white-gold glow (#f4dca0). "
                       "Poised and confident, a small floating holographic clipboard at her side, a thin headset mic, hair tied back.",
    "opportunity-scout": "Opportunity Scout, an explorer. Cyan glow (#29d9f5). Scanner visor pushed up on the forehead, "
                         "one hand shading the eyes as if looking into the distance, a small radar disc on the wrist.",
    "growth-scout": "Growth Scout, an analyst. Cyan glow (#29d9f5). Holding a glowing data tablet with a rising "
                                 "line chart, slim glasses, focused expression.",
    "blog-planner": "Blog Planner, a writer. Coral glow (#ff8a65). Holding a light stylus beside a floating page with faint "
                    "lines of text, thoughtful expression.",
    "social-planner": "Social Planner, a broadcaster. Coral glow (#ff8a65). Energetic, a small floating satellite dish over "
                      "one shoulder and a phone-shaped light panel in hand, mid-gesture.",
    "prospect-finder": "Prospect Finder, a tracker. Amber glow (#f7b538). A targeting-reticle monocle over one eye and a small "
                       "floating map with glowing pins.",
    "client-wins": "Client Wins, a support lead. Amber glow (#f7b538). Wearing a headset, one hand raised in a warm welcoming wave.",
    "code-health": "Code Health, a code doctor. Green glow (#3ee6a8). Holding a diagnostic scanner wand, a small floating shield icon.",
    "youtube-watcher": "YouTube Watcher, a learner. Green glow (#3ee6a8). Headphones on, two small floating video screens beside the head.",
}

OUT_WEB = Path.home() / "hq" / "web" / "art"
OUT_KEEP = Path.home() / "hq" / "art"


def load_provider():
    path = REPO / "plugins" / "image_gen" / "openai-codex" / "__init__.py"
    spec = importlib.util.spec_from_file_location("hq_codex_image", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod.OpenAICodexImageGenProvider()


def main(ids: list[str]) -> None:
    if ids == ["all"]:
        ids = list(CHARACTERS)
    provider = load_provider()
    OUT_WEB.mkdir(parents=True, exist_ok=True)
    OUT_KEEP.mkdir(parents=True, exist_ok=True)
    for aid in ids:
        if aid not in CHARACTERS:
            print(f"{aid}: unknown id")
            continue
        result = provider.generate(STYLE + CHARACTERS[aid], aspect_ratio="portrait")
        if not result.get("success", result.get("image")):
            print(f"{aid}: FAILED {json.dumps(result)[:400]}")
            continue
        src = Path(result["image"])
        for dest in (OUT_WEB / f"{aid}.png", OUT_KEEP / f"{aid}.png"):
            shutil.copyfile(src, dest)
        print(f"{aid}: ok {result.get('pixel_size') or result.get('extra', {}).get('pixel_size')} -> {OUT_WEB / (aid + '.png')}")


if __name__ == "__main__":
    main(sys.argv[1:] or ["chief-assistant"])
