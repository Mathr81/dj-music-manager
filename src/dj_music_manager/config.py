"""Emplacement des données et clé API (Gestionnaire d'identifiants Windows, jamais dans le code ni la base)."""

import logging
import os
import shutil
import sys
from logging.handlers import RotatingFileHandler
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


def bundled_ffmpeg_dir() -> Path | None:
    """Dossier du ffmpeg embarqué dans le paquet PyInstaller (None hors paquet)."""
    bundle = getattr(sys, "_MEIPASS", None)
    return Path(bundle) / "ffmpeg" if bundle else None


def ffmpeg_tools(ffmpeg_dir: str | None) -> tuple[str, str]:
    """(ffmpeg, ffprobe) : dossier des Réglages, sinon celui embarqué dans l'app, sinon PATH."""
    ffmpeg_dir = ffmpeg_dir or bundled_ffmpeg_dir()
    if ffmpeg_dir:
        tools = (str(Path(ffmpeg_dir) / "ffmpeg.exe"), str(Path(ffmpeg_dir) / "ffprobe.exe"))
    else:
        tools = (shutil.which("ffmpeg"), shutil.which("ffprobe"))
    if not all(t and Path(t).is_file() for t in tools):
        raise FileNotFoundError("ffmpeg/ffprobe introuvables : indique leur dossier dans les Réglages")
    return tools


def api_log_path() -> Path:
    return data_dir() / "api_errors.log"


def setup_api_log() -> Path:
    """Journal des erreurs de l'API (2 Mo, 3 archives) : une ligne par incident, pour corriger l'API."""
    path = api_log_path()
    handler = RotatingFileHandler(path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s | %(message)s", "%Y-%m-%d %H:%M:%S"))
    logger = logging.getLogger("dj_music_manager.api")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return path
