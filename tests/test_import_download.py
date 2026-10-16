"""Parcours complet : aperçu -> téléchargement -> dédoublonnage -> réimport, API simulées."""

import shutil
import subprocess

import httpx
import pytest

from dj_music_manager import importer
from dj_music_manager.api_client import ApiClient
from dj_music_manager.deezer import DeezerPublic
from dj_music_manager.downloader import Downloader
from dj_music_manager.library import Library

PLAYLIST = "https://www.deezer.com/fr/playlist/9"
TRACKS = [
    {"id": "1", "artist": "Daft Punk", "title": "One More Time", "duration": "0:02", "url": "https://www.deezer.com/track/1"},
    # même enregistrement sous un autre ID Deezer (single vs album) : même ISRC
    {"id": "2", "artist": "Daft Punk", "title": "One More Time", "duration": "0:02", "url": "https://www.deezer.com/track/2"},
    {"id": "3", "artist": "Nobody", "title": "Missing", "duration": "3:00", "url": "https://www.deezer.com/track/3"},
]


@pytest.fixture(scope="module")
def flac(tmp_path_factory) -> bytes:
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg absent")
    p = tmp_path_factory.mktemp("fixture") / "src.flac"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                    "-metadata", "artist=Daft Punk", "-metadata", "title=One More Time", "-metadata", "album=Discovery",
                    "-metadata", "ISRC=GBDUW0000053", "-metadata", "BPM=122.7", str(p)], check=True)
    return p.read_bytes()


@pytest.fixture
def env(tmp_path, flac):
    raw = tmp_path / "RAW"
    raw.mkdir()
    lib = Library(tmp_path / "library.db")
    lib.set_setting("raw_dir", str(raw))
    downloads = []

    def api_handler(request):
        if request.url.path == "/tracks":
            return httpx.Response(200, json=TRACKS)
        track_id = request.url.params["id"]
        downloads.append(track_id)
        if track_id == "3":
            return httpx.Response(422, json={"detail": "Track not found or unavailable.\nDebugID: 1"})
        return httpx.Response(200, content=flac, headers={
            "content-disposition": "attachment; filename=\"Daft Punk - One More Time.flac\"",
            "x-real-quality": "Lossless", "x-lossless": "true", "x-max-frequency": "21000"})

    def deezer_handler(request):
        path = request.url.path
        if path == "/playlist/9":
            return httpx.Response(200, json={"title": "Ma playlist"})
        if path in ("/track/1", "/track/2"):
            return httpx.Response(200, json={"id": 1, "isrc": "GBDUW0000053"})
        return httpx.Response(200, json={"error": {"type": "DataException", "message": "no data", "code": 800}})

    api = ApiClient("k", base_url="https://test", transport=httpx.MockTransport(api_handler))
    deezer = DeezerPublic(transport=httpx.MockTransport(deezer_handler))
    return lib, api, deezer, raw, downloads


def run(lib, api, job_ids):
    d = Downloader(api, lib)
    d.submit(job_ids)
    d.close(wait=True)


def test_import_dedups_and_reimport_only_fetches_missing(env):
    lib, api, deezer, raw, downloads = env

    p = importer.preview(api, deezer, lib, PLAYLIST)
    assert p.title == "Ma playlist"
    assert len(p.new) == 3
    run(lib, api, importer.queue(lib, p, "flac"))

    tracks = lib.tracks()
    assert len(tracks) == 1  # 1 et 2 ont le même ISRC
    t = tracks[0]
    assert (t["artist"], t["album"], t["isrc"], t["bpm"], t["format"]) == (
        "Daft Punk", "Discovery", "GBDUW0000053", 122.7, "flac")
    assert (t["badge"], t["real_quality"], t["max_frequency"], t["quality_origin"]) == (
        "Lossless", "Lossless", 21000, "api")
    assert [f.name for f in raw.iterdir()] == ["Daft Punk - One More Time.flac"]  # doublon supprimé
    assert lib.track_by_external("deezer", "1") == lib.track_by_external("deezer", "2") == t["id"]

    [failed] = lib.failed_jobs()
    assert (failed["external_id"], failed["http_status"]) == ("3", 422)
    assert failed["error"].startswith("Track not found")

    playlist_id = failed["playlist_id"]
    assert lib.playlist_track_ids(playlist_id) == [t["id"]]

    # réimport : seule la piste manquante repart, l'ancien échec est remplacé
    downloads.clear()
    p = importer.preview(api, deezer, lib, PLAYLIST)
    assert set(p.existing) == {"1", "2"}
    run(lib, api, importer.queue(lib, p, "flac"))
    assert downloads == ["3"]
    assert len(lib.failed_jobs()) == 1
    assert lib.playlist_track_ids(playlist_id) == [t["id"]]


def test_match_by_normalized_artist_title_and_duration(env):
    lib, *_ = env
    track_id, created = lib.add_track({"path": "a.mp3", "artist": "Daft Punk", "title": "One More Time",
                                       "duration": 320.4, "source": "manual"})
    assert created
    assert lib.find_track(None, "Daft Punk feat. Romanthony", "One More Time (Original Mix)", 322) == track_id
    assert lib.find_track(None, "Daft Punk", "One More Time", 330) is None  # hors ±3 s
