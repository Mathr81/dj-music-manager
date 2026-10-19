"""Construit l'app et son installateur : uv run python packaging/build.py
1. ffmpeg « essentials » de gyan.dev (téléchargé une fois dans build/ffmpeg, empreinte SHA-256 vérifiée) ;
2. dossier autonome dist/dj-music-manager (PyInstaller) ;
3. installateur dist/DJ-Music-Manager-<version>-setup.exe (Inno Setup 6)."""

import hashlib
import os
import shutil
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

import httpx  # certificats de certifi : celui de gyan.dev échoue avec le magasin de Python

ROOT = Path(__file__).resolve().parents[1]
FFMPEG_ZIP = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
FFMPEG_DIR = ROOT / "build" / "ffmpeg"
ISCC = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Inno Setup 6" / "ISCC.exe"


def fetch_ffmpeg():
    if all((FFMPEG_DIR / f"{n}.exe").is_file() for n in ("ffmpeg", "ffprobe")):
        return
    FFMPEG_DIR.mkdir(parents=True, exist_ok=True)
    archive = FFMPEG_DIR / "essentials.zip"
    print("téléchargement de", FFMPEG_ZIP)
    with httpx.stream("GET", FFMPEG_ZIP, follow_redirects=True, timeout=60) as r, archive.open("wb") as f:
        r.raise_for_status()
        for chunk in r.iter_bytes():
            f.write(chunk)
    expected = httpx.get(FFMPEG_ZIP + ".sha256", follow_redirects=True).raise_for_status().text.split()[0].lower()
    if hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
        archive.unlink()
        raise SystemExit("ffmpeg : empreinte SHA-256 incorrecte, archive supprimée")
    with zipfile.ZipFile(archive) as z:
        for member in z.namelist():
            name = member.rsplit("/", 1)[-1]
            if member.endswith(f"/bin/{name}") and name in ("ffmpeg.exe", "ffprobe.exe"):
                (FFMPEG_DIR / name).write_bytes(z.read(member))  # nom fixe : rien n'est écrit hors du dossier
    archive.unlink()


def main():
    fetch_ffmpeg()
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    shutil.rmtree(ROOT / "dist" / "dj-music-manager", ignore_errors=True)
    subprocess.run([sys.executable, "-m", "PyInstaller", "packaging/dj-music-manager.spec", "--noconfirm",
                    "--distpath", "dist", "--workpath", "build/pyinstaller"],
                   cwd=ROOT, check=True, env={**os.environ, "FFMPEG_DIR": str(FFMPEG_DIR)})
    if not ISCC.is_file():
        raise SystemExit(f"Inno Setup 6 introuvable ({ISCC}) : winget install JRSoftware.InnoSetup")
    subprocess.run([str(ISCC), f"/DAppVersion={version}", "packaging/installer.iss"], cwd=ROOT, check=True)
    print("ok :", ROOT / "dist" / f"DJ-Music-Manager-{version}-setup.exe")


if __name__ == "__main__":
    main()
