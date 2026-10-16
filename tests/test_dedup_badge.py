import pytest

from dj_music_manager.dedup import norm_key
from dj_music_manager.quality.badge import badge


@pytest.mark.parametrize("artist, title, expected", [
    ("Daft Punk", "One More Time", "daft punk|one more time"),
    ("Daft Punk feat. Romanthony", "One More Time (Original Mix)", "daft punk|one more time"),
    ("DAFT PUNK, Romanthony", "One More Time - Original Mix", "daft punk|one more time"),
    ("Stromaé", "Papaoutai (feat. X) [Extended Mix]", "stromae|papaoutai extended mix"),
    ("Artist", "Title ft. Someone (Radio Edit)", "artist|title radio edit"),
    ("Malaa x Tchami", "Zoo", "malaa|zoo"),
    ("Florence + The Machine", "Dog Days Are Over!", "florence the machine|dog days are over"),
    ("Artist", "The Feather", "artist|the feather"),
])
def test_norm_key(artist, title, expected):
    assert norm_key(artist, title) == expected


@pytest.mark.parametrize("real, lossless, fmt, expected", [
    ("Lossless", True, "flac", "Lossless"),
    ("LOSSY ~320kbps", False, "mp3", "320"),
    ("LOSSY ~256kbps", False, "mp3", "256"),
    ("LOSSY ~224kbps", False, "mp3", "192"),
    ("LOSSY ~96kbps", False, "mp3", "128"),
    ("~192kbps (Transcoded to Lossless)", False, "flac", "Faux FLAC"),
    ("~128kbps (Re-encoded)", False, "mp3", "Transcode"),
    (None, False, "flac", "Faux FLAC"),
    ("unknown", None, "m4a", None),
])
def test_badge(real, lossless, fmt, expected):
    assert badge(real, lossless, fmt) == expected
