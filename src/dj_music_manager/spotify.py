"""API Web Spotify : pistes d'un album ou d'une playlist, connexion OAuth PKCE (sans secret client).

Depuis février 2026, une app en mode développeur ne reçoit le contenu que des playlists possédées
ou co-éditées par l'utilisateur connecté, et son propriétaire doit avoir Spotify Premium.
"""

import base64
import hashlib
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
NOT_OWNED = ("Spotify ne donne le contenu que des playlists que tu possèdes ou co-édites. "
             "Copie ses titres dans une playlist à toi (Ctrl+A, « Ajouter à la playlist ») puis réessaie.")


class SpotifyError(Exception):
    pass


def is_spotify_link(url: str) -> bool:
    return LINK_RE.search(url) is not None


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

    def collection(self, url: str) -> tuple[str, list[dict]]:
        """(nom, pistes) ; chaque piste : id, artist, title, duration (s), url, isrc (si connu)."""
        m = LINK_RE.search(url)
        if not m:
            raise SpotifyError(f"lien Spotify non reconnu : {url}")
        kind, sid = m[1], m[2]
        meta = self._get(f"/{kind}s/{sid}")
        if kind == "playlist":
            if "items" not in meta and "tracks" not in meta:
                raise SpotifyError(NOT_OWNED)
            try:
                page = self._get(f"/playlists/{sid}/items", {"limit": 50})
            except SpotifyError as e:
                raise SpotifyError(NOT_OWNED) if " 403 " in str(e) else e
        else:
            page = self._get(f"/albums/{sid}/tracks", {"limit": 50})
        tracks = []
        while page:
            for entry in page["items"]:
                t = (entry.get("item") or entry.get("track")) if kind == "playlist" else entry
                if not t or t.get("type") != "track" or t.get("is_local") or not t.get("id"):
                    continue  # épisodes de podcast, fichiers locaux
                tracks.append({
                    "id": t["id"],
                    "artist": ", ".join(a["name"] for a in t.get("artists", [])),
                    "title": t["name"],
                    "duration": t["duration_ms"] / 1000,
                    "url": f"https://open.spotify.com/track/{t['id']}",
                    "isrc": (t.get("external_ids") or {}).get("isrc"),
                })
            page = self._get(page["next"]) if page.get("next") else None
        return meta["name"], tracks
