"""Emplacement des données et clé API (Gestionnaire d'identifiants Windows, jamais dans le code ni la base)."""

import os
import shutil
from pathlib import Path

import keyring

SERVICE = "dj-music-manager"
DEFAULT_QUALITY = "mp3_320"


def data_dir() -> Path:
    d = Path(os.environ.get("APPDATA", Path.home())) / "dj-music-manager"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_api_key() -> str | None:
    return keyring.get_password(SERVICE, "api_key")


def set_api_key(key: str):
    keyring.set_password(SERVICE, "api_key", key)


def get_spotify_token() -> str | None:
    return keyring.get_password(SERVICE, "spotify_refresh_token")


def set_spotify_token(token: str):
    keyring.set_password(SERVICE, "spotify_refresh_token", token)


def ffmpeg_tools(ffmpeg_dir: str | None) -> tuple[str, str]:
    """(ffmpeg, ffprobe) : dossier des Réglages, sinon PATH."""
    # shortcut: le ffmpeg embarqué à côté de l'exe arrive avec le paquet PyInstaller (étape 7)
    if ffmpeg_dir:
        tools = (str(Path(ffmpeg_dir) / "ffmpeg.exe"), str(Path(ffmpeg_dir) / "ffprobe.exe"))
    else:
        tools = (shutil.which("ffmpeg"), shutil.which("ffprobe"))
    if not all(t and Path(t).is_file() for t in tools):
        raise FileNotFoundError("ffmpeg/ffprobe introuvables : indique leur dossier dans les Réglages")
    return tools
