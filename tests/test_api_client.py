import httpx
import pytest

from dj_music_manager.api_client import ApiClient, ApiError, filename

AUDIO_HEADERS = {
    "content-disposition": "attachment; filename=\"Stromae - Papaoutai.flac\"; "
                           "filename*=utf-8''Stromae%20-%20Papa%C3%B4utai.flac",
    "x-real-quality": "Lossless", "x-lossless": "true", "x-max-frequency": "21563",
}


def client(handler) -> ApiClient:
    return ApiClient("secret", base_url="https://test", transport=httpx.MockTransport(handler))


def test_download_streams_to_disk_with_quality_headers(tmp_path):
    seen, progress = [], []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, headers=AUDIO_HEADERS, content=b"x" * 1000)

    dl = client(handler).download(tmp_path, "flac", id="1", on_progress=lambda d, t: progress.append(d))
    assert dl.path.name == "Stromae - Papaôutai.flac"
    assert dl.path.read_bytes() == b"x" * 1000
    assert (dl.real_quality, dl.lossless, dl.max_frequency) == ("Lossless", True, 21563)
    assert seen[0].headers["x-api-key"] == "secret"
    assert dict(seen[0].url.params) == {"id": "1", "quality": "flac"}
    assert progress[-1] == 1000
    assert not list(tmp_path.glob("*.part"))


def test_never_overwrites_existing_file(tmp_path):
    (tmp_path / "Stromae - Papaôutai.flac").write_bytes(b"old")
    dl = client(lambda r: httpx.Response(200, headers=AUDIO_HEADERS, content=b"new")).download(tmp_path, "flac", id="1")
    assert dl.path.name == "Stromae - Papaôutai (2).flac"
    assert (tmp_path / "Stromae - Papaôutai.flac").read_bytes() == b"old"


def test_filename_cannot_escape_dest_dir():
    assert filename('attachment; filename="../../evil.mp3"') == "_.._evil.mp3"
    assert filename("attachment; filename*=utf-8''..%5C..%5Cevil.mp3") == "_.._evil.mp3"


@pytest.mark.parametrize("status, calls", [(422, 1), (504, 2)])
def test_422_not_retried_504_retried_once(tmp_path, status, calls):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(status, json={"detail": "Track not found or unavailable.\nDebugID: 123"})

    with pytest.raises(ApiError) as e:
        client(handler).download(tmp_path, "mp3_320", id="1")
    assert e.value.status == status
    assert e.value.message.startswith("Track not found")
    assert len(seen) == calls


def test_504_then_success(tmp_path):
    responses = iter([httpx.Response(504, json={"detail": "timeout"}),
                      httpx.Response(200, headers=AUDIO_HEADERS, content=b"a")])
    assert client(lambda r: next(responses)).download(tmp_path, "flac", id="1").path.exists()


def test_503_retried_after_retry_after(tmp_path, monkeypatch):
    responses = iter([httpx.Response(503, headers={"retry-after": "120"}, json={"detail": "bots mis de côté"}),
                      httpx.Response(200, headers=AUDIO_HEADERS, content=b"a")])
    api = client(lambda r: next(responses))
    waits = []
    monkeypatch.setattr(api._closed, "wait", lambda s: waits.append(s) or False)
    assert api.download(tmp_path, "flac", id="1").path.exists()
    assert waits == [120.0]


def test_503_wait_interrupted_by_close(tmp_path):
    api = client(lambda r: httpx.Response(503, headers={"retry-after": "999"}, json={"detail": "bots mis de côté"}))
    api._closed.set()  # app fermée pendant l'attente : pas de nouvel essai
    with pytest.raises(ApiError) as e:
        api.download(tmp_path, "flac", id="1")
    assert (e.value.status, e.value.retry_after) == (503, 999.0)


def test_validation_detail_is_a_list(tmp_path):
    body = {"detail": [{"type": "string_pattern_mismatch", "loc": ["query", "id"], "msg": "String should match"}]}
    with pytest.raises(ApiError) as e:
        client(lambda r: httpx.Response(422, json=body)).download(tmp_path, "flac", id="abc")
    assert e.value.message == "query.id : String should match"
