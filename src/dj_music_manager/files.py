"""Écriture dans RAW sans jamais écraser un fichier existant."""

import itertools
import os
import shutil
from pathlib import Path


def reserve(dest_dir: Path, name: str):
    """Réserve un nom libre en créant le .part en exclusif (sûr même en parallèle). Renvoie (final, fichier .part)."""
    stem, ext = os.path.splitext(name)
    for n in itertools.count(1):
        final = Path(dest_dir) / (name if n == 1 else f"{stem} ({n}){ext}")
        if final.exists():
            continue
        try:
            return final, open(final.with_name(final.name + ".part"), "xb")
        except FileExistsError:
            continue


def copy_into(dest_dir: Path, src: Path) -> Path:
    final, f = reserve(dest_dir, src.name)
    part = Path(f.name)
    try:
        with f, open(src, "rb") as source:
            shutil.copyfileobj(source, f, 1024 * 1024)
        os.rename(part, final)  # sous Windows, échoue plutôt que d'écraser
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    return final
