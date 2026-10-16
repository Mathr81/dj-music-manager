"""Import d'un album/playlist Deezer ou Spotify : aperçu dédoublonné, puis création des jobs de téléchargement."""

from dataclasses import dataclass, field

from .api_client import ApiClient
from .deezer import DeezerPublic
from .library import Library
from .spotify import Spotify, SpotifyError, is_spotify_link


def parse_duration(s: str | None) -> int | None:
    """'5:20' ou '1:02:03' -> secondes."""
    if not s:
        return None
    secs = 0
    for part in s.split(":"):
        secs = secs * 60 + int(part)
    return secs


def find_existing(lib: Library, provider: str, t: dict, isrc_lookup) -> int | None:
    """Ordre : ID de la source -> ISRC -> artiste + titre normalisés ± 3 s."""
    track_id = lib.track_by_external(provider, t["id"])
    if track_id is not None:
        return track_id
    isrc = t.get("isrc")
    if not isrc:
        try:
            isrc = isrc_lookup(t["id"])
        except Exception:
            isrc = None  # source injoignable : on continue avec artiste + titre
    track_id = lib.find_track(isrc, t["artist"], t["title"], t["duration"])
    if track_id is not None:
        lib.add_external(provider, t["id"], track_id)  # la prochaine fois, trouvé dès l'étape 1
    return track_id


@dataclass
class Preview:
    url: str
    title: str
    tracks: list[dict]  # id, artist, title, duration (s), url, isrc éventuel
    provider: str = "deezer"
    existing: dict[str, int] = field(default_factory=dict)  # ID source -> ID interne

    @property
    def new(self) -> list[dict]:
        return [t for t in self.tracks if t["id"] not in self.existing]


def preview(api: ApiClient, deezer: DeezerPublic, lib: Library, url: str, on_progress=None,
            spotify: Spotify | None = None) -> Preview:
    if is_spotify_link(url):
        if spotify is None:
            raise SpotifyError("Spotify n'est pas connecté : configure-le dans les Réglages")
        title, tracks = spotify.collection(url)
        p = Preview(url, title, tracks, "spotify")
        isrc_lookup = lambda track_id: (spotify.track(track_id).get("external_ids") or {}).get("isrc")
    else:
        tracks = [{**t, "duration": parse_duration(t.get("duration"))} for t in api.tracks(url)]
        try:
            title = deezer.collection_title(url)
        except Exception:
            title = url
        p = Preview(url, title, tracks)
        isrc_lookup = lambda track_id: deezer.track(track_id).get("isrc")
    for i, t in enumerate(p.tracks, 1):
        track_id = find_existing(lib, p.provider, t, isrc_lookup)
        if track_id is not None:
            p.existing[t["id"]] = track_id
        if on_progress:
            on_progress(i, len(p.tracks))
    return p


def queue(lib: Library, p: Preview, quality: str) -> list[int]:
    """Crée/maj la playlist (références, jamais de copie) et renvoie les ID des jobs à lancer."""
    playlist_id = lib.playlist_for_source(p.url, p.title)
    lib.set_playlist_tracks(playlist_id, [(p.existing[t["id"]], i) for i, t in enumerate(p.tracks)
                                          if t["id"] in p.existing])
    lib.drop_failed_jobs(playlist_id)
    return lib.add_jobs([
        {"playlist_id": playlist_id, "position": i, "provider": p.provider, "external_id": t["id"], "url": t["url"],
         "artist": t["artist"], "title": t["title"], "expected_duration": t["duration"], "quality": quality}
        for i, t in enumerate(p.tracks) if t["id"] not in p.existing
    ])
