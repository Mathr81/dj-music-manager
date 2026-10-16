"""Analyse spectrale locale : portage de whatsmybitrate v0.4 (wmb_core.py, © Oren Cohen, licence MIT).

Mêmes seuils et même logique de détection de transcode que l'original. Différences d'implémentation :
décodage par ffmpeg au lieu de librosa, STFT et fenêtre Blackman-Harris réécrites en numpy.
Le résultat `estimate` a le format de whatsmybitrate, qui est aussi celui de l'en-tête X-Real-Quality.
"""

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

LOSSLESS_CODECS = {"flac", "alac", "wavpack", "ape", "tak", "tta", "mlp", "truehd", "dsd_lsbf", "dsd_msbf"}
VBR_CODECS = {"vorbis", "opus", "aac", "mp4a", "he-aac", "eac3"}
VBR_SCAN_CODECS = {"vorbis", "opus", "aac", "mp4a", "he-aac"}
BRACKETS = [96, 128, 160, 192, 224, 256, 320]

TARGET_HZ_RESOLUTION = 5.0
RELATIVE_DB_THRESHOLD = -90.0
FALLBACK_DB_THRESHOLD = -75.0
GAP_HZ_THRESHOLD = 700.0

# pas de fenêtre console qui clignote à chaque appel depuis l'app graphique
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass
class Analysis:
    codec: str
    sample_rate: int
    stated_kbps: int | None
    max_frequency: float
    brickwall: bool
    estimate: str  # "Lossless", "LOSSY ~320kbps", "~192kbps (Transcoded to Lossless)", "~128kbps (Re-encoded)"
    lossless: bool


def is_lossless_codec(codec: str | None) -> bool:
    c = (codec or "").lower()
    return c in LOSSLESS_CODECS or c.startswith("pcm_")


def probe(path: Path, ffprobe: str) -> tuple[dict, dict]:
    out = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "a:0", "-show_entries",
         "stream=bit_rate,codec_name,duration,sample_rate,channels:stream_tags=BPS,BPS-eng:format=bit_rate,duration",
         "-of", "json", str(path)],
        capture_output=True, text=True, check=True, timeout=60, creationflags=_NO_WINDOW).stdout
    data = json.loads(out)
    return (data.get("streams") or [{}])[0], data.get("format", {})


def stated_kbps(path: Path, stream: dict, fmt: dict) -> int | None:
    """Débit annoncé : tag BPS, puis débit du flux, puis du conteneur, puis taille / durée."""
    tags = stream.get("tags", {})
    for value in (tags.get("BPS") or tags.get("BPS-eng"), stream.get("bit_rate"), fmt.get("bit_rate")):
        if value and value != "N/A":
            return int(value) // 1000
    duration = stream.get("duration") or fmt.get("duration")
    if duration and float(duration) > 0:
        return int(os.path.getsize(path) * 8 / float(duration) / 1000)
    return None


