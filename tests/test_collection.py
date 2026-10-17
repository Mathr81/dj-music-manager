"""POST /collections + suivi + /file : collecte de tout le lien, repli piste par piste, titres en plus."""

import shutil
import subprocess
import threading

import httpx
import pytest

from dj_music_manager import importer
from dj_music_manager.api_client import ApiClient, ApiError
from dj_music_manager.downloader import Downloader
from dj_music_manager.importer import Preview, match_collection
from dj_music_manager.library import Library

URL = "https://open.spotify.com/playlist/35Izkr0GbGBWHszIvqJItZ"


def client(handler) -> ApiClient:
    return ApiClient("k", base_url="https://test", transport=httpx.MockTransport(handler))


def test_collection_keeps_only_safe_file_paths():
    body = {"id": "abc", "status": "running", "bot": "x", "tracks": [
        {"file": "/file/MusicsHuntersbot/12", "artist": "A", "title": "B"},
        {"file": "https://ailleurs.example/vol", "artist": "C", "title": "D"},
        {"file": "/file/../../admin", "artist": "E", "title": "F"}]}
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(202, json=body)

    state = client(handler).start_collection(URL, "mp3_320")
    assert (seen[0].method, seen[0].url.path, dict(seen[0].url.params)) == (
        "POST", "/collections", {"url": URL, "quality": "mp3_320"})
    assert [t["file"] for t in state["tracks"]] == ["/file/MusicsHuntersbot/12"]
    assert state["id"] == "abc" and state["status"] == "running"


def test_collection_status_refuses_strange_ids():
    with pytest.raises(ApiError, match="invalide"):
        client(lambda r: pytest.fail("aucune requête ne doit partir")).collection_status("../../admin")


def test_file_refuses_foreign_urls_so_the_key_never_leaves(tmp_path):
    with pytest.raises(ApiError, match="invalide"):
        client(lambda r: pytest.fail("aucune requête ne doit partir")).file(tmp_path, "https://ailleurs.example/x")


def test_collection_retried_once_on_504():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(504, json={"detail": "bot muet"})

    with pytest.raises(ApiError):
        client(handler).start_collection(URL, "flac")
    assert len(calls) == 2 and dict(calls[0].url.params) == {"url": URL, "quality": "flac"}


def track(track_id, artist, title, duration):
    return {"id": track_id, "artist": artist, "title": title, "duration": duration,
            "url": f"https://open.spotify.com/track/{track_id}", "isrc": None}


def test_match_collection():
    tracks = [track("a", "Daft Punk", "One More Time", 320), track("b", "Vacra, La Rvfleuze", "Fille à Papa", 170),
              track("c", "Inconnu", "Absent", 200), track("d", "Stromae", "Papaoutai", 232)]
    entries = [
        {"file": "/file/x/1", "artist": "Daft Punk", "title": "One More Time (Original Mix)", "duration": "5:21"},
        {"file": "/file/x/2", "artist": "La Rvfleuze & Vacra", "title": "Fille a papa", "duration": "2:50"},
        {"file": "/file/x/3", "artist": "Stromae", "title": "Papaoutai", "duration": "4:10"},  # autre version
        {"file": "/file/x/4", "artist": "Nouveau", "title": "Titre 101", "duration": "3:00"},
    ]
    matched, extras = match_collection(entries, tracks)
    assert {k: v["file"] for k, v in matched.items()} == {"a": "/file/x/1", "b": "/file/x/2"}
    assert [e["file"] for e in extras] == ["/file/x/3", "/file/x/4"]


@pytest.fixture(scope="module")
def audio(tmp_path_factory):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg absent")
    out = {}
    for name, isrc in (("un", "FRAAA0000001"), ("deux", "FRAAA0000002"), ("plus", "FRAAA0000003")):
        p = tmp_path_factory.mktemp(name) / f"{name}.flac"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=duration=1", "-metadata", f"title={name}",
                        "-metadata", f"ISRC={isrc}", str(p)], check=True)
        out[name] = p.read_bytes()
    return out


UN = {"file": "/file/bot/1", "artist": "A", "title": "Un", "duration": "0:01"}
LA = {"file": "/file/bot/2", "artist": "Déjà", "title": "Là", "duration": "3:20"}  # déjà en bibliothèque
PLUS = {"file": "/file/bot/3", "artist": "C", "title": "Plus", "duration": "0:01"}  # au-delà des 100 publics


def state(status, tracks, bot="MusicsHuntersbot"):
    return {"id": "c0ffee", "status": status, "error": None, "bot": bot, "tracks": tracks}


