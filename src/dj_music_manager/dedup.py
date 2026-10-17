"""Normalisation artiste + titre pour le dédoublonnage (jamais par nom de fichier)."""

import re
import unicodedata

DURATION_TOLERANCE = 3  # secondes

_FEAT_BRACKET = re.compile(r"[(\[]\s*(?:feat|ft|featuring)\b[^)\]]*[)\]]", re.I)
_FEAT_TRAILING = re.compile(r"\s(?:feat|ft|featuring)\b.*?(?=[(\[]|$)", re.I)
_ORIGINAL_MIX = re.compile(r"[(\[]\s*original mix\s*[)\]]|-\s*original mix\b", re.I)
_ARTIST_SEP = re.compile(r"\s*[,;/&]\s*|\s+(?:x|vs\.?)\s+", re.I)


def norm_text(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c)).casefold()
    return " ".join(re.sub(r"[\W_]+", " ", s).split())


def _strip(s: str) -> str:
    s = _FEAT_BRACKET.sub(" ", s)
    s = _FEAT_TRAILING.sub(" ", s)
    return _ORIGINAL_MIX.sub(" ", s)


def norm_key(artist: str | None, title: str | None) -> str | None:
    """'Daft Punk feat. X' + 'One More Time (Original Mix)' -> 'daft punk|one more time'."""
    if not artist or not title:
        return None
    main_artist = _ARTIST_SEP.split(_strip(artist), maxsplit=1)[0]
    return f"{norm_text(main_artist)}|{norm_text(_strip(title))}"


def titles_match(expected: str | None, got: str | None) -> bool:
    """Même morceau à la version près : « One More Time » ~ « One More Time (Radio Edit) », pas « Voyager »."""
    a, b = norm_text(_strip(expected or "")), norm_text(_strip(got or ""))
    return not a or not b or a in b or b in a


def same_duration(a: float | None, b: float | None) -> bool:
    return a is not None and b is not None and abs(a - b) <= DURATION_TOLERANCE
