"""Tonalité : détection (chromagramme + profils de Krumhansl-Kessler), lecture des tags, notation Camelot.

La tonalité est rangée sous forme musicale courte (« Am », « F# », « Bbm ») ; l'affichage utilise Camelot (8A, 2B…),
la roue des DJ : deux sons sont compatibles s'ils ont le même numéro, un numéro voisin, ou la même lettre inversée.
"""

import re

import numpy as np

SR = 11025
N_FFT = 8192  # 1,3 Hz par case : de quoi séparer les demi-tons dès 55 Hz
HOP = 4096
NOTES = ("C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B")
MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
# Camelot : C majeur = 8B, A mineur = 8A ; +1 à chaque quinte (7 demi-tons)
CAMELOT = {**{f"{NOTES[(i * 7) % 12]}": f"{(7 + i) % 12 + 1}B" for i in range(12)},
           **{f"{NOTES[(9 + i * 7) % 12]}m": f"{(7 + i) % 12 + 1}A" for i in range(12)}}
FROM_CAMELOT = {v: k for k, v in CAMELOT.items()}
_ENHARMONIC = {"C#": "Db", "D#": "Eb", "Gb": "F#", "G#": "Ab", "A#": "Bb", "Cb": "B", "Fb": "E", "E#": "F", "B#": "C"}
_TAG_RE = re.compile(r"^\s*([A-Ga-g])\s*([#b♯♭]?)\s*(m|min|minor|maj|major|dur|moll)?\s*$", re.I)
# Open Key (Traktor) : 1d = C majeur, 1m = A mineur, +1 à chaque quinte
_OPEN_KEY = re.compile(r"^\s*(\d{1,2})\s*([dm])\s*$", re.I)


def chroma(y: np.ndarray) -> np.ndarray:
    """Profil des 12 classes de hauteur (do à si), notes de 55 Hz à 2 kHz.

    Par trame : crête de chaque note (le nombre de cases FFT par demi-ton croît avec la fréquence, une somme
    favoriserait le haut de chaque octave), moins la médiane des notes (bruit large bande : batterie), puis
    normalisation (une trame forte ne pèse pas plus qu'une douce)."""
    n = 1 + (len(y) - N_FFT) // HOP
    if n < 4:
        return np.zeros(12)
    frames = np.lib.stride_tricks.as_strided(y, (n, N_FFT), (y.strides[0] * HOP, y.strides[0]))
    mag = np.log1p(1000 * np.abs(np.fft.rfft(frames * np.hanning(N_FFT), axis=1)))
    freqs = np.fft.rfftfreq(N_FFT, 1 / SR)
    band = (freqs >= 52) & (freqs <= 2100)
    midi = np.round(12 * np.log2(freqs[band] / 440) + 69).astype(int)
    notes = np.arange(33, 97)  # la 55 Hz à do 2093 Hz
    peaks = np.stack([mag[:, band][:, midi == m].max(axis=1) for m in notes], axis=1)
    peaks = np.maximum(0, peaks - np.median(peaks, axis=1, keepdims=True))
    peaks /= np.maximum(peaks.sum(axis=1, keepdims=True), 1e-9)
    out = np.bincount(notes % 12, weights=peaks.sum(axis=0), minlength=12)
    return out / (out.sum() or 1)


def estimate(y: np.ndarray) -> str | None:
    c = chroma(y)
    if not c.any():
        return None
    best, name = -2.0, None
    for tonic in range(12):
        for profile, suffix in ((MAJOR, ""), (MINOR, "m")):
            r = np.corrcoef(c, np.roll(profile, tonic))[0, 1]
            if r > best:
                best, name = r, NOTES[tonic] + suffix
    return name


def parse(text: str | None) -> str | None:
    """Tag de tonalité (« Am », « A minor », « 8A », « 1m »…) -> forme courte, ou None si illisible."""
    if not text:
        return None
    t = text.strip()
    if t.upper() in FROM_CAMELOT:
        return FROM_CAMELOT[t.upper()]
    if m := _OPEN_KEY.match(t):
        number, mode = int(m[1]), m[2].lower()
        if 1 <= number <= 12:  # Open Key n = Camelot n+7
            return FROM_CAMELOT[f"{(number + 6) % 12 + 1}{'B' if mode == 'd' else 'A'}"]
        return None
    if not (m := _TAG_RE.match(t)):
        return None
    note = m[1].upper() + {"♯": "#", "♭": "b"}.get(m[2], m[2])
    note = _ENHARMONIC.get(note, note)
    minor = (m[3] or "").lower() in ("m", "min", "minor", "moll")
    return note + ("m" if minor else "") if note in NOTES else None


def camelot(key: str | None) -> str:
    return CAMELOT.get(key or "", "")


def camelot_sort(key: str | None) -> float:
    """Ordre de la roue : 1A, 1B, 2A… (les sons sans tonalité à la fin)."""
    code = camelot(key)
    return int(code[:-1]) + (0.5 if code.endswith("B") else 0) if code else 99


def compatible(key: str | None) -> set[str]:
    """Tonalités qui se mixent avec celle-ci : même case, ±1 sur la roue, relative (A <-> B)."""
    code = camelot(key)
    if not code:
        return set()
    number, letter = int(code[:-1]), code[-1]
    codes = {code, f"{number % 12 + 1}{letter}", f"{(number - 2) % 12 + 1}{letter}",
             f"{number}{'A' if letter == 'B' else 'B'}"}
    return {FROM_CAMELOT[c] for c in codes}
