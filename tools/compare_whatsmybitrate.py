"""Compare l'analyse qualité de l'app avec whatsmybitrate d'origine sur des fichiers réels.

    uv run python tools/compare_whatsmybitrate.py <dépôt whatsmybitrate> <python de son venv> FICHIER...

Le python passé doit avoir les dépendances de whatsmybitrate (requirements.txt) ; ffmpeg/ffprobe dans le PATH.
Code de sortie 1 si une estimation ou le verdict lossless diffère.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

from dj_music_manager.quality.analysis import analyze

ORIGINAL = """
import json, sys
sys.path.insert(0, sys.argv[1])
from wmb_core import AudioFile
for f in sys.argv[2:]:
    a = AudioFile(f)
    a.analyze()
    print(json.dumps({"file": f, "max_frequency": a.max_frequency_peak, "estimate": a.estimated_bitrate,
                      "lossless": a.is_lossless, "error": a.error}), flush=True)
"""


def main():
    repo, python, *files = sys.argv[1:]
    out = subprocess.run([python, "-I", "-c", ORIGINAL, repo, *files], capture_output=True, text=True, check=True)
    original = {r["file"]: r for r in map(json.loads, out.stdout.splitlines())}
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    mismatches = 0
    print(f"{'fichier':34} {'coupure wmb':>11} {'app':>7}  {'estimation wmb':34} {'app':34} ok")
    for f in files:
        o, a = original[f], analyze(Path(f), ffmpeg, ffprobe)
        same = o["estimate"] == a.estimate and o["lossless"] == a.lossless and not o["error"]
        mismatches += not same
        print(f"{Path(f).name[:34]:34} {o['max_frequency'] or 0:11.0f} {a.max_frequency:7.0f}  "
              f"{str(o['estimate'] or o['error'])[:34]:34} {a.estimate:34} {'✓' if same else '✗'}")
    print(f"\n{len(files) - mismatches}/{len(files)} identiques")
    sys.exit(1 if mismatches else 0)


if __name__ == "__main__":
    main()