def run_import(tmp_path, audio, states=None, collection_status=202):
    """states : réponses successives de la collecte (POST puis chaque suivi ; la dernière se répète)."""
    raw = tmp_path / "RAW"
    raw.mkdir()
    lib = Library(tmp_path / "db")
    lib.set_setting("raw_dir", str(raw))
    known, _ = lib.add_track({"path": "x.mp3", "artist": "Déjà", "title": "Là", "duration": 200.0, "source": "manual"})
    states = list(states or [state("running", [UN]), state("done", [UN, LA, PLUS])])
    calls = []
    first_file = threading.Event()

    def handler(request):
        path, params = request.url.path, dict(request.url.params)
        if path == "/download":
            calls.append(f"/download?{'&'.join(f'{k}={v}' for k, v in params.items())}")
        else:
            calls.append(f"{request.method} {path}" if path.startswith("/collections") else path)
        if path.startswith("/file"):
            first_file.set()
        if request.method == "GET" and path.startswith("/collections") and not calls.count("early"):
            calls.append("early" if first_file.wait(5) else "late")  # le suivi attend un 1er téléchargement
        if path.startswith("/collections"):
            if collection_status != 202:
                return httpx.Response(collection_status, json={"detail": "bot muet"})
            return httpx.Response(202, json=states.pop(0) if len(states) > 1 else states[0])
        name = {"/file/bot/1": "un", "/file/bot/3": "plus", "/file/secours/1": "un", "/file/secours/3": "plus"}.get(
            path) or ("un" if params.get("url", "").endswith("/s1") else "deux")
        return httpx.Response(200, content=audio[name], headers={
            "content-disposition": f'attachment; filename="{name}.flac"', "x-real-quality": "Lossless"})

    api = client(handler)
    p = Preview(URL, "ÉTÉ 2K26", [track("s1", "A", "Un", 1.0), track("s2", "B", "Deux", 1.0)], "spotify", total=4)
    job_ids = importer.queue(lib, p, "flac")
    d = Downloader(api, lib)
    d.poll_seconds = 0.01
    d.submit_collection(p, "flac", job_ids).join()
    d.close(wait=True)
    return lib, calls, known


def test_collection_then_fallback_and_extra_tracks(tmp_path, audio):
    lib, calls, known = run_import(tmp_path, audio)

    assert calls[0] == "POST /collections"
    assert "early" in calls  # « Un » est parti pendant que la collecte tournait encore
    assert sorted(c for c in calls if c.startswith(("/file", "/download"))) == sorted([
        "/file/bot/1", "/file/bot/3", "/download?url=https://open.spotify.com/track/s2&quality=flac"])  # « Deux » absent
    assert lib.failed_jobs() == []
    titles = {t["id"]: t["title"] for t in lib.tracks()}
    playlist = lib.playlist_track_ids(lib.playlists()[0]["id"])
    assert [titles[i] for i in playlist] == ["un", "deux", "Là", "plus"]  # ordre : aperçu puis titres en plus
    assert known in playlist  # « Là » relié sans être retéléchargé
    assert lib.track_by_external("spotify", "s1") is not None


def test_bot_switch_restarts_the_list_without_duplicates(tmp_path, audio):
    secours_un = {**UN, "file": "/file/secours/1"}
    secours_plus = {**PLUS, "file": "/file/secours/3"}
    lib, calls, _ = run_import(tmp_path, audio, states=[
        state("running", [UN]),
        state("running", [], bot="DeezerMusicBot"),  # le secours reprend de zéro
        state("done", [secours_un, LA, secours_plus], bot="DeezerMusicBot")])
    downloads = [c for c in calls if c.startswith(("/file", "/download"))]
    assert "/file/secours/1" not in downloads  # « Un » déjà servi par le premier bot
    assert downloads.count("/file/secours/3") == 1 and lib.failed_jobs() == []
    assert len(lib.tracks()) == 4  # Là (déjà là) + un + deux + plus


def test_collection_failure_falls_back_to_track_by_track(tmp_path, audio):
    lib, calls, _ = run_import(tmp_path, audio, collection_status=504)
    assert calls[:2] == ["POST /collections", "POST /collections"]  # un seul nouvel essai
    assert sorted(calls[2:]) == ["/download?url=https://open.spotify.com/track/s1&quality=flac",
                                 "/download?url=https://open.spotify.com/track/s2&quality=flac"]
    assert lib.failed_jobs() == [] and len(lib.tracks()) == 3


def test_wrong_song_from_bot_goes_to_failures(tmp_path, audio):
    raw = tmp_path / "RAW"
    raw.mkdir()
    lib = Library(tmp_path / "db")
    lib.set_setting("raw_dir", str(raw))
    api = client(lambda r: httpx.Response(200, content=audio["deux"], headers={  # fichier titré « deux »
        "content-disposition": 'attachment; filename="deux.flac"', "x-real-quality": "Lossless"}))
    [job_id] = lib.add_jobs([{"provider": "deezer", "external_id": "1", "artist": "A", "title": "Voyager",
                              "quality": "flac"}])
    d = Downloader(api, lib)
    d.submit([job_id])
    d.close(wait=True)
    [failed] = lib.failed_jobs()
    assert "au lieu de « Voyager »" in failed["error"]
    assert lib.tracks() == [] and list(raw.iterdir()) == []  # rien de rangé, pas d'orphelin
