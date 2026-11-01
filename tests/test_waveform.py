import numpy as np

from dj_music_manager.waveform import SR, analyze


def tone(hz: float, seconds: float, amp: float = 1.0) -> np.ndarray:
    return (amp * np.sin(2 * np.pi * hz * np.arange(int(SR * seconds)) / SR)).astype(np.float32)


def test_each_band_follows_its_own_part():
    y = np.concatenate([tone(60, 10), tone(1000, 10), tone(3500, 10), tone(60, 10, 0.5)])
    levels = analyze(y, buckets=40)
    assert levels.shape == (40, 3)
    assert levels[:10, 0].min() > 0.9 and levels[:10, 1:].max() < 0.1  # grave seul
    assert levels[10:20, 1].min() > 0.9 and levels[10:20, [0, 2]].max() < 0.1  # médium seul
    assert levels[20:30, 2].min() > 0.9 and levels[20:30, :2].max() < 0.1  # aigu seul
    assert 0.4 < levels[30:, 0].mean() < 0.6  # grave deux fois moins fort


def test_too_short_or_silent():
    assert not analyze(np.zeros(100, np.float32)).any()
    assert not analyze(np.zeros(SR * 10, np.float32), buckets=10).any()  # silence : pas de division par zéro
