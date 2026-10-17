"""Lecture des tags existants (jamais d'écriture)."""

from pathlib import Path

import mutagen
from mutagen.easymp4 import EasyMP4Tags
from mutagen.id3 import ID3

EasyMP4Tags.RegisterFreeformKey("isrc", "ISRC")  # M4A : ISRC en champ iTunes libre
# AIFF et WAV portent de l'ID3 brut : mutagen n'en a pas de variante « easy »
ID3_FRAMES = {"artist": "TPE1", "title": "TIT2", "album": "TALB", "isrc": "TSRC", "bpm": "TBPM"}


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
        "duration": f.info.length,
        "format": Path(path).suffix.lstrip(".").lower(),
    }
