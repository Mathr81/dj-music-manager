"""Tonalité sur des progressions d'accords synthétiques (sons riches en harmoniques), tags et roue Camelot."""

import numpy as np
import pytest

from dj_music_manager.key import CAMELOT, SR, camelot, camelot_sort, compatible, estimate, parse

MIDI = {n: i for i, n in enumerate(("C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"))}


def chord(notes: list[int], seconds: float) -> np.ndarray:
    t = np.arange(int(SR * seconds)) / SR
    y = np.zeros_like(t)
    for midi in notes:
        f = 440 * 2 ** ((midi - 69) / 12)
        for h in range(1, 6):  # harmoniques décroissantes, comme un vrai instrument
            y += np.sin(2 * np.pi * f * h * t) / h
    return y


def progression(tonic: str, minor: bool) -> np.ndarray:
    root = 48 + MIDI[tonic]
    third = 3 if minor else 4
    # i - iv - v - i (mineur) ou I - IV - V - I (majeur), basse comprise
    degrees = [(0, third, 7), (5, 5 + third, 12), (7, 7 + 4, 14), (0, third, 7)]
    parts = [chord([root - 12 + d[0]] + [root + x for x in d], 4) for d in degrees] * 3
    return (np.concatenate(parts) / 10).astype(np.float32)


@pytest.mark.parametrize("tonic, minor", [("A", True), ("C", False), ("F#", True), ("Eb", False), ("G", True),
                                           ("D", False)])
def test_estimate_key(tonic, minor):
    assert estimate(progression(tonic, minor)) == tonic + ("m" if minor else "")


def test_silence():
    assert estimate(np.zeros(SR * 30, np.float32)) is None


@pytest.mark.parametrize("tag, key", [("Am", "Am"), ("A minor", "Am"), ("a min", "Am"), ("C#", "Db"),
                                      ("C# Major", "Db"), ("Bbm", "Bbm"), ("A#m", "Bbm"), ("8A", "Am"),
                                      ("8B", "C"), ("12a", "Dbm"), ("1d", "C"), ("1m", "Am"), ("F♯m", "F#m"),
                                      ("", None), ("H", None), ("13A", None), ("Unknown", None)])
def test_parse_tags(tag, key):
    assert parse(tag) == key


def test_camelot_wheel():
    assert len(CAMELOT) == 24 and len(set(CAMELOT.values())) == 24
    assert camelot("Am") == "8A" and camelot("C") == "8B" and camelot("G") == "9B" and camelot("Ebm") == "2A"
    assert camelot(None) == ""
    assert compatible("Am") == {"Am", "Em", "Dm", "C"}  # 8A : 9A, 7A, 8B
    assert compatible("B") == {"B", "F#", "E", "Abm"}  # 1B : 2B, 12B, 1A
    assert sorted(["C", "Am", "Dbm", None], key=camelot_sort) == ["Am", "C", "Dbm", None]
