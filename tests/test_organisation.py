"""Tags, playlists manuelles et intelligentes."""

import pytest

from dj_music_manager.library import Library
from dj_music_manager.smart import where


@pytest.fixture
def lib(tmp_path):
    lib = Library(tmp_path / "db")
    for i, (artist, title, bpm, badge) in enumerate([
        ("Daft Punk", "One More Time", 123, "Lossless"),
        ("Daft Punk", "Aerodynamic", 123, "320"),
        ("Stromae", "Papaoutai", 116, "Faux FLAC"),
        ("Fred again..", "Delilah (100%_mix)", 140, "320"),
        ("Inconnu", "Sans BPM", None, None),
    ]):
        lib.add_track({"path": f"{i}.mp3", "artist": artist, "title": title, "bpm": bpm, "badge": badge,
                       "duration": 200 + i * 10, "source": "manual"})
    return lib


def ids(lib, *titles):
    return [t["id"] for title in titles for t in lib.tracks() if t["title"] == title]


def test_tags_are_free_and_case_insensitive(lib):
    a, b = ids(lib, "One More Time", "Papaoutai")
    lib.add_tags([a, b], ["House", " warmup ", ""])
    lib.add_tags([a], ["house", "Énergie 8"])
    assert lib.tags() == ["Énergie 8", "House", "warmup"]
    assert lib.tags_by_track()[a] == ["Énergie 8", "House", "warmup"]
    lib.remove_tag([a, b], "WARMUP")
    assert "warmup" not in lib.tags()  # tag orphelin supprimé
    assert lib.tags_by_track()[b] == ["House"]


def test_manual_playlist_keeps_order_and_only_references(lib):
    a, b, c = ids(lib, "One More Time", "Aerodynamic", "Papaoutai")
    pid = lib.create_playlist("Warmup")
    lib.add_to_playlist(pid, [c, a])
    lib.add_to_playlist(pid, [b, a])  # a déjà présent : garde sa place
    assert lib.playlist_track_ids(pid) == [c, a, b]
    lib.remove_from_playlist(pid, [a])
    assert lib.playlist_track_ids(pid) == [c, b]
    lib.delete_playlist(pid)
    assert lib.playlist(pid) is None and len(lib.tracks()) == 5  # les sons restent


def test_smart_playlist_rules(lib):
    a, b, c, d = ids(lib, "One More Time", "Aerodynamic", "Papaoutai", "Delilah (100%_mix)")
    lib.add_tags([a, c], ["house"])
    pid = lib.create_playlist("Peak house", {"match": "all", "rules": [
        {"field": "tag", "op": "has", "value": "HOUSE"},
        {"field": "bpm", "op": ">=", "value": "120"},
        {"field": "badge", "op": "is_not", "value": "faux flac"},
    ]})
    assert lib.playlist_track_ids(pid) == [a]
    lib.add_tags([b], ["house"])  # évaluée à la volée
    assert lib.playlist_track_ids(pid) == [b, a]  # triée par artiste puis titre

    lib.update_playlist(pid, "Any", {"match": "any", "rules": [
        {"field": "artist", "op": "contains", "value": "stro"},
        {"field": "bpm", "op": "<=", "value": 116.5},
    ]})
    assert lib.playlist(pid)["name"] == "Any" and lib.playlist_track_ids(pid) == [c]


@pytest.mark.parametrize("value, expected", [("100%_", True), ("0%x", False), ("'; DROP TABLE tracks; --", False)])
def test_contains_treats_wildcards_and_quotes_literally(lib, value, expected):
    pid = lib.create_playlist("x", {"rules": [{"field": "title", "op": "contains", "value": value}]})
    assert bool(lib.playlist_track_ids(pid)) is expected
    assert len(lib.tracks()) == 5


def test_invalid_rules_are_rejected(lib):
    with pytest.raises(KeyError):
        where({"rules": [{"field": "path; DROP", "op": "is", "value": "x"}]})
    with pytest.raises(ValueError):
        lib.create_playlist("x", {"rules": [{"field": "bpm", "op": "contains", "value": "1"}]})
    with pytest.raises(ValueError):
        where({"rules": [{"field": "bpm", "op": ">=", "value": "rapide"}]})
    assert where({"rules": []}) == ("1", [])