def decode(path: Path, ffmpeg: str, channels: int) -> np.ndarray:
    """Mono float32 à la fréquence d'origine (comme librosa.load(sr=None, mono=True) : moyenne des canaux)."""
    raw = subprocess.run([ffmpeg, "-v", "error", "-i", str(path), "-map", "0:a:0", "-f", "f32le", "-"],
                         capture_output=True, check=True, creationflags=_NO_WINDOW).stdout
    y = np.frombuffer(raw, np.float32)
    if channels > 1:
        y = y[: len(y) // channels * channels].reshape(-1, channels).mean(axis=1, dtype=np.float32)
    return y


def _blackman_harris(n: int) -> np.ndarray:
    """Équivalent de scipy.signal.windows.blackmanharris(n) (symétrique)."""
    a = 2 * np.pi * np.arange(n) / (n - 1)
    return 0.35875 - 0.48829 * np.cos(a) + 0.14128 * np.cos(2 * a) - 0.01168 * np.cos(3 * a)


def _power_spectrogram(y: np.ndarray, n_fft: int) -> np.ndarray:
    """|librosa.stft(y, n_fft, hop=n_fft//2, center=True, pad_mode='constant')|², par blocs pour la mémoire."""
    hop = n_fft // 2
    frames = np.lib.stride_tricks.sliding_window_view(np.pad(y, n_fft // 2), n_fft)[::hop]
    window = _blackman_harris(n_fft).astype(np.float32)
    out = np.empty((n_fft // 2 + 1, len(frames)), np.float32)
    for s in range(0, len(frames), 128):
        out[:, s:s + 128] = (np.abs(np.fft.rfft(frames[s:s + 128] * window, axis=1)) ** 2).T
    return out


def _to_db(psd: np.ndarray) -> np.ndarray:
    with np.errstate(divide="ignore"):
        return 10 * np.log10(psd / np.max(psd))


def _gap_max_freq(psd_db: np.ndarray, freqs: np.ndarray, threshold: float) -> tuple[float, np.ndarray]:
    """Fin du signal : premier trou > 700 Hz entre bins au-dessus du seuil, sinon dernier bin significatif."""
    sig = np.where(psd_db > threshold)[0]
    if sig.size == 0:
        return 0.0, sig
    gaps = np.where(np.diff(freqs[sig]) > GAP_HZ_THRESHOLD)[0]
    return float(freqs[sig[gaps[0]]] if gaps.size else freqs[sig[-1]]), sig


def analyze_spectrum(y: np.ndarray, sr: int, codec: str) -> tuple[float, bool]:
    """(fréquence de coupure en Hz, brickwall détecté)."""
    lossless = is_lossless_codec(codec)
    nperseg = 1 << (int(sr / TARGET_HZ_RESOLUTION) - 1).bit_length()
    if not sr or len(y) < nperseg:
        return 0.0, True

    peak = np.max(np.abs(y))
    if peak > np.finfo(np.float32).tiny:  # librosa.util.normalize
        y = y / peak
    s_power = _power_spectrogram(y, nperseg)
    psd = np.median(s_power, axis=1)
    freqs = np.fft.rfftfreq(nperseg, 1 / sr)
    if np.max(psd) <= 0:
        return 0.0, True
    psd_db = _to_db(psd)

    def idx(hz: float) -> int:
        return int(np.argmin(np.abs(freqs - hz)))

    hi_res = lossless and sr > 48000

    def clamp_hi_res(max_freq, sig):
        if hi_res and max_freq > 24000:
            below = sig[freqs[sig] < 24000]
            if below.size:
                return float(freqs[below[-1]])
        return max_freq

    max_freq, sig = _gap_max_freq(psd_db, freqs, RELATIVE_DB_THRESHOLD)
    max_freq = clamp_hi_res(max_freq, sig)
    if max_freq >= 21000 or max_freq / (sr / 2) > 0.99:
        max_freq, sig = _gap_max_freq(psd_db, freqs, FALLBACK_DB_THRESHOLD)
        max_freq = clamp_hi_res(max_freq, sig)

    # recherche d'une « falaise » (brickwall d'encodeur) sous la coupure
    vbr_scan = codec.lower() in VBR_SCAN_CODECS
    if vbr_scan:
        trigger, bottom = 15000.0, 10000.0
    elif lossless:
        trigger, bottom = 14000.0, 13000.0
    else:
        trigger, bottom = 21000.0, 15000.0

    if max_freq >= trigger:
        step = idx(1500.0)
        ceil = idx(22000.0)
        cliff_threshold = 18.0 if lossless else 15.0
        silence_margin = 12.0 if vbr_scan else 8.0
        best = 0.0
        for i in range(idx(min(max_freq, 21000.0)), idx(bottom), -1):
            start = max(0, i - step)
            if psd_db[start] - psd_db[i] > cliff_threshold:
                p90_above = np.percentile(psd_db[i:ceil + 1], 90) if i < ceil else -100.0
                if p90_above < psd_db[start] - silence_margin:
                    best = float(freqs[i])
                    if not vbr_scan:
                        break
        brickwall = best > 0.0
        if brickwall:
            max_freq = best
    else:
        drop = psd_db[idx(max(max_freq - 1500.0, 0))] - psd_db[idx(max_freq)]
        brickwall = drop >= 12.0 if lossless else not (drop < 15.0 and max_freq > 18500)

    # lossless sans falaise globale : recherche d'une coupure stable par tranches de 15 s
    if lossless and not brickwall:
        per_chunk = int(15.0 * sr / (nperseg / 2))
        n_frames = s_power.shape[1]
        if per_chunk > 0 and n_frames > per_chunk:
            cliffs = []
            for s in range(0, n_frames, per_chunk):
                e = min(s + per_chunk, n_frames)
                if e - s < per_chunk * 0.5:
                    continue
                chunk_psd = np.median(s_power[:, s:e], axis=1)
                if np.max(chunk_psd) <= 0:
                    continue
                f, chunk_sig = _gap_max_freq(_to_db(chunk_psd), freqs, -75.0)
                if chunk_sig.size and 10000.0 < f < 20500.0:
                    cliffs.append(f)
            clusters: dict[float, list[float]] = {}
            for f in cliffs:
                center = next((c for c in clusters if abs(f - c) < 300.0), None)
                clusters.setdefault(f if center is None else center, []).append(f)
            needed = max(2, min(3, (n_frames // per_chunk) // 4))
            best_center, votes = None, 0
            for center, fs in clusters.items():
                if len(fs) >= needed and len(fs) > votes:
                    best_center, votes = center, len(fs)
            if best_center is not None:
                max_freq = sum(clusters[best_center]) / len(clusters[best_center])
                brickwall = True

    return max_freq, brickwall


def classify(max_freq: float, brickwall: bool, codec: str, stated: int | None) -> tuple[str, bool]:
    """(estimation au format whatsmybitrate, vrai lossless)."""
    f = max_freq or 0.0
    bracket = 96 if f <= 15500 else 128 if f <= 16500 else 192 if f <= 18500 else 256 if f <= 19600 else 320
    if is_lossless_codec(codec):
        if brickwall:
            return f"~{bracket}kbps (Transcoded to Lossless)", False
        return "Lossless", True
    snapped = min(BRACKETS, key=lambda b: abs(b - stated)) if stated else None
    vbr = codec.lower() in VBR_CODECS
    if brickwall and snapped and snapped > bracket + (70 if vbr else 20):
        return f"~{bracket}kbps (Re-encoded)", False
    if snapped:
        kbps = bracket if (not vbr and not brickwall and bracket > snapped) else snapped
        return f"LOSSY ~{kbps}kbps", False
    return f"LOSSY ~{bracket}kbps", False


def analyze(path: Path, ffmpeg: str, ffprobe: str) -> Analysis:
    stream, fmt = probe(path, ffprobe)
    codec = stream.get("codec_name", "Unknown")
    sr = int(stream.get("sample_rate") or 0)
    stated = stated_kbps(path, stream, fmt)
    y = decode(path, ffmpeg, int(stream.get("channels") or 1))
    max_freq, brickwall = analyze_spectrum(y, sr, codec)
    estimate, lossless = classify(max_freq, brickwall, codec, stated)
    return Analysis(codec, sr, stated, max_freq, brickwall, estimate, lossless)
