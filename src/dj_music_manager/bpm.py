"""Détection du BPM quand le fichier n'a pas de tag : enveloppe d'attaques + autocorrélation, en numpy.

Extrait de 60 s au milieu du morceau, mono 11 025 Hz. L'autocorrélation est pondérée autour de 125 BPM
(une octave d'écart type) contre les erreurs de moitié/double, puis affinée au centième sur 16 multiples.
"""

import subprocess
from pathlib import Path

import numpy as np

SR = 11025
HOP = 64
N_FFT = 1024
EXCERPT = 60.0  # s
MIN_BPM, MAX_BPM = 60.0, 200.0
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def decode_excerpt(path: Path, ffmpeg: str, duration: float | None) -> np.ndarray:
    start = max(0.0, (duration or 0) / 2 - EXCERPT / 2)
    raw = subprocess.run([ffmpeg, "-v", "error", "-ss", f"{start:.1f}", "-t", str(EXCERPT), "-i", str(path),
                          "-map", "0:a:0", "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
                         capture_output=True, check=True, creationflags=_NO_WINDOW).stdout
    return np.frombuffer(raw, np.float32)


def onset_envelope(y: np.ndarray) -> np.ndarray:
    """Flux spectral (log-compressé, positif seulement), moyenne locale retirée."""
    n = 1 + (len(y) - N_FFT) // HOP
    frames = np.lib.stride_tricks.as_strided(y, (n, N_FFT), (y.strides[0] * HOP, y.strides[0]))
    mag = np.log1p(100 * np.abs(np.fft.rfft(frames * np.hanning(N_FFT), axis=1)))
    flux = np.maximum(0, np.diff(mag, axis=0)).sum(axis=1)
    local = np.convolve(flux, np.ones(16) / 16, mode="same")
    return np.maximum(0, flux - local)


def _ac_at(ac: np.ndarray, lag: float) -> float:
    i = int(lag)
    return ac[i] + (lag - i) * (ac[i + 1] - ac[i]) if i + 1 < len(ac) else 0.0


def estimate(y: np.ndarray) -> float | None:
    if len(y) < SR * 10:
        return None
    env = onset_envelope(y)
    env = env - env.mean()
    if not env.any():
        return None  # silence
    spectrum = np.fft.rfft(env, 2 * len(env))
    ac = np.fft.irfft(spectrum * np.conj(spectrum))[: len(env)]
    fps = SR / HOP

    def score(bpm: float, multiples: int) -> float:  # peigne sur les multiples de la période
        lag = 60 * fps / bpm
        return sum(_ac_at(ac, lag * k) for k in range(1, multiples + 1))

    coarse = np.arange(MIN_BPM, MAX_BPM, 0.5)
    prior = np.exp(-0.5 * np.log2(coarse / 125) ** 2)  # 174 plutôt que 87, 90 plutôt que 180
    best = coarse[np.argmax([score(b, 4) * p for b, p in zip(coarse, prior)])]
    fine = np.arange(best - 1, best + 1, 0.01)
    bpm = float(fine[np.argmax([score(b, 16) for b in fine])])
    return float(round(bpm)) if abs(bpm - round(bpm)) < 0.15 else round(bpm, 1)


def detect(path: Path, ffmpeg: str, duration: float | None = None) -> float | None:
    return estimate(decode_excerpt(path, ffmpeg, duration))
