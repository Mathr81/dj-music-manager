"""Remplacement d'un son par une meilleure version : même ID, playlists et tags suivent."""

from pathlib import Path

import pytest
from test_manual import FFMPEG, FFPROBE, make

from dj_music_manager.library import Library
from dj_music_manager.manual import add_file, replace_file, replace_warnings

pytestmark = pytest.mark.skipif(not (FFMPEG and FFPROBE), reason="ffmpeg absent")


@pytest.fixture
def env(tmp_path):
    raw = tmp_path / "RAW"
    raw.mkdir()
    lib = Library(tmp_path / "db")
    old = make(raw / "Artiste - Titre.mp3", isrc="FRXXX0000001", secs=12)  # MP3 128 par défaut
    _, track_id = add_file(lib, old, raw, FFMPEG, FFPROBE)
    lib.add_tags([track_id], ["house"])
    playlist_id = lib.create_playlist("Set")
    lib.add_to_playlist(playlist_id, [track_id])
    lib.add_external("deezer", "42", track_id)
    return lib, raw, tmp_path / "achats", track_id, playlist_id


def test_replace_keeps_id_and_everything_that_points_to_it(env):
    lib, raw, inbox, track_id, playlist_id = env
    before = lib.track(track_id)
    new = make(inbox / "Artiste - Titre.aiff", artist="", title="", secs=12)  # AIFF acheté, sans tags
    trashed = []

    dest = replace_file(lib, track_id, new, raw, FFMPEG, FFPROBE, trash=trashed.append)

    t = lib.track(track_id)
    assert dest == raw / "Artiste - Titre.aiff" and t["path"] == "Artiste - Titre.aiff"
    assert (t["format"], t["badge"], t["quality_origin"], t["source"]) == ("aiff", "Lossless", "local", "manual")
    assert (t["artist"], t["title"], t["isrc"], t["added_at"]) == (  # métadonnées connues conservées
        "Artiste", "Titre", "FRXXX0000001", before["added_at"])
    assert trashed == [str(raw / "Artiste - Titre.mp3")]  # corbeille, pas de suppression définitive
    assert new.exists()  # le fichier acheté n'est pas déplacé
    assert lib.playlist_track_ids(playlist_id) == [track_id]
    assert lib.tags_by_track()[track_id] == ["house"]
    assert lib.track_by_external("deezer", "42") == track_id
    assert len(lib.tracks()) == 1


def test_same_file_name_does_not_leave_a_numbered_copy(env):
    lib, raw, inbox, track_id, _ = env
    new = make(inbox / "Artiste - Titre.mp3", isrc="FRXXX0000001", secs=12)
    replace_file(lib, track_id, new, raw, FFMPEG, FFPROBE, trash=lambda p: Path(p).unlink())
    assert lib.track(track_id)["path"] == "Artiste - Titre.mp3"
    assert [p.name for p in raw.iterdir()] == ["Artiste - Titre.mp3"]


def test_warnings_before_replacing_with_the_wrong_file(env):
    lib, raw, inbox, track_id, _ = env
    other = make(inbox / "autre.flac", isrc="FRXXX0000002", artist="Autre", title="Son", secs=30)
    add_file(lib, other, raw, FFMPEG, FFPROBE)
    warnings = replace_warnings(lib, track_id, other)
    assert any("Autre – Son" in w for w in warnings) and any("Durée différente" in w for w in warnings)
    assert replace_warnings(lib, track_id, make(inbox / "ok.flac", isrc="FRXXX0000001", secs=12)) == []


def test_refuses_another_tracks_file(env):
    lib, raw, inbox, track_id, _ = env
    _, other_id = add_file(lib, make(raw / "b.flac", isrc="FRXXX0000003", title="B"), raw, FFMPEG, FFPROBE)
    with pytest.raises(ValueError, match="autre son"):
        replace_file(lib, track_id, raw / "b.flac", raw, FFMPEG, FFPROBE, trash=lambda p: None)
    with pytest.raises(ValueError, match="déjà le fichier"):
        replace_file(lib, track_id, raw / "Artiste - Titre.mp3", raw, FFMPEG, FFPROBE, trash=lambda p: None)
