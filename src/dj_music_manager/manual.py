"""Ajout manuel (glisser-déposer, scan de dossier) et remplacement d'un son : même dédoublonnage, analyse locale."""

import os
from pathlib import Path

from send2trash import send2trash

from .files import copy_into
from .library import Library
from .quality.analysis import analyze
from .quality.badge import badge
from .tags import read_tags

AUDIO_EXTS = {".mp3", ".flac", ".wav", ".aiff", ".aif", ".m4a", ".aac", ".ogg", ".opus"}
DURATION_WARNING = 10  # s


def _quality_fields(src: Path, fmt: str, ffmpeg: str, ffprobe: str) -> dict:
    a = analyze(src, ffmpeg, ffprobe)
    return {"real_quality": a.estimate, "lossless": a.lossless, "max_frequency": round(a.max_frequency),
            "badge": badge(a.estimate, a.lossless, fmt), "quality_origin": "local"}


def collect(paths) -> list[Path]:
    """Fichiers audio parmi des fichiers et dossiers (récursif)."""
    found = []
    for p in map(Path, paths):
        if p.is_dir():
            found += sorted(f for f in p.rglob("*") if f.suffix.lower() in AUDIO_EXTS and f.is_file())
        elif p.suffix.lower() in AUDIO_EXTS and p.is_file():
            found.append(p)
    return found


def add_file(lib: Library, src: Path, raw: Path, ffmpeg: str, ffprobe: str) -> tuple[bool, int]:
    """(ajouté, ID interne). Copie dans RAW, sauf si le fichier y est déjà : il est alors référencé sur place."""
    src, raw = Path(src).resolve(), Path(raw).resolve()
    in_raw = src.is_relative_to(raw)
    if in_raw and (track_id := lib.track_by_path(src.relative_to(raw).as_posix())):
        return False, track_id  # même fichier déjà référencé (nouveau scan de RAW)
    info = read_tags(src)
    info["title"] = info["title"] or src.stem
    track_id = lib.find_track(info["isrc"], info["artist"], info["title"], info["duration"])
    if track_id is not None:
        return False, track_id
    quality = _quality_fields(src, info["format"], ffmpeg, ffprobe)  # avant la copie : un fichier illisible n'entre pas dans RAW
    dest = src if in_raw else copy_into(raw, src)
    try:
        track_id, created = lib.add_track({**info, **quality, "path": dest.relative_to(raw).as_posix(),
                                           "source": "manual"})
    except BaseException:
        if dest != src:
            dest.unlink(missing_ok=True)
        raise
    if not created and dest != src:
        dest.unlink()  # doublon apparu en parallèle : on retire notre copie, jamais l'original
    return created, track_id


def replace_warnings(lib: Library, track_id: int, new_file: Path) -> list[str]:
    """Raisons de demander confirmation avant de remplacer (mauvais fichier choisi ?)."""
    info, current = read_tags(new_file), lib.track(track_id)
    warnings = []
    other = lib.find_track(info["isrc"], info["artist"], info["title"], info["duration"])
    if other is not None and other != track_id:
        t = lib.track(other)
        warnings.append(f"Ce fichier ressemble à un autre son de la bibliothèque : {t['artist']} – {t['title']}.")
    if current["duration"] and abs(current["duration"] - info["duration"]) > DURATION_WARNING:
        warnings.append(f"Durée différente : {current['duration']:.0f} s actuellement, {info['duration']:.0f} s pour "
                        "le nouveau fichier (autre version ?).")
    return warnings


def replace_file(lib: Library, track_id: int, new_file: Path, raw: Path, ffmpeg: str, ffprobe: str,
                 trash=send2trash) -> Path:
    """Remplace le fichier d'un son en gardant son ID : playlists, tags et ID externes suivent.
    Le nouveau fichier est copié dans RAW (l'original n'est pas touché), l'ancien part à la corbeille."""
    src, raw = Path(new_file).resolve(), Path(raw).resolve()
    current = lib.track(track_id)
    old = raw / current["path"]
    if src == old.resolve():
        raise ValueError("c'est déjà le fichier de ce son")
    in_raw = src.is_relative_to(raw)
    if in_raw and lib.track_by_path(src.relative_to(raw).as_posix()) not in (None, track_id):
        raise ValueError("ce fichier est déjà celui d'un autre son de la bibliothèque")
    info = read_tags(src)
    quality = _quality_fields(src, info["format"], ffmpeg, ffprobe)
    dest = src if in_raw else copy_into(raw, src)
    # on garde les métadonnées connues, le nouveau fichier ne complète que les champs vides
    fields = {k: current[k] or info[k] for k in ("artist", "title", "album", "isrc", "bpm")}
    fields.update(quality, path=dest.relative_to(raw).as_posix(), format=info["format"],
                  duration=info["duration"], quality=None, source="manual")
    try:
        lib.update_track(track_id, fields)
    except BaseException:
        if dest != src:
            dest.unlink(missing_ok=True)
        raise
    if old.exists():
        trash(str(old))  # récupérable depuis la corbeille Windows
        clean = raw / src.name
        if dest != src and dest.name != src.name and not clean.exists():
            os.rename(dest, clean)  # « Titre (2).mp3 » ne gênait que tant que l'ancien était là
            dest = clean
            lib.update_track(track_id, {"path": dest.relative_to(raw).as_posix()})
    return dest
