import pytest

from dj_music_manager.dedup import norm_key, titles_match
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


@pytest.mark.parametrize("expected, got, same", [
    ("One More Time", "One More Time", True),
    ("One More Time", "One More Time (Radio Edit)", True),
    ("Fille à Papa", "Fille a papa", True),
    ("Delilah (pull me out of this)", "Delilah", True),
    ("Song feat. X", "Song", True),
    ("Voyager", "Veridis Quo", False),
    ("Un", None, True),  # fichier sans titre : rien à comparer
])
def test_titles_match(expected, got, same):
    assert titles_match(expected, got) is same


def test_wrong_isrc_from_the_api_does_not_merge_different_songs(tmp_path, caplog):
    """Cas réel (bot de secours) : « Voyager » étiqueté avec l'ISRC de « Something About Us »."""
    import logging

    from dj_music_manager.library import Library
    lib = Library(tmp_path / "db")
    about_us, _ = lib.add_track({"path": "a.mp3", "artist": "Daft Punk", "title": "Something About Us",
                                 "isrc": "GBDUW0000065", "duration": 233.0, "source": "deezer"})
    with caplog.at_level(logging.WARNING, logger="dj_music_manager.api"):
        voyager, created = lib.add_track({"path": "v.mp3", "artist": "Daft Punk", "title": "Voyager",
                                          "isrc": "GBDUW0000065", "duration": 227.0, "source": "deezer"})
    assert created and voyager != about_us
    assert "ISRC GBDUW0000065 porté par « Daft Punk – Something About Us » et par « Daft Punk – Voyager »" \
        in caplog.text
    # même ISRC et même titre : c'est bien un doublon
    assert lib.find_track("GBDUW0000065", "Daft Punk", "Something About Us (Radio Edit)", None) == about_us
