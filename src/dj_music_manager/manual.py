"""Ajout manuel de fichiers (glisser-déposer, scan de dossier) : même dédoublonnage, analyse qualité locale."""

from pathlib import Path

from .files import copy_into
from .library import Library
from .quality.analysis import analyze
from .quality.badge import badge
from .tags import read_tags

AUDIO_EXTS = {".mp3", ".flac", ".wav", ".aiff", ".aif", ".m4a", ".aac", ".ogg", ".opus"}


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
    a = analyze(src, ffmpeg, ffprobe)  # avant la copie : un fichier illisible n'entre pas dans RAW
    dest = src if in_raw else copy_into(raw, src)
    try:
        track_id, created = lib.add_track({
            **info,
            "path": dest.relative_to(raw).as_posix(),
            "source": "manual",
            "real_quality": a.estimate,
            "lossless": a.lossless,
            "max_frequency": round(a.max_frequency),
            "badge": badge(a.estimate, a.lossless, info["format"]),
            "quality_origin": "local",
        })
    except BaseException:
        if dest != src:
            dest.unlink(missing_ok=True)
        raise
    if not created and dest != src:
        dest.unlink()  # doublon apparu en parallèle : on retire notre copie, jamais l'original
    return created, track_id
