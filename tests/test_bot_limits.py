"""Consignes de l'API : 2 /download au plus en parallèle, aucun pendant une collecte, 503 réessayé."""

import threading
import time

import httpx
import pytest

from dj_music_manager.api_client import ApiClient, ApiError
from dj_music_manager.downloader import Downloader
from dj_music_manager.library import Library


def setup(tmp_path, handler, n_jobs):
    lib = Library(tmp_path / "db")
    lib.set_setting("raw_dir", str(tmp_path))
    ids = lib.add_jobs([{"provider": "deezer", "external_id": str(i), "artist": "A", "title": f"T{i}",
                         "quality": "flac"} for i in range(n_jobs)])
    api = ApiClient("k", base_url="https://test", transport=httpx.MockTransport(handler))
    return lib, Downloader(api, lib), ids


def test_at_most_two_downloads_at_once(tmp_path):
    lock, current, peak = threading.Lock(), [0], [0]

    def handler(request):
        with lock:
            current[0] += 1
            peak[0] = max(peak[0], current[0])
        time.sleep(0.1)
        with lock:
            current[0] -= 1
        return httpx.Response(422, json={"detail": "Track not found or unavailable."})

    lib, d, ids = setup(tmp_path, handler, 6)
    d.submit(ids)
    d.close(wait=True)
    assert peak[0] == 2 and len(lib.failed_jobs()) == 6


def test_no_download_while_a_collection_runs(tmp_path):
    calls = []
    lib, d, ids = setup(tmp_path, lambda r: calls.append(r) or httpx.Response(422, json={"detail": "x"}), 1)
    d._collections = 1  # une collecte occupe le bot
    d.submit(ids)
    time.sleep(0.5)
    assert calls == []
    with d._idle:
        d._collections = 0
        d._idle.notify_all()
    d.close(wait=True)
    assert len(calls) == 1


def test_503_retried_three_times_then_reported(tmp_path, monkeypatch):
    calls = []
    api = ApiClient("k", base_url="https://test", transport=httpx.MockTransport(
        lambda r: calls.append(r) or httpx.Response(503, headers={"retry-after": "5"}, json={"detail": "bots"})))
    monkeypatch.setattr(api._closed, "wait", lambda s: False)
    with pytest.raises(ApiError) as e:
        api.download(tmp_path, "flac", id="1")
    assert e.value.status == 503 and len(calls) == 4
