"""Lecture des tags existants (jamais d'écriture)."""

from pathlib import Path

import mutagen
from mutagen.easyid3 import EasyID3
from mutagen.easymp4 import EasyMP4Tags
from mutagen.id3 import ID3

from .key import parse as parse_key

EasyMP4Tags.RegisterFreeformKey("isrc", "ISRC")  # M4A : ISRC en champ iTunes libre
EasyID3.RegisterTextKey("initialkey", "TKEY")  # MP3 : tonalité, absente des clés « easy » par défaut
# AIFF et WAV portent de l'ID3 brut : mutagen n'en a pas de variante « easy »
ID3_FRAMES = {"artist": "TPE1", "title": "TIT2", "album": "TALB", "isrc": "TSRC", "bpm": "TBPM", "initialkey": "TKEY"}


def read_tags(path: Path) -> dict:
    f = mutagen.File(path, easy=True)
    if f is None:
        raise ValueError(f"format audio non reconnu : {path}")
    t = f.tags or {}
    if isinstance(t, ID3):
        t = {key: [str(v) for v in t[frame].text] for key, frame in ID3_FRAMES.items() if frame in t}

    def first(key):
        v = t.get(key)
        return v[0] if v else None

    try:
        bpm = float(first("bpm")) or None
    except (TypeError, ValueError):
        bpm = None
    return {
        "artist": ", ".join(t.get("artist", [])) or None,
        "title": first("title"),
        "album": first("album"),
        "isrc": first("isrc"),
        "bpm": bpm,
        "key": parse_key(first("initialkey") or first("key")),  # FLAC/Vorbis : INITIALKEY ou KEY
        "duration": f.info.length,
        "format": Path(path).suffix.lstrip(".").lower(),
    }


def cover(path: Path) -> bytes | None:
    """Pochette intégrée au fichier (ID3 APIC, image FLAC, covr MP4), sinon None."""
    f = mutagen.File(path)
    if f is None:
        return None
    if getattr(f, "pictures", None):  # FLAC
        return f.pictures[0].data
    tags = f.tags or {}
    if isinstance(tags, ID3):
        pics = tags.getall("APIC")
        return pics[0].data if pics else None
    covr = tags.get("covr") if hasattr(tags, "get") else None
    return bytes(covr[0]) if covr else None
