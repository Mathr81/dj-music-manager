"""Lecture des tags existants (jamais d'écriture)."""

from pathlib import Path

import mutagen


def read_tags(path: Path) -> dict:
    f = mutagen.File(path, easy=True)
    if f is None:
        raise ValueError(f"format audio non reconnu : {path}")
    t = f.tags or {}

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
