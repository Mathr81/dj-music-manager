import json
import threading
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from dj_music_manager.spotify import (NOT_OWNED, REDIRECT_URI, Spotify, SpotifyError, authorize, is_spotify_link,
                                      public_collection)

PID = "37i9dQZF1DXcBWIGoYBM5M"


def track(i, **extra):
    return {"type": "track", "id": f"{i:022d}", "name": f"Titre {i}", "duration_ms": 200_000 + i * 1000,
            "artists": [{"name": "Artiste"}, {"name": "Invité"}], "external_ids": {"isrc": f"ISRC{i}"}, **extra}


def spotify_client(routes: dict, tokens: list) -> Spotify:
    def handler(request):
        if request.url.host == "accounts.spotify.com":
            tokens.append(parse_qs(request.content.decode()))
            return httpx.Response(200, json={"access_token": "A", "expires_in": 3600, "refresh_token": "R2"})
        assert request.headers["authorization"] == "Bearer A"
        key = request.url.path + ("?offset=" + request.url.params["offset"] if "offset" in request.url.params else "")
        status, body = routes[key]
        return httpx.Response(status, json=body)

    rotated = []
    s = Spotify("cid", "R1", rotated.append, transport=httpx.MockTransport(handler))
    s.rotated = rotated
    return s


@pytest.mark.parametrize("url, ok", [
    (f"https://open.spotify.com/playlist/{PID}?si=abc", True),
    (f"https://open.spotify.com/intl-fr/album/{PID}", True),
    (f"spotify:playlist:{PID}", True),
    (f"https://open.spotify.com/track/{PID}", False),
    ("https://www.deezer.com/fr/playlist/123", False),
])
def test_is_spotify_link(url, ok):
    assert is_spotify_link(url) is ok


def test_playlist_paginated_skips_local_files_and_episodes():
    tokens = []
    s = spotify_client({
        f"/v1/playlists/{PID}": (200, {"name": "Warmup", "items": {"total": 4}}),
        f"/v1/playlists/{PID}/items": (200, {
            "items": [{"item": track(1)}, {"item": track(2, is_local=True)}, {"item": {"type": "episode", "id": "x"}}],
            "next": f"https://api.spotify.com/v1/playlists/{PID}/items?offset=50&limit=50"}),
        f"/v1/playlists/{PID}/items?offset=50": (200, {"items": [{"item": track(3)}, {"item": None}], "next": None}),
    }, tokens)
    title, tracks = s.collection(f"https://open.spotify.com/playlist/{PID}")
    assert title == "Warmup"
    assert [t["id"] for t in tracks] == [f"{1:022d}", f"{3:022d}"]
    assert tracks[0] == {"id": f"{1:022d}", "artist": "Artiste, Invité", "title": "Titre 1", "duration": 201.0,
                         "url": f"https://open.spotify.com/track/{1:022d}", "isrc": "ISRC1"}
    assert len(tokens) == 1 and tokens[0]["grant_type"] == ["refresh_token"]  # jeton réutilisé
    assert s.rotated == ["R2"]  # le nouveau refresh token est remonté pour être stocké


def test_playlist_not_owned_explains_the_limit():
    s = spotify_client({f"/v1/playlists/{PID}": (200, {"name": "Today's Top Hits"})}, [])
    with pytest.raises(SpotifyError, match="possèdes ou co-édites"):
        s.collection(f"https://open.spotify.com/playlist/{PID}")
    s = spotify_client({f"/v1/playlists/{PID}": (200, {"name": "X", "items": {}}),
                        f"/v1/playlists/{PID}/items": (403, {"error": {"status": 403, "message": "Forbidden"}})}, [])
    with pytest.raises(SpotifyError) as e:
        s.collection(f"https://open.spotify.com/playlist/{PID}")
    assert str(e.value) == NOT_OWNED


def test_album_tracks():
    album_track = {k: v for k, v in track(7).items() if k != "external_ids"}  # pistes simplifiées : pas d'ISRC
    s = spotify_client({f"/v1/albums/{PID}": (200, {"name": "Discovery"}),
                        f"/v1/albums/{PID}/tracks": (200, {"items": [album_track], "next": None})}, [])
    title, tracks = s.collection(f"https://open.spotify.com/album/{PID}")
    assert (title, tracks[0]["title"], tracks[0]["isrc"]) == ("Discovery", "Titre 7", None)


def test_authorize_pkce_roundtrip():
    exchanged = []

    def token_handler(request):
        exchanged.append(parse_qs(request.content.decode()))
        return httpx.Response(200, json={"access_token": "A", "refresh_token": "R", "expires_in": 3600})

    def fake_browser(url):  # simule Spotify qui redirige vers le serveur local
        q = parse_qs(urlparse(url).query)
        assert q["code_challenge_method"] == ["S256"] and q["redirect_uri"] == [REDIRECT_URI]
        threading.Thread(target=lambda: httpx.get(f"{REDIRECT_URI}?code=C0DE&state={q['state'][0]}")).start()

    assert authorize("cid", fake_browser, timeout=10, transport=httpx.MockTransport(token_handler)) == "R"
    body = exchanged[0]
    assert body["code"] == ["C0DE"] and body["client_id"] == ["cid"] and len(body["code_verifier"][0]) >= 43


def embed_page(name: str, items: list[dict]) -> str:
    data = {"props": {"pageProps": {"state": {"data": {"entity": {"name": name, "trackList": items}}}}}}
    return f'<html><script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script></html>'


def embed_item(i: int, artists: str = "Vacra,\xa0La Rvfleuze") -> dict:
    return {"uri": f"spotify:track:{i:022d}", "title": f"Titre {i}", "subtitle": artists, "duration": 169586}


def public_transport(embed_html: str, song_count: int | None = None, status: int = 200):
    def handler(request):
        if request.url.path.startswith("/embed/"):
            return httpx.Response(status, text=embed_html)
        meta = f'<meta name="music:song_count" content="{song_count}"/>' if song_count else ""
        return httpx.Response(200, text=f"<html><head>{meta}</head></html>")
    return httpx.MockTransport(handler)


def test_public_playlist_without_login():
    items = [embed_item(1), {"uri": "spotify:episode:xyz", "title": "Podcast", "subtitle": "", "duration": 1}]
    name, tracks, total = public_collection(f"https://open.spotify.com/playlist/{PID}?si=abc",
                                            transport=public_transport(embed_page("ÉTÉ 2K26", items)))
    assert (name, total) == ("ÉTÉ 2K26", None)  # moins de 100 titres : liste complète
    assert tracks == [{"id": f"{1:022d}", "artist": "Vacra, La Rvfleuze", "title": "Titre 1", "duration": 169.586,
                       "url": f"https://open.spotify.com/track/{1:022d}", "isrc": None}]


def test_public_playlist_truncated_at_100_reports_real_total():
    html = embed_page("ÉTÉ 2K26", [embed_item(i) for i in range(100)])
    _, tracks, total = public_collection(f"https://open.spotify.com/playlist/{PID}",
                                         transport=public_transport(html, song_count=297))
    assert (len(tracks), total) == (100, 297)


@pytest.mark.parametrize("html, status, message", [
    ("<html>pas de données</html>", 200, "format de la page Spotify inattendu"),
    ("", 404, "introuvable"),
])
def test_public_playlist_errors_are_explicit(html, status, message):
    with pytest.raises(SpotifyError, match=message):
        public_collection(f"https://open.spotify.com/playlist/{PID}", transport=public_transport(html, status=status))
