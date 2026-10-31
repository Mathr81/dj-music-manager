"""BPM sur des boucles synthétiques (grosse caisse + charleston + bruit), dont des tempos non entiers."""

import numpy as np
import pytest

from dj_music_manager.bpm import SR, estimate


def loop(bpm: float, seconds: float = 60, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    t = np.arange(int(SR * seconds)) / SR
    y = 0.05 * rng.standard_normal(len(t))
    beat = 60 / bpm
    kick = np.sin(2 * np.pi * 55 * np.arange(int(0.12 * SR)) / SR) * np.exp(-np.arange(int(0.12 * SR)) / 300)
    hat = rng.standard_normal(int(0.03 * SR)) * np.exp(-np.arange(int(0.03 * SR)) / 60) * 0.3
    for i in range(int(seconds / beat)):
        s = int(i * beat * SR)
        y[s:s + len(kick)] += kick[: len(y) - s]
        h = int((i + 0.5) * beat * SR)  # contretemps
        y[h:h + len(hat)] += hat[: max(0, len(y) - h)]
    return y.astype(np.float32)


@pytest.mark.parametrize("bpm", [90, 122, 124, 128, 140, 174, 126.5])
def test_estimate_tempo(bpm):
    assert estimate(loop(bpm)) == pytest.approx(bpm, abs=0.3)


def test_silence_and_short_clip():
    assert estimate(np.zeros(SR * 30, np.float32)) is None
    assert estimate(loop(120, seconds=5)) is None
