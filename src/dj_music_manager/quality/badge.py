"""Badge qualité unique, depuis une estimation au format whatsmybitrate (= en-tête X-Real-Quality)."""

import re

BADGES = ("128", "192", "256", "320", "Lossless", "Faux FLAC", "Transcode")
LOSSLESS_FORMATS = {"flac", "aiff", "aif", "wav", "alac"}


def badge(real_quality: str | None, lossless: bool | None = None, fmt: str | None = None) -> str | None:
    """None si la qualité est inconnue (ex. 'unknown' pour AAC/M4A)."""
    if fmt in LOSSLESS_FORMATS and lossless is False:
        return "Faux FLAC"  # X-Lossless=false sur un FLAC = issu d'un MP3
    if not real_quality:
        return None
    if "Transcoded to Lossless" in real_quality:
        return "Faux FLAC"
    if "Re-encoded" in real_quality:
        return "Transcode"
    if real_quality == "Lossless":
        return "Lossless"
    m = re.search(r"~(\d+)\s*kbps", real_quality)
    if not m:
        return None
    kbps = int(m[1])  # 96 et 160 -> 128, 224 -> 192
    return next((b for b in ("320", "256", "192") if kbps >= int(b)), "128")
