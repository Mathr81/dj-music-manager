"""Analyse locale sur des signaux synthétiques (la validation contre l'original : tools/compare_whatsmybitrate.py)."""

import shutil
import subprocess

import pytest

from dj_music_manager.quality.analysis import analyze, classify
from dj_music_manager.quality.badge import badge

FFMPEG, FFPROBE = shutil.which("ffmpeg"), shutil.which("ffprobe")
needs_ffmpeg = pytest.mark.skipif(not (FFMPEG and FFPROBE), reason="ffmpeg absent")


@pytest.fixture(scope="module")
def files(tmp_path_factory):
    d = tmp_path_factory.mktemp("q")

    def ff(*args):
        subprocess.run([FFMPEG, "-v", "error", "-y", *args], check=True)

    ff("-f", "lavfi", "-i", "anoisesrc=color=pink:duration=20:sample_rate=44100", "-ac", "2", str(d / "real.flac"))
    ff("-i", str(d / "real.flac"), "-c:a", "libmp3lame", "-b:a", "128k", str(d / "128.mp3"))
    ff("-i", str(d / "128.mp3"), "-c:a", "flac", str(d / "fake.flac"))
    ff("-i", str(d / "128.mp3"), "-c:a", "libmp3lame", "-b:a", "320k", str(d / "reencoded.mp3"))
    return d


@needs_ffmpeg
@pytest.mark.parametrize("name, expected_badge", [
    ("real.flac", "Lossless"),
    ("128.mp3", "128"),
    ("fake.flac", "Faux FLAC"),
    ("reencoded.mp3", "Transcode"),
])
def test_badges_on_synthetic_files(files, name, expected_badge):
    a = analyze(files / name, FFMPEG, FFPROBE)
    assert badge(a.estimate, a.lossless, name.rsplit(".", 1)[1]) == expected_badge


@pytest.mark.parametrize("max_freq, brickwall, codec, stated, expected", [
    (22050, False, "flac", 900, ("Lossless", True)),
    (16900, True, "flac", 900, ("~192kbps (Transcoded to Lossless)", False)),
    (16900, True, "mp3", 320, ("~192kbps (Re-encoded)", False)),
    (20200, False, "mp3", 320, ("LOSSY ~320kbps", False)),
    (20200, False, "mp3", 128, ("LOSSY ~320kbps", False)),  # spectre plus riche que le débit annoncé
    (22050, True, "aac", 256, ("LOSSY ~256kbps", False)),  # VBR : marge de 70 kbps
    (15000, False, "mp3", None, ("LOSSY ~96kbps", False)),
])
def test_classify_matches_whatsmybitrate_rules(max_freq, brickwall, codec, stated, expected):
    assert classify(max_freq, brickwall, codec, stated) == expected
