"""Pause / annulation de la file, journal des erreurs de l'API, playlists en double par lien de partage."""

import logging
import shutil
import subprocess
import threading

import httpx
import pytest

from dj_music_manager.api_client import ApiClient, ApiError
from dj_music_manager.downloader import Downloader
from dj_music_manager.importer import canonical_source
from dj_music_manager.library import Library

SECRET = "cle-tres-secrete"


def client(handler) -> ApiClient:
    return ApiClient(SECRET, base_url="https://test", transport=httpx.MockTransport(handler))


@pytest.mark.parametrize("url", [
    "https://open.spotify.com/playlist/35Izkr0GbGBWHszIvqJItZ?si=f45e0e7750d74a08",
    "https://open.spotify.com/intl-fr/playlist/35Izkr0GbGBWHszIvqJItZ?si=2d6b6276881e40fe",
    "spotify:playlist:35Izkr0GbGBWHszIvqJItZ",
])
def test_canonical_spotify_source(url):
    assert canonical_source(url) == "https://open.spotify.com/playlist/35Izkr0GbGBWHszIvqJItZ"


def test_canonical_deezer_source():
    assert canonical_source("https://www.deezer.com/fr/album/302127?utm=x") == "https://www.deezer.com/album/302127"


def test_merge_playlists_imported_from_two_share_links(tmp_path):
    """Le cas réel : « ÉTÉ 2K26 » importée deux fois, liens différents par leur « ?si=… »."""
    lib = Library(tmp_path / "db")
    ids = [lib.add_track({"path": f"{i}.mp3", "title": str(i), "source": "spotify"})[0] for i in range(5)]
    first = lib.playlist_for_source("https://open.spotify.com/playlist/35Izkr0GbGBWHszIvqJItZ?si=aaa", "ÉTÉ 2K26")
    lib.set_playlist_tracks(first, [(ids[0], 0), (ids[1], 1), (ids[4], 2)])  # 1er import, arrêté en cours
    second = lib.playlist_for_source("https://open.spotify.com/playlist/35Izkr0GbGBWHszIvqJItZ?si=bbb", "ÉTÉ 2K26")
    lib.set_playlist_tracks(second, [(ids[0], 0), (ids[1], 1), (ids[2], 2), (ids[3], 3)])
    [failed] = lib.add_jobs([{"playlist_id": second, "provider": "spotify", "title": "x", "quality": "flac"}])

    assert lib.merge_source_duplicates(canonical_source) == 1
    [p] = lib.playlists()
    assert (p["id"], p["source_url"]) == (first, "https://open.spotify.com/playlist/35Izkr0GbGBWHszIvqJItZ")
    assert lib.playlist_track_ids(first) == [ids[0], ids[1], ids[2], ids[3], ids[4]]  # dernier import + reste
    assert lib.job(failed)["playlist_id"] == first
    assert lib.merge_source_duplicates(canonical_source) == 0  # idempotent
    # un nouvel import avec encore un autre lien de partage retombe sur la même playlist
    assert lib.playlist_for_source(canonical_source(
        "https://open.spotify.com/playlist/35Izkr0GbGBWHszIvqJItZ?si=ccc"), "ÉTÉ 2K26") == first


@pytest.fixture
def lib(tmp_path):
    lib = Library(tmp_path / "db")
    (tmp_path / "RAW").mkdir()
    lib.set_setting("raw_dir", str(tmp_path / "RAW"))
    return lib


def test_pause_then_resume(lib):
    started = threading.Event()

    def handler(request):
        started.set()
        return httpx.Response(422, json={"detail": "Track not found"})

    d = Downloader(client(handler), lib)
    d.pause()
    [job] = lib.add_jobs([{"provider": "deezer", "external_id": "1", "title": "t", "quality": "flac"}])
    d.submit([job])
    assert not started.wait(1.0)  # rien ne part pendant la pause
    d.resume()
    d.close(wait=True)
    assert started.is_set() and lib.job(job)["status"] == "failed"


