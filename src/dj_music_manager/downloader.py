"""Exécution des jobs : 4 téléchargements simultanés max, une piste en échec n'arrête pas les autres."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .api_client import ApiClient, ApiError, log
from .dedup import norm_key, titles_match
from .importer import match_collection, parse_duration
from .library import Library
from .quality.badge import badge
from .tags import read_tags

MAX_PARALLEL = 4  # au-delà, le débit Telegram (~6 Mo/s pour tout le compte) plafonne
POLL_SECONDS = 4  # suivi d'une collecte (l'API conseille 3 à 5 s)
COLLECTION_TIMEOUT = 1800  # s : au-delà, on cesse d'attendre une collecte qui ne finit pas


class Cancelled(Exception):
    pass


class Downloader:
    def __init__(self, api: ApiClient, lib: Library, on_event=None):
        """on_event(kind, job_id, payload) est appelé depuis les threads de travail.
        kind : 'status' (payload None), 'progress' (payload = pourcentage) ou 'collection' (job_id 0, message)."""
        self.api = api
        self.lib = lib
        self.on_event = on_event or (lambda *a: None)
        self._pool = ThreadPoolExecutor(MAX_PARALLEL)
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._cancelled: set[int] = set()
        self.poll_seconds = POLL_SECONDS

    # --- pause et annulation
    @property
    def paused(self) -> bool:
        return self._paused.is_set()

    def pause(self):
        """Plus aucun nouveau téléchargement ne démarre ; ceux en cours se terminent."""
        self._paused.set()

    def resume(self):
        self._paused.clear()

    def cancel(self, job_ids: list[int]):
        """Les jobs en attente ne partiront pas ; ceux en cours s'interrompent (fichier partiel supprimé)."""
        self._cancelled.update(job_ids)

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
        while self._paused.is_set() and not self._stop.is_set() and job_id not in self._cancelled:
            time.sleep(0.3)
        if self._stop.is_set() or job_id in self._cancelled:
            return
        job = self.lib.job(job_id)
        if job is None or job["status"] != "queued":  # supprimé de la file entre-temps
            return
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
            if self._stop.is_set() or job["id"] in self._cancelled:
                raise Cancelled("téléchargement annulé")
            pct = done * 100 // total if total else 0
            if pct != last:
                last = pct
                self.on_event("progress", job["id"], pct)

        dl, deezer_id = self._fetch(job, raw, progress)
        try:
            info = read_tags(dl.path)
            # le bot (surtout celui de secours) renvoie parfois un autre morceau : on ne le range pas en silence
            if not job["download_id"] and not titles_match(job["title"], info["title"]):
                log.warning("%s | fichier inattendu (bot %s) | attendu « %s – %s » | reçu « %s – %s »", dl.source,
                            dl.bot, job["artist"], job["title"], info["artist"], info["title"])
                raise ApiError(0, f"le bot a renvoyé « {info['artist']} – {info['title']} » au lieu de "
                                  f"« {job['title']} » : utilise « Chercher à la main »")
            quality = job["quality"]
            if quality == "flac" and info["format"] == "mp3":
                quality = "mp3_320"  # le bot de secours ne fournit que du MP3 320
            track_id, created = self.lib.add_track({
                **info,
                "path": dl.path.relative_to(raw).as_posix(),
                "expected_duration": job["expected_duration"],
                "quality": quality,
                "source": job["provider"],
                "real_quality": dl.real_quality,
                "lossless": dl.lossless,
                "max_frequency": dl.max_frequency,
                "badge": badge(dl.real_quality, dl.lossless, info["format"]),
                "quality_origin": "api",
            }, job["provider"], job["external_id"])
        except BaseException:
            dl.path.unlink(missing_ok=True)  # fichier illisible : pas d'orphelin dans RAW
            raise
        if deezer_id:
            self.lib.add_external("deezer", deezer_id, track_id)
        if not created:
            dl.path.unlink()  # doublon (même ISRC, ou même piste téléchargée en parallèle) : notre propre fichier
        return track_id

    def _fetch(self, job, raw: Path, progress):
        """Renvoie (téléchargement, ID Deezer s'il est connu)."""
        if job["file"]:  # déjà préparé par le bot via /collection
            try:
                return self.api.file(raw, job["file"], on_progress=progress), job["download_id"]
            except ApiError as e:
                if e.status != 404:  # 404 : message disparu côté Telegram, on repasse piste par piste
                    raise
        deezer_id = job["download_id"] or (job["external_id"] if job["provider"] == "deezer" else None)
        if deezer_id:
            return self.api.download(raw, job["quality"], id=deezer_id, on_progress=progress), deezer_id
        error = None
        if job["url"]:
            try:
                return self.api.download(raw, job["quality"], url=job["url"], on_progress=progress), None
            except ApiError as e:
                if e.status not in (404, 422):  # 422 : le bot n'a pas trouvé l'équivalent Deezer
                    raise
                error = e
        chosen = closest(self.api.search(f"{job['artist']} {job['title']}"), job["expected_duration"])
        if chosen is None:
            message = "aucun résultat en recherche"
            raise ApiError(error.status, f"{error.message} ({message})") if error else ApiError(404, message)
        self.lib.update_job(job["id"], download_id=chosen["id"])  # une relance ira droit au but
        return self.api.download(raw, job["quality"], id=chosen["id"], on_progress=progress), chosen["id"]

    # --- album/playlist entier en un appel au bot
    def submit_collection(self, p, quality: str, job_ids: list[int]) -> threading.Thread:
        """Collecte de tout le lien par le bot (POST /collections, suivi toutes les quelques secondes) : chaque
        piste part en téléchargement (/file) dès que son fichier arrive. Ce que le bot ne fournit pas passe ensuite
        piste par piste. Si l'aperçu était tronqué (p.total), les fichiers en plus sont les titres manquants :
        ajoutés s'ils ne sont pas déjà dans la bibliothèque."""
        for j in job_ids:
            self.on_event("status", j, None)  # visibles « En attente » pendant que le bot prépare
        thread = threading.Thread(target=self._collect, args=(p, quality, job_ids), daemon=True)
        thread.start()
        return thread

    def _collect(self, p, quality: str, job_ids: list[int]):
        jobs_by_track = {self.lib.job(j)["external_id"]: j for j in job_ids}
        remaining = list(p.tracks)  # pistes attendues sans fichier
        served: list[dict] = []  # pistes attendues déjà rapprochées d'un fichier
        seen_files: set[str] = set()
        extra_keys: set[str | None] = set()
        submitted: list[int] = []
        added: list[int] = []
        position = len(p.tracks)
        playlist_id = self.lib.playlist_for_source(p.url, p.title) if p.total else None
        state: dict = {}
        self.on_event("collection", 0, "Le bot prépare l'album ou la playlist ; chaque piste part dès qu'elle arrive…")
        try:
            state = self.api.start_collection(p.url, quality)
            deadline = time.monotonic() + COLLECTION_TIMEOUT
            bot = state.get("bot")
            while True:
                if bot and state.get("bot") != bot:  # le secours a pris le relais : sa liste repart de zéro
                    log.warning("collecte %s (%s) | bot %s remplacé par %s en cours de route",
                                state.get("id"), p.url, bot, state.get("bot"))
                bot = state.get("bot") or bot
                new = [t for t in state["tracks"] if t["file"] not in seen_files]
                seen_files.update(t["file"] for t in new)
                matched, unmatched = match_collection(new, remaining)
                for track_id, entry in matched.items():
                    served += [t for t in remaining if t["id"] == track_id]
                    remaining = [t for t in remaining if t["id"] != track_id]
                    job_id = jobs_by_track.get(track_id)
                    if job_id is not None:  # sinon déjà dans la bibliothèque : rien à télécharger
                        self.lib.update_job(job_id, file=entry["file"])
                        self.submit([job_id])
                        submitted.append(job_id)
                if p.total:
                    _, extras = match_collection(unmatched, served)  # écarte un titre déjà servi par l'autre bot
                    for e in extras:
                        key = norm_key(e.get("artist"), e.get("title"))
                        if key in extra_keys:
                            continue
                        extra_keys.add(key)
                        duration = parse_duration(e.get("duration"))
                        track_id = self.lib.find_track(None, e.get("artist"), e.get("title"), duration)
                        if track_id is not None:
                            self.lib.link(playlist_id, track_id, position)
                        else:
                            new_ids = self.lib.add_jobs([{
                                "playlist_id": playlist_id, "position": position, "provider": p.provider,
                                "file": e["file"], "artist": e.get("artist"), "title": e.get("title"),
                                "expected_duration": duration, "quality": quality}])
                            self.submit(new_ids)
                            added += new_ids
                        position += 1
                self.on_event("collection", 0, f"Bot ({bot}) : {len(seen_files)} fichier(s) reçu(s)…")
                if state.get("status") != "running" or self._stop.is_set():
                    break
                if time.monotonic() > deadline:
                    log.warning("collecte %s (%s) | toujours « running » après %d min : abandon du suivi",
                                state.get("id"), p.url, COLLECTION_TIMEOUT // 60)
                    break
                time.sleep(self.poll_seconds)
                state = self.api.collection_status(state["id"])
            if state.get("status") == "error":
                log.warning("collecte %s (%s) | status error | %s", state.get("id"), p.url, state.get("error"))
        except Exception as e:  # erreur déjà journalisée par le client API si elle vient de l'API
            self.on_event("collection", 0, f"Collecte interrompue ({e}) : le reste passe piste par piste.")
        if remaining and state:
            log.warning("collecte %s (%s) | %d piste(s) attendue(s) sur %d absente(s) (%d fichier(s) reçu(s)) : %s",
                        state.get("id"), p.url, len(remaining), len(p.tracks), len(seen_files),
                        "; ".join(f"{t['artist']} – {t['title']}" for t in remaining[:30]))
        rest = [j for j in job_ids if j not in submitted]
        message = f"Bot : {len(submitted)} piste(s) par la collecte"
        if added:
            message += f", {len(added)} titre(s) en plus de l'aperçu"
        if rest:
            message += f" ; {len(rest)} cherchée(s) une par une"
        self.on_event("collection", 0, message)
        self.submit(rest)

def closest(results: list[dict], duration: float | None) -> dict | None:
    """Résultat de /search dont la durée est la plus proche de celle attendue."""
    if not results or duration is None:
        return results[0] if results else None
    return min(results, key=lambda r: abs((parse_duration(r.get("duration")) or 0) - duration))
