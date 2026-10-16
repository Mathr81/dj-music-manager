"""Import d'un album/playlist Deezer : aperçu dédoublonné, puis création des jobs de téléchargement."""

from dataclasses import dataclass, field

from .api_client import ApiClient
from .deezer import DeezerPublic
from .library import Library


def parse_duration(s: str | None) -> int | None:
    """'5:20' ou '1:02:03' -> secondes."""
    if not s:
        return None
    secs = 0
    for part in s.split(":"):
        secs = secs * 60 + int(part)
    return secs


def find_existing(deezer: DeezerPublic, lib: Library, t: dict) -> int | None:
    """Ordre : ID Deezer -> ISRC (API publique Deezer) -> artiste + titre normalisés ± 3 s."""
    track_id = lib.track_by_external("deezer", t["id"])
    if track_id is not None:
        return track_id
    try:
        isrc = deezer.track(t["id"]).get("isrc")
    except Exception:
        isrc = None  # Deezer injoignable : on continue avec artiste + titre
    track_id = lib.find_track(isrc, t["artist"], t["title"], parse_duration(t.get("duration")))
    if track_id is not None:
        lib.add_external("deezer", t["id"], track_id)  # la prochaine fois, trouvé dès l'étape 1
    return track_id


@dataclass
class Preview:
    url: str
    title: str
    tracks: list[dict]  # tel que renvoyé par /tracks
    existing: dict[str, int] = field(default_factory=dict)  # ID Deezer -> ID interne

    @property
    def new(self) -> list[dict]:
        return [t for t in self.tracks if t["id"] not in self.existing]


def preview(api: ApiClient, deezer: DeezerPublic, lib: Library, url: str, on_progress=None) -> Preview:
    tracks = api.tracks(url)
    try:
        title = deezer.collection_title(url)
    except Exception:
        title = url
    p = Preview(url, title, tracks)
    for i, t in enumerate(tracks, 1):
        track_id = find_existing(deezer, lib, t)
        if track_id is not None:
            p.existing[t["id"]] = track_id
        if on_progress:
            on_progress(i, len(tracks))
    return p


def queue(lib: Library, p: Preview, quality: str) -> list[int]:
    """Crée/maj la playlist (références, jamais de copie) et renvoie les ID des jobs à lancer."""
    playlist_id = lib.playlist_for_source(p.url, p.title)
    lib.set_playlist_tracks(playlist_id, [(p.existing[t["id"]], i) for i, t in enumerate(p.tracks)
                                          if t["id"] in p.existing])
    lib.drop_failed_jobs(playlist_id)
    return lib.add_jobs([
        {"playlist_id": playlist_id, "position": i, "provider": "deezer", "external_id": t["id"], "url": t["url"],
         "artist": t["artist"], "title": t["title"], "expected_duration": parse_duration(t.get("duration")),
         "quality": quality}
        for i, t in enumerate(p.tracks) if t["id"] not in p.existing
    ])
