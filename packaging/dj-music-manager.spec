# Paquet Windows : uv run pyinstaller packaging/dj-music-manager.spec --noconfirm
# ffmpeg/ffprobe sont pris dans FFMPEG_DIR, sinon dans le PATH, et copiés dans _internal/ffmpeg.
import os
import shutil
from pathlib import Path

ffmpeg_dir = os.environ.get("FFMPEG_DIR")
tools = []
for name in ("ffmpeg", "ffprobe"):
    found = str(Path(ffmpeg_dir) / f"{name}.exe") if ffmpeg_dir else shutil.which(name)
    if not found or not Path(found).is_file():
        raise SystemExit(f"{name}.exe introuvable : mets-le dans le PATH ou indique FFMPEG_DIR")
    tools.append((os.path.realpath(found), "ffmpeg"))  # realpath : les liens de WinGet sont des raccourcis

root = Path(SPECPATH).parent
a = Analysis(
    [str(root / "packaging" / "launch.py")],
    pathex=[str(root / "src")],
    datas=tools,  # en données : pas d'analyse de dépendances sur ces exe autonomes
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="dj-music-manager", console=False)
coll = COLLECT(exe, a.binaries, a.datas, name="dj-music-manager")
