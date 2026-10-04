"""Generate synthetic face photos for building 3D avatars on avaturn.me.

Same route as gen_art.py: Hermes' openai-codex image provider (gpt-image-2 on the ChatGPT
plan, no API key). These are invented people, not photos of anyone real. Output goes to
~/hq/art/faces/<id>.png. Run with Hermes' Python:

    ~/.hermes/tools/python-*/bin/python3 gen_faces.py
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gen_art import load_provider  # noqa: E402

STYLE = (
    "Photorealistic passport-style studio photo of an invented adult person who does not exist. "
    "Facing the camera straight on, head and top of shoulders, neutral relaxed expression with the mouth closed, "
    "eyes open looking into the lens, even soft front lighting with no harsh shadows, plain light grey background, "
    "hair pulled back or short so the ears and forehead are visible, no glasses, no hat, no jewellery, no makeup "
    "effects, sharp focus, natural skin texture, 50mm lens. This is a reference photo for building a 3D avatar. "
)

FACES = {
    "woman-1": "A woman in her early 30s, Northern European, light brown hair tied back, calm and warm.",
    "woman-2": "A woman in her mid 30s, South Asian, dark hair tied back, composed and friendly.",
    "woman-3": "A woman in her late 20s, East Asian, black hair in a low bun, attentive and kind.",
    "man-1": "A man in his mid 30s, Mediterranean, short dark hair, clean shaven, calm and confident.",
    "man-2": "A man in his early 40s, West African, short cropped hair, neat short beard, friendly.",
    "man-3": "A man in his late 20s, Latin American, short wavy hair, clean shaven, relaxed and approachable.",
}

OUT = Path.home() / "hq" / "art" / "faces"


def main() -> None:
    provider = load_provider()
    OUT.mkdir(parents=True, exist_ok=True)
    for fid, desc in FACES.items():
        result = provider.generate(STYLE + desc, aspect_ratio="square")
        if not result.get("success", result.get("image")):
            print(f"{fid}: FAILED {str(result)[:300]}")
            continue
        shutil.copyfile(result["image"], OUT / f"{fid}.png")
        print(f"{fid}: ok")


if __name__ == "__main__":
    main()