def test_cancelled_jobs_never_start(lib):
    d = Downloader(client(lambda r: pytest.fail("job annulé : aucune requête")), lib)
    d.pause()
    jobs = lib.add_jobs([{"provider": "deezer", "external_id": str(i), "title": "t", "quality": "flac"}
                         for i in range(3)])
    d.submit(jobs)
    d.cancel(jobs)
    lib.delete_jobs(jobs)
    d.resume()
    d.close(wait=True)
    assert all(lib.job(j) is None for j in jobs)


def test_api_errors_are_logged_without_the_key(tmp_path, caplog):
    def handler(request):
        if request.url.path == "/download":
            return httpx.Response(422, json={"detail": "@MusicsHuntersbot ne répond plus,\nréessai dans 797 s"})
        raise httpx.ReadTimeout("timed out", request=request)

    api = client(handler)
    with caplog.at_level(logging.WARNING, logger="dj_music_manager.api"):
        with pytest.raises(ApiError):
            api.download(tmp_path, "flac", id="3135553")
        with pytest.raises(ApiError, match="réseau"):
            api.search("daft punk")
    lines = [r.getMessage() for r in caplog.records]
    assert lines[0].startswith("/download?id=3135553&quality=flac | 422 en ")
    assert lines[0].endswith("@MusicsHuntersbot ne répond plus, réessai dans 797 s")  # une ligne par incident
    assert lines[1].startswith("/search?q=daft punk | erreur réseau en ") and "ReadTimeout" in lines[1]
    assert all(SECRET not in line for line in lines)


def test_incomplete_collection_and_wrong_file_are_logged(lib, caplog):
    from dj_music_manager.importer import Preview
    p = Preview("https://www.deezer.com/album/1", "Album", [
        {"id": "1", "artist": "A", "title": "SPA", "duration": 1.0, "url": None, "isrc": None},
        {"id": "2", "artist": "B", "title": "Absente", "duration": 1.0, "url": None, "isrc": None}])
    api = client(lambda r: httpx.Response(200, json={"tracks": [
        {"file": "/file/bot/7", "artist": "A", "title": "SPA", "duration": "0:01"}]}))
    jobs = lib.add_jobs([{"provider": "deezer", "external_id": i, "title": t, "quality": "flac"}
                         for i, t in (("1", "SPA"), ("2", "Absente"))])
    with caplog.at_level(logging.WARNING, logger="dj_music_manager.api"):
        d = Downloader(api, lib)
        d.pause()  # on ne teste que le rapprochement et le journal, pas les téléchargements
        d.submit_collection(p, "flac", jobs).join()
        d.close()
    message = next(r.getMessage() for r in caplog.records if "pistes attendues" in r.getMessage())
    assert "1 fichier(s) pour 2 pistes attendues" in message and "B – Absente" in message


def test_wrong_file_is_logged_with_its_source(lib, caplog, tmp_path):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg absent")
    flac = tmp_path / "x.flac"
    subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i", "sine=duration=1", "-metadata", "artist=Dj Blax",
                    "-metadata", "title=Bazardée (Mashup Remix)", str(flac)], check=True)
    api = client(lambda r: httpx.Response(200, content=flac.read_bytes(), headers={
        "content-disposition": 'attachment; filename="x.flac"', "x-bot": "MusicsHuntersbot"}))
    [job] = lib.add_jobs([{"provider": "deezer", "external_id": "1", "artist": "Damso", "title": "SPA",
                           "quality": "flac", "file": "/file/MusicsHuntersbot/4801"}])
    with caplog.at_level(logging.WARNING, logger="dj_music_manager.api"):
        d = Downloader(api, lib)
        d.submit([job])
        d.close(wait=True)
    [message] = [r.getMessage() for r in caplog.records]
    assert message == ("/file/MusicsHuntersbot/4801 | fichier inattendu (bot MusicsHuntersbot) | "
                       "attendu « Damso – SPA » | reçu « Dj Blax – Bazardée (Mashup Remix) »")
