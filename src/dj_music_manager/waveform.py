"""Forme d'onde du lecteur : niveaux des graves, médiums et aigus par tranche (rendu 3 bandes façon rekordbox)."""

import subprocess
from pathlib import Path

import numpy as np

SR = 8000  # assez pour l'allure du morceau et les 3 bandes
BUCKETS = 800
LOW, HIGH = 250, 2000  # Hz : graves < 250, aigus > 2000
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def decode(path: Path, ffmpeg: str) -> np.ndarray:
    raw = subprocess.run([ffmpeg, "-v", "error", "-i", str(path), "-map", "0:a:0", "-ac", "1", "-ar", str(SR),
                          "-f", "f32le", "-"], capture_output=True, check=True, creationflags=_NO_WINDOW).stdout
    return np.frombuffer(raw, np.float32)


def analyze(y: np.ndarray, buckets: int = BUCKETS) -> np.ndarray:
    """Niveaux 0..1 des graves, médiums et aigus par tranche : tableau (tranches, 3).
    Chaque bande est rapportée à son propre maximum (98e centile) : les aigus, peu énergétiques, restent visibles."""
    n = len(y) // buckets
    if n < 16:
        return np.zeros((buckets, 3), np.float32)
    chunks = y[: n * buckets].reshape(buckets, n)
    power = np.abs(np.fft.rfft(chunks, axis=1)) ** 2
    freqs = np.fft.rfftfreq(n, 1 / SR)
    bands = np.sqrt(np.stack([power[:, freqs < LOW].sum(1), power[:, (freqs >= LOW) & (freqs < HIGH)].sum(1),
                              power[:, freqs >= HIGH].sum(1)], axis=1))
    top = np.percentile(bands, 98, axis=0)
    return np.clip(bands / np.where(top > 0, top, 1), 0, 1).astype(np.float32)


def compute(path: Path, ffmpeg: str) -> np.ndarray:
    return analyze(decode(path, ffmpeg))
