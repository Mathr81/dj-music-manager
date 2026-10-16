"""API publique Deezer (api.deezer.com) : ISRC et titre d'album/playlist."""

import re
import threading
import time

import httpx

API = "https://api.deezer.com"
COLLECTION_RE = re.compile(r"deezer\.com/(?:[a-z]{2}/)?(album|playlist)/(\d+)")


class DeezerError(Exception):
    pass


class DeezerPublic:
    def __init__(self, transport: httpx.BaseTransport | None = None):
        self._http = httpx.Client(base_url=API, timeout=20, transport=transport)
        self._lock = threading.Lock()
        self._next = 0.0

    def _get(self, path: str) -> dict:
        for _ in range(3):
            with self._lock:  # quota Deezer : 50 requêtes / 5 s
                wait = self._next - time.monotonic()
                if wait > 0:
                    time.sleep(wait)
                self._next = time.monotonic() + 0.1
            data = self._http.get(path).raise_for_status().json()
            err = data.get("error")
            if not err:
                return data
            if err.get("code") != 4:  # 4 = "Quota limit exceeded"
                raise DeezerError(err.get("message"))
            time.sleep(5)
        raise DeezerError("quota Deezer dépassé")

    def track(self, track_id: str) -> dict:
        return self._get(f"/track/{track_id}")

    def collection_title(self, url: str) -> str:
        m = COLLECTION_RE.search(url)
        if not m:
            raise DeezerError(f"lien Deezer non reconnu : {url}")
        return self._get(f"/{m[1]}/{m[2]}")["title"]
