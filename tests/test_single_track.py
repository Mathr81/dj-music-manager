"""Un titre seul depuis la barre d'import : lien Spotify ou Deezer, ou recherche libre."""

import httpx
import pytest

from dj_music_manager import importer
from dj_music_manager.deezer import DeezerPublic
from dj_music_manager.library import Library

SP_ID = "2dvChuAwfrlqZdJumaeATh"


@pytest.mark.parametrize("text, link", [
    ("qu'on s'attache", False),
    ("Daft Punk - One More Time", False),
    (f"https://open.spotify.com/intl-fr/track/{SP_ID}?si=fe4d25ce7cdc497b", True),
    (f"spotify:track:{SP_ID}", True),
    ("www.deezer.com/fr/track/3135553", True),
])
def test_link_or_search(text, link):
    assert importer.is_link(text) is link


def deezer(handler=None) -> DeezerPublic:
    return DeezerPublic(transport=httpx.MockTransport(handler or (lambda r: pytest.fail("Deezer inutile ici"))))


def test_spotify_track_link_queues_one_job_without_playlist(tmp_path, monkeypatch):
    lib = Library(tmp_path / "db")
    monkeypatch.setattr(importer, "public_track", lambda track_id: {
        "id": track_id, "artist": "Vacra", "title": "Qu'on s'attache", "duration": 180.0,
        "url": f"https://open.spotify.com/track/{track_id}", "isrc": None})

    p = importer.track_preview(deezer(), lib, f"https://open.spotify.com/intl-fr/track/{SP_ID}?si=x")
    assert (p.single, p.provider, p.title, p.existing) == (True, "spotify", "Vacra – Qu'on s'attache", {})
    [job_id] = importer.queue(lib, p, "mp3_320")
    job = lib.job(job_id)
    assert (job["playlist_id"], job["external_id"], job["url"]) == (None, SP_ID, f"https://open.spotify.com/track/{SP_ID}")
    assert lib.playlists() == []


def test_deezer_track_link_already_in_library(tmp_path):
    lib = Library(tmp_path / "db")
    known, _ = lib.add_track({"path": "a.mp3", "artist": "Daft Punk", "title": "One More Time", "duration": 320.0,
                              "isrc": "GBDUW0000053", "source": "manual"})
    api = deezer(lambda r: httpx.Response(200, json={
        "id": 3135553, "title": "One More Time (Radio Edit)", "duration": 230, "isrc": "GBDUW0000053",
        "artist": {"name": "Daft Punk"}}))
    p = importer.track_preview(api, lib, "https://www.deezer.com/fr/track/3135553")
    assert p.existing == {"3135553": known}  # reconnu par l'ISRC malgré la durée


def test_search_result_and_playlist_links(tmp_path):
    lib = Library(tmp_path / "db")
    chosen = {"id": "69879250", "artist": "Stromae", "title": "Papaoutai", "duration": "3:52",
              "url": "https://www.deezer.com/track/69879250"}
    p = importer.track_preview(deezer(lambda r: httpx.Response(200, json={"id": 69879250})), lib, "papaoutai",
                               chosen=chosen)
    assert (p.provider, p.tracks[0]["duration"], p.new[0]["id"]) == ("deezer", 232, "69879250")
    assert importer.track_preview(deezer(), lib, "https://www.deezer.com/fr/playlist/9") is None  # → aperçu playlist
