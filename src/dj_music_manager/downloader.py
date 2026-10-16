"""Exécution des jobs : 4 téléchargements simultanés max, une piste en échec n'arrête pas les autres."""

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .api_client import ApiClient, ApiError
from .library import Library
from .quality.badge import badge
from .tags import read_tags

MAX_PARALLEL = 4  # au-delà, le débit Telegram (~6 Mo/s pour tout le compte) plafonne


class Cancelled(Exception):
    pass


class Downloader:
    def __init__(self, api: ApiClient, lib: Library, on_event=None):
        """on_event(kind, job_id, payload) est appelé depuis les threads de travail.
        kind : 'status' (payload None) ou 'progress' (payload = pourcentage)."""
        self.api = api
        self.lib = lib
        self.on_event = on_event or (lambda *a: None)
        self._pool = ThreadPoolExecutor(MAX_PARALLEL)
        self._stop = threading.Event()

    def submit(self, job_ids: list[int]):
        jobs = [self.lib.job(j) for j in job_ids]
        # changer de qualité coûte ~3 s côté API : on enchaîne les jobs d'une même qualité
        for job in sorted(jobs, key=lambda j: j["quality"]):
            self.lib.update_job(job["id"], status="queued", error=None, http_status=None)
            self.on_event("status", job["id"], None)
            self._pool.submit(self._run, job["id"])

    def close(self, wait: bool = False):
        """wait=True : termine la file ; sinon annule ce qui reste et interrompt les téléchargements."""
        if not wait:
            self._stop.set()
        self._pool.shutdown(wait=wait, cancel_futures=not wait)

    def _run(self, job_id: int):
        if self._stop.is_set():
            return
        job = self.lib.job(job_id)
        self.lib.start_job(job_id)
        self.on_event("status", job_id, None)
        try:
            # déjà arrivé entre-temps (même piste dans un autre import) ?
            track_id = self.lib.track_by_external(job["provider"], job["external_id"])
            if track_id is None:
                track_id = self._download(job)
            if job["playlist_id"]:
                self.lib.link(job["playlist_id"], track_id, job["position"])
            self.lib.update_job(job_id, status="done", track_id=track_id, error=None, http_status=None)
        except ApiError as e:
            self.lib.update_job(job_id, status="failed", http_status=e.status, error=e.message)
        except Exception as e:  # une piste en échec n'arrête jamais la playlist
            self.lib.update_job(job_id, status="failed", error=f"{type(e).__name__} : {e}")
        self.on_event("status", job_id, None)

    def _download(self, job) -> int:
        raw = Path(self.lib.get_setting("raw_dir"))
        last = -1

        def progress(done, total):
            nonlocal last
            if self._stop.is_set():
                raise Cancelled("téléchargement annulé")
            pct = done * 100 // total if total else 0
            if pct != last:
                last = pct
                self.on_event("progress", job["id"], pct)

        dl = self.api.download(raw, job["quality"], id=job["download_id"] or job["external_id"], on_progress=progress)
        try:
            info = read_tags(dl.path)
            quality = job["quality"]
            if quality == "flac" and info["format"] == "mp3":
                quality = "mp3_320"  # le bot de secours ne fournit que du MP3 320
            track_id, created = self.lib.add_track({
                **info,
                "path": dl.path.relative_to(raw).as_posix(),
                "expected_duration": job["expected_duration"],
                "quality": quality,
                "source": "deezer",
                "real_quality": dl.real_quality,
                "lossless": dl.lossless,
                "max_frequency": dl.max_frequency,
                "badge": badge(dl.real_quality, dl.lossless, info["format"]),
                "quality_origin": "api",
            }, job["provider"], job["external_id"])
        except BaseException:
            dl.path.unlink(missing_ok=True)  # fichier illisible : pas d'orphelin dans RAW
            raise
        if job["download_id"]:
            self.lib.add_external("deezer", job["download_id"], track_id)
        if not created:
            dl.path.unlink()  # doublon (même ISRC, ou même piste téléchargée en parallèle) : notre propre fichier
        return track_id
