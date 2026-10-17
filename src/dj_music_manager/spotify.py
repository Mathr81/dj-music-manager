"""Pistes d'un album ou d'une playlist Spotify.

- API Web officielle avec connexion OAuth PKCE (sans secret client). Depuis février 2026, une app en mode
  développeur ne reçoit le contenu que des playlists possédées ou co-éditées par l'utilisateur connecté.
- Sans connexion, ou pour la playlist publique de quelqu'un d'autre : page d'intégration publique (lecteur
  « embed »), qui ne liste que les 100 premiers titres.
"""

import base64
import hashlib
import json
import re
import secrets
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API = "https://api.spotify.com/v1"
REDIRECT_HOST, REDIRECT_PORT = "127.0.0.1", 8888
REDIRECT_URI = f"http://{REDIRECT_HOST}:{REDIRECT_PORT}/callback"  # à déclarer dans le tableau de bord Spotify
SCOPES = "playlist-read-private playlist-read-collaborative"
LINK_RE = re.compile(r"(?:open\.spotify\.com/(?:intl-[a-z]{2}/)?|spotify:)(playlist|album)[/:]([A-Za-z0-9]{22})")
TRACK_LINK_RE = re.compile(r"(?:open\.spotify\.com/(?:intl-[a-z]{2}/)?track/|spotify:track:)([A-Za-z0-9]{22})")
NOT_OWNED = ("Spotify ne donne le contenu que des playlists que tu possèdes ou co-édites. "
             "Copie ses titres dans une playlist à toi (Ctrl+A, « Ajouter à la playlist ») puis réessaie.")
EMBED_URL = "https://open.spotify.com/embed/{kind}/{id}"
PAGE_URL = "https://open.spotify.com/{kind}/{id}"
EMBED_LIMIT = 100  # la page d'intégration ne liste pas au-delà
_NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S)


class SpotifyError(Exception):
    pass


class NotOwnedError(SpotifyError):
    """L'API officielle ne livre pas le contenu de cette playlist (pas à l'utilisateur connecté)."""


def is_spotify_link(url: str) -> bool:
    return LINK_RE.search(url) is not None


def track_ids_in(text: str) -> list[str]:
    """ID des titres d'un texte collé depuis l'app Spotify (Ctrl+A puis Ctrl+C), dans l'ordre, sans doublon."""
    return list(dict.fromkeys(TRACK_LINK_RE.findall(text)))


def _track(track_id: str, artist: str, title: str, duration_s: float, isrc: str | None = None) -> dict:
    return {"id": track_id, "artist": artist, "title": title, "duration": duration_s,
            "url": f"https://open.spotify.com/track/{track_id}", "isrc": isrc}


def _public_client(transport: httpx.BaseTransport | None) -> httpx.Client:
    return httpx.Client(timeout=20, transport=transport, follow_redirects=True,
                        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})


def _embed_entity(http: httpx.Client, kind: str, sid: str) -> dict:
    """Données de la page d'intégration publique (lecteur « embed »).
    shortcut: dépend du format interne de cette page ; à adapter si Spotify le change."""
    r = http.get(EMBED_URL.format(kind=kind, id=sid))
    if r.status_code == 404:
        raise SpotifyError(f"{kind} Spotify introuvable (privé ou supprimé ?)")
    if r.status_code != 200:
        raise SpotifyError(f"page Spotify inaccessible ({r.status_code})")
    try:
        return json.loads(_NEXT_DATA_RE.search(r.text)[1])["props"]["pageProps"]["state"]["data"]["entity"]
    except (TypeError, KeyError, ValueError) as e:
        raise SpotifyError("format de la page Spotify inattendu : l'import public est peut-être à mettre à jour") from e


def public_track(track_id: str, transport: httpx.BaseTransport | None = None) -> dict:
    """Titre, artistes et durée d'un morceau, sans connexion (les pages de morceaux ne sont pas restreintes)."""
    with _public_client(transport) as http:
        e = _embed_entity(http, "track", track_id)
    try:
        return _track(track_id, ", ".join(a["name"] for a in e.get("artists", [])), e["title"], e["duration"] / 1000)
    except (TypeError, KeyError) as err:
        raise SpotifyError("format de la page Spotify inattendu : l'import public est peut-être à mettre à jour") from err


def public_collection(url: str, transport: httpx.BaseTransport | None = None) -> tuple[str, list[dict], int | None]:
    """Sans connexion, par la page d'intégration publique : (nom, pistes, nombre total de titres si tronqué)."""
    m = LINK_RE.search(url)
    if not m:
        raise SpotifyError(f"lien Spotify non reconnu : {url}")
    kind, sid = m[1], m[2]
    with _public_client(transport) as http:
        entity = _embed_entity(http, kind, sid)
        try:
            name, items = entity["name"], entity.get("trackList", [])
            tracks = [_track(t["uri"].rsplit(":", 1)[1], t.get("subtitle", "").replace("\xa0", " "), t["title"],
                             t["duration"] / 1000)
                      for t in items if t.get("uri", "").startswith("spotify:track:")]  # pas les épisodes
        except (TypeError, KeyError) as e:
            raise SpotifyError("format de la page Spotify inattendu : l'import public est peut-être à mettre à jour") from e
        total = None
        if len(tracks) >= EMBED_LIMIT:  # peut-être tronquée : le vrai total est sur la page publique
            page = http.get(PAGE_URL.format(kind=kind, id=sid))
            count = re.search(r'<meta name="music:song_count" content="(\d+)"', page.text)
            total = int(count[1]) if count else None
    return name, tracks, total


