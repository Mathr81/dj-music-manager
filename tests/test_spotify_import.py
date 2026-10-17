"""Import Spotify : dédoublonnage avec l'existant, /download?url=, repli /search par durée la plus proche."""

import shutil
import subprocess

import httpx
import pytest

from dj_music_manager import importer
from dj_music_manager.api_client import ApiClient
from dj_music_manager.deezer import DeezerPublic
from dj_music_manager.downloader import Downloader, closest
from dj_music_manager.library import Library
from dj_music_manager.spotify import NOT_OWNED, NotOwnedError

URL = "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"
SP_TRACKS = [
    # déjà en bibliothèque (téléchargée depuis Deezer) : reconnue par ISRC
    {"id": "sp1", "artist": "Daft Punk", "title": "One More Time", "duration": 320.0,
     "url": "https://open.spotify.com/track/sp1", "isrc": "GBDUW0000053"},
    # trouvée directement par le bot via le lien Spotify
    {"id": "sp2", "artist": "Stromae", "title": "Papaoutai", "duration": 232.0,
     "url": "https://open.spotify.com/track/sp2", "isrc": None},
    # le bot ne trouve pas le lien : repli /search
    {"id": "sp3", "artist": "Fred again..", "title": "Delilah (pull me out of this)", "duration": 250.0,
     "url": "https://open.spotify.com/track/sp3", "isrc": None},
]


class FakeSpotify:
    def collection(self, url):
        return "Warmup", SP_TRACKS

    def track(self, track_id):
        return {"external_ids": {}}


@pytest.fixture(scope="module")
def mp3(tmp_path_factory):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg absent")
    out = {}
    for name, isrc, secs in (("papaoutai", "BEUM71300001", 2), ("delilah", "GBAHS2300001", 3)):
        p = tmp_path_factory.mktemp(name) / f"{name}.mp3"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency=440:duration={secs}",
                        "-metadata", f"title={name}", "-metadata", f"TSRC={isrc}", str(p)], check=True)
        out[name] = p.read_bytes()
    return out


def test_spotify_import(tmp_path, mp3):
    raw = tmp_path / "RAW"
    raw.mkdir()
    lib = Library(tmp_path / "db")
    lib.set_setting("raw_dir", str(raw))
    existing, _ = lib.add_track({"path": "Daft Punk - One More Time.mp3", "artist": "Daft Punk",
                                 "title": "One More Time", "isrc": "GBDUW0000053", "duration": 320.4,
                                 "source": "deezer"}, "deezer", "3135553")
    calls = []

    def api_handler(request):
        p = dict(request.url.params)
        calls.append(p)
        if request.url.path == "/search":
            return httpx.Response(200, json=[
                {"id": "111", "artist": "Fred again..", "title": "Delilah", "duration": "3:20", "url": "u"},
                {"id": "222", "artist": "Fred again..", "title": "Delilah (pull me out of this)", "duration": "4:09",
                 "url": "u"}])
        if p.get("url") == "https://open.spotify.com/track/sp3":
            return httpx.Response(422, json={"detail": "Track not found or unavailable."})
        name = "papaoutai" if p.get("url") else "delilah"
        return httpx.Response(200, content=mp3[name], headers={
            "content-disposition": f'attachment; filename="{name}.mp3"', "x-real-quality": "LOSSY ~320kbps",
            "x-lossless": "false"})

    api = ApiClient("k", base_url="https://test", transport=httpx.MockTransport(api_handler))
    deezer = DeezerPublic(transport=httpx.MockTransport(lambda r: httpx.Response(500)))

    p = importer.preview(api, deezer, lib, URL, spotify=FakeSpotify())
    assert (p.provider, p.title, p.existing) == ("spotify", "Warmup", {"sp1": existing})
    d = Downloader(api, lib)
    d.submit(importer.queue(lib, p, "mp3_320"))
    d.close(wait=True)

    assert lib.failed_jobs() == []
    assert {"url": "https://open.spotify.com/track/sp2", "quality": "mp3_320"} in calls
    assert {"id": "222", "quality": "mp3_320"} in calls  # 4:09 est le plus proche de 250 s
    by_title = {t["title"]: t for t in lib.tracks()}
    assert by_title["delilah"]["source"] == "spotify"
    assert lib.track_by_external("spotify", "sp3") == lib.track_by_external("deezer", "222") == by_title["delilah"]["id"]
    assert lib.track_by_external("spotify", "sp1") == existing

    # réimport : tout est reconnu par l'ID Spotify, plus aucun téléchargement
    assert importer.preview(api, deezer, lib, URL, spotify=FakeSpotify()).new == []


def test_closest():
    results = [{"id": "a", "duration": "3:00"}, {"id": "b", "duration": "3:58"}]
    assert closest(results, 235)["id"] == "b"
    assert closest(results, None)["id"] == "a"
    assert closest([], 200) is None


class NotMinePlaylist(FakeSpotify):
    def collection(self, url):
        raise NotOwnedError(NOT_OWNED)


@pytest.mark.parametrize("spotify", [None, NotMinePlaylist()], ids=["sans connexion", "playlist d'un autre"])
@pytest.mark.parametrize("total, warned", [(None, False), (3, False), (297, True)])
def test_public_playlist_fallback(tmp_path, monkeypatch, spotify, total, warned):
    lib = Library(tmp_path / "db")
    lib.add_track({"path": "a.mp3", "artist": "Daft Punk", "title": "One More Time", "duration": 320.4,
                   "source": "deezer"})
    public = [{**t, "isrc": None} for t in SP_TRACKS]  # la page publique ne donne pas l'ISRC
    monkeypatch.setattr(importer, "public_collection", lambda url: ("ÉTÉ 2K26", public, total))

    p = importer.preview(None, None, lib, URL, spotify=spotify)

    assert (p.provider, p.title, len(p.tracks)) == ("spotify", "ÉTÉ 2K26", 3)
    assert set(p.existing) == {"sp1"}  # reconnue par artiste + titre ± 3 s
    assert (p.warning is not None) is warned
    if warned:
        assert "294 autres" in p.warning
