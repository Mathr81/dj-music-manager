"""Client de l'API Music DL (référence : docs/API.md)."""

import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

import httpx

from .files import reserve

QUALITIES = ("flac", "mp3_320", "mp3_128")
FILE_PATH_RE = re.compile(r"^/file/[A-Za-z0-9_.-]+/\d+$")


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"{status} : {message}")
        self.status = status
        self.message = message


@dataclass
class Download:
    path: Path
    real_quality: str | None  # X-Real-Quality
    lossless: bool | None  # X-Lossless
    max_frequency: int | None  # X-Max-Frequency


def error_message(resp: httpx.Response) -> str:
    """`detail` est une chaîne, ou un tableau {loc, msg, type} pour les 422 de validation."""
    try:
        body = resp.json()
    except ValueError:
        return resp.text[:300] or resp.reason_phrase
    detail = body.get("detail") if isinstance(body, dict) else body
    if isinstance(detail, list):
        return "; ".join(
            f"{'.'.join(map(str, d.get('loc', [])))} : {d.get('msg')}" if isinstance(d, dict) else str(d)
            for d in detail
        )
    return str(detail)


def filename(content_disposition: str) -> str:
    """Nom depuis Content-Disposition (filename* UTF-8 en priorité), nettoyé pour Windows."""
    m = re.search(r"filename\*=utf-8''([^;]+)", content_disposition, re.I)
    if m:
        name = unquote(m[1])
    else:
        m = re.search(r'filename="([^"]*)"', content_disposition)
        name = m[1] if m else ""
    # le nom vient du serveur : pas de séparateur de chemin ni de caractère interdit
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    if not name:
        raise ApiError(0, "réponse sans nom de fichier")
    return name


class ApiClient:
    def __init__(self, api_key: str, base_url: str, transport: httpx.BaseTransport | None = None):
        self._http = httpx.Client(
            base_url=base_url, headers={"X-API-Key": api_key}, timeout=150, transport=transport
        )

    def close(self):
        self._http.close()

    def _get_json(self, path: str, params: dict, timeout: float | None = None):
        r = self._http.get(path, params=params, **({"timeout": timeout} if timeout else {}))
        if r.status_code != 200:
            raise ApiError(r.status_code, error_message(r))
        return r.json()

    @staticmethod
    def _retry_504(call):
        """422 : pas de nouvel essai (même réponse garantie) ; 504 : un seul nouvel essai."""
        try:
            return call()
        except ApiError as e:
            if e.status != 504:
                raise
        return call()

    def tracks(self, url: str) -> list[dict]:
        return self._get_json("/tracks", {"url": url})

    def search(self, q: str) -> list[dict]:
        return self._get_json("/search", {"q": q})

    def collection(self, url: str, quality: str) -> list[dict]:
        """Toutes les pistes d'un album/playlist en un appel au bot ; fichiers à récupérer avec file().
        Une piste que le bot ne trouve pas est simplement absente."""
        # ~10-35 s pour 45 pistes, bien plus si le bot de secours prend le relais : délai large
        data = self._retry_504(lambda: self._get_json("/collection", {"url": url, "quality": quality}, timeout=900))
        return [t for t in data.get("tracks", []) if FILE_PATH_RE.match(str(t.get("file", "")))]

    def download(self, dest_dir: Path, quality: str, *, id: str | None = None, url: str | None = None,
                 on_progress=None) -> Download:
        """Télécharge une piste dans dest_dir."""
        params = {"id": id} if id else {"url": url}
        params["quality"] = quality
        return self._retry_504(lambda: self._stream_to(Path(dest_dir), "/download", params, on_progress))

    def file(self, dest_dir: Path, path: str, on_progress=None) -> Download:
        """Télécharge un fichier listé par collection()."""
        if not FILE_PATH_RE.match(path):  # chemin fourni par le serveur : jamais d'URL vers un autre hôte
            raise ApiError(0, f"chemin de fichier invalide : {path!r}")
        return self._retry_504(lambda: self._stream_to(Path(dest_dir), path, {}, on_progress))

    def _stream_to(self, dest_dir: Path, path: str, params: dict, on_progress) -> Download:
        with self._http.stream("GET", path, params=params) as r:
            if r.status_code != 200:
                r.read()
                raise ApiError(r.status_code, error_message(r))
            final, f = reserve(dest_dir, filename(r.headers.get("content-disposition", "")))
            part = Path(f.name)
            total = int(r.headers.get("content-length") or 0)
            done = 0
            try:
                with f:
                    for chunk in r.iter_bytes():
                        f.write(chunk)
                        done += len(chunk)
                        if on_progress:
                            on_progress(done, total)
                os.rename(part, final)  # sous Windows, échoue plutôt que d'écraser
            except BaseException:
                part.unlink(missing_ok=True)
                raise
            h = r.headers
            mf = h.get("x-max-frequency", "")
            return Download(
                path=final,
                real_quality=h.get("x-real-quality") or None,
                lossless={"true": True, "false": False}.get(h.get("x-lossless", "").lower()),
                max_frequency=int(mf) if mf.isdigit() else None,
            )