def authorize(client_id: str, open_browser=webbrowser.open, timeout: float = 180,
              transport: httpx.BaseTransport | None = None) -> str:
    """Connexion dans le navigateur, retour sur un serveur local. Renvoie le refresh token."""
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    result: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            url = urlparse(self.path)
            if url.path != "/callback":
                self.send_error(404)
                return
            result.update({k: v[0] for k, v in parse_qs(url.query).items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write("Connexion Spotify terminée, tu peux fermer cet onglet.".encode())

        def log_message(self, *args):
            pass

    with HTTPServer((REDIRECT_HOST, REDIRECT_PORT), Handler) as server:
        server.timeout = 1
        open_browser(AUTH_URL + "?" + urlencode({
            "client_id": client_id, "response_type": "code", "redirect_uri": REDIRECT_URI, "scope": SCOPES,
            "code_challenge_method": "S256", "code_challenge": challenge, "state": state}))
        deadline = time.monotonic() + timeout
        while not result and time.monotonic() < deadline:
            server.handle_request()
    if not result:
        raise SpotifyError("pas de réponse de Spotify (délai dépassé)")
    if result.get("state") != state:
        raise SpotifyError("réponse Spotify invalide (state)")
    if "error" in result:
        raise SpotifyError(f"connexion refusée : {result['error']}")
    with httpx.Client(timeout=20, transport=transport) as http:
        r = http.post(TOKEN_URL, data={"grant_type": "authorization_code", "code": result["code"],
                                       "redirect_uri": REDIRECT_URI, "client_id": client_id,
                                       "code_verifier": verifier})
    if r.status_code != 200:
        raise SpotifyError(f"échange du code refusé : {r.text[:200]}")
    return r.json()["refresh_token"]


class Spotify:
    def __init__(self, client_id: str, refresh_token: str, on_new_refresh_token=None,
                 transport: httpx.BaseTransport | None = None):
        self.client_id = client_id
        self.refresh_token = refresh_token
        self.on_new_refresh_token = on_new_refresh_token or (lambda t: None)
        self._http = httpx.Client(base_url=API, timeout=20, transport=transport)
        self._access = None
        self._expires = 0.0

    def _token(self) -> str:
        if time.monotonic() >= self._expires - 60:
            r = self._http.post(TOKEN_URL, data={"grant_type": "refresh_token", "refresh_token": self.refresh_token,
                                                 "client_id": self.client_id})
            if r.status_code != 200:
                raise SpotifyError("connexion Spotify expirée : reconnecte-toi dans les Réglages")
            data = r.json()
            self._access = data["access_token"]
            self._expires = time.monotonic() + data["expires_in"]
            if data.get("refresh_token", self.refresh_token) != self.refresh_token:
                self.refresh_token = data["refresh_token"]
                self.on_new_refresh_token(self.refresh_token)
        return self._access

    def _get(self, url: str, params: dict | None = None) -> dict:
        for _ in range(3):
            r = self._http.get(url, params=params, headers={"Authorization": f"Bearer {self._token()}"})
            wait = int(r.headers.get("retry-after", "1"))
            if r.status_code == 429 and wait <= 30:
                time.sleep(wait)
                continue
            if r.status_code != 200:
                try:
                    message = r.json()["error"]["message"]
                except (ValueError, KeyError, TypeError):
                    message = r.text[:200]
                raise SpotifyError(f"Spotify {r.status_code} : {message}")
            return r.json()
        raise SpotifyError("Spotify : trop de requêtes, réessaie plus tard")

    def track(self, track_id: str) -> dict:
        return self._get(f"/tracks/{track_id}")

    def track_info(self, track_id: str) -> dict:
        """Même format que les pistes de collection(), ISRC compris."""
        return self._to_track(self.track(track_id))

    @staticmethod
    def _to_track(t: dict) -> dict:
        return _track(t["id"], ", ".join(a["name"] for a in t.get("artists", [])), t["name"], t["duration_ms"] / 1000,
                      (t.get("external_ids") or {}).get("isrc"))

    def collection(self, url: str) -> tuple[str, list[dict]]:
        """(nom, pistes) ; chaque piste : id, artist, title, duration (s), url, isrc (si connu)."""
        m = LINK_RE.search(url)
        if not m:
            raise SpotifyError(f"lien Spotify non reconnu : {url}")
        kind, sid = m[1], m[2]
        meta = self._get(f"/{kind}s/{sid}")
        if kind == "playlist":
            if "items" not in meta and "tracks" not in meta:
                raise NotOwnedError(NOT_OWNED)
            try:
                page = self._get(f"/playlists/{sid}/items", {"limit": 50})
            except SpotifyError as e:
                raise NotOwnedError(NOT_OWNED) if " 403 " in str(e) else e
        else:
            page = self._get(f"/albums/{sid}/tracks", {"limit": 50})
        tracks = []
        while page:
            for entry in page["items"]:
                t = (entry.get("item") or entry.get("track")) if kind == "playlist" else entry
                if not t or t.get("type") != "track" or t.get("is_local") or not t.get("id"):
                    continue  # épisodes de podcast, fichiers locaux
                tracks.append(self._to_track(t))
            page = self._get(page["next"]) if page.get("next") else None
        return meta["name"], tracks
