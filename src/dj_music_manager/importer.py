"""Import d'un album/playlist ou d'un titre Deezer ou Spotify : aperçu dédoublonné, puis création des jobs."""

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import httpx

from .api_client import ApiClient
from .deezer import COLLECTION_RE, DeezerPublic
from .dedup import norm_key, same_duration
from .library import Library
from .spotify import (LINK_RE, TRACK_LINK_RE, NotOwnedError, RateLimitedError, Spotify, SpotifyError, is_spotify_link,
                      public_collection, public_track, track_ids_in)

DEEZER_TRACK_RE = re.compile(r"deezer\.com/(?:[a-z]{2}/)?track/(\d+)")

PASTE_HINT = ("Dans l'app Spotify : ouvre la playlist, Ctrl+A puis Ctrl+C, "
              "puis clique sur « Coller la liste complète ».")


def parse_duration(s: str | None) -> int | None:
    """'5:20' ou '1:02:03' -> secondes."""
    if not s:
        return None
    secs = 0
    for part in s.split(":"):
        secs = secs * 60 + int(part)
    return secs


def canonical_source(url: str) -> str:
    """Même album/playlist quel que soit le lien partagé (« ?si=… » de Spotify, /intl-fr/, /fr/…)."""
    if m := LINK_RE.search(url):
        return f"https://open.spotify.com/{m[1]}/{m[2]}"
    if m := COLLECTION_RE.search(url):
        return f"https://www.deezer.com/{m[1]}/{m[2]}"
    return url.strip()


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
    warning: str | None = None  # ex. playlist Spotify publique tronquée à 100 titres
    total: int | None = None  # vrai nombre de titres quand la liste est tronquée
    single: bool = False  # un titre seul : pas de playlist

    @property
    def new(self) -> list[dict]:
        return [t for t in self.tracks if t["id"] not in self.existing]


def _spotify_preview(url: str, spotify: Spotify | None) -> Preview:
    """API officielle si connecté et autorisé, sinon page publique (100 premiers titres)."""
    if spotify is not None:
        try:
            title, tracks = spotify.collection(url)
            return Preview(url, title, tracks, "spotify")
        except (NotOwnedError, RateLimitedError):
            pass  # playlist publique de quelqu'un d'autre, ou quota de l'API dépassé
    title, tracks, total = public_collection(url)
    p = Preview(url, title, tracks, "spotify")
    if total and total > len(tracks):
        p.total = total
        p.warning = (f"Spotify ne rend publics que les {len(tracks)} premiers titres de cette playlist "
                     f"(sur {total}), et le bot n'en télécharge pas plus de 100 par playlist. Pour avoir les "
                     f"{total - len(tracks)} autres : " + PASTE_HINT[0].lower() + PASTE_HINT[1:])
    return p


def _spotify_track(spotify: Spotify | None, track_id: str, lib: Library, fetch_public=None) -> dict:
    """Cache, puis API officielle si connecté (ISRC compris), page publique sinon ou si le quota est dépassé."""
    hit = lib.spotify_cached(track_id)
    if hit and "title" in hit:  # sinon entrée partielle (ISRC seul)
        return hit
    t = None
    if spotify is not None:
        try:
            t = spotify.track_info(track_id)
        except RateLimitedError:
            pass
    t = t or (fetch_public or public_track)(track_id)
    lib.cache_spotify({**t, "isrc": t.get("isrc") or (hit or {}).get("isrc")})
    return t


def _isrc_lookup(spotify: Spotify | None, lib: Library):
    if spotify is None:
        return lambda track_id: None  # sans connexion : artiste + titre ± 3 s, puis ISRC des tags au téléchargement

    def lookup(track_id):
        hit = lib.spotify_cached(track_id) or {"id": track_id}
        if hit.get("isrc") is not None:  # "" : déjà demandé, Spotify n'en a pas
            return hit["isrc"] or None
        isrc = (spotify.track(track_id).get("external_ids") or {}).get("isrc")
        lib.cache_spotify({**hit, "isrc": isrc or ""})
        return isrc
    return lookup


def _dedup(p: Preview, lib: Library, isrc_lookup, on_progress) -> Preview:
    for i, t in enumerate(p.tracks, 1):
        track_id = find_existing(lib, p.provider, t, isrc_lookup)
        if track_id is not None:
            p.existing[t["id"]] = track_id
        if on_progress:
            on_progress(i, len(p.tracks))
    return p


def preview_pasted(base: Preview, text: str, lib: Library, spotify: Spotify | None = None,
                   on_progress=None, fetch_public=public_track) -> Preview:
    """Liste complète d'une playlist Spotify à partir des liens copiés dans l'app (Ctrl+A, Ctrl+C).
    Garde le lien et le nom de la playlist analysée ; ne récupère que les titres inconnus de l'aperçu."""
    ids = track_ids_in(text)
    if not ids:
        raise SpotifyError(f"Le presse-papiers ne contient aucun lien de titre Spotify. {PASTE_HINT}")
    known = {t["id"]: t for t in base.tracks}
    if known and not known.keys() & set(ids):
        raise SpotifyError("Les titres copiés ne correspondent pas à cette playlist : as-tu copié la bonne ?")
    missing = [i for i in ids if i not in known]

    def get(track_id):
        try:
            return _spotify_track(spotify, track_id, lib, fetch_public)
        except (SpotifyError, httpx.HTTPError):
            return None  # titre retiré de Spotify : on l'ignore plutôt que d'abandonner tout le collage

    fetched = {}
    # pages publiques : ~1 s chacune, 4 en parallèle restent raisonnables pour Spotify
    with ThreadPoolExecutor(4) as pool:
        for n, (track_id, t) in enumerate(zip(missing, pool.map(get, missing)), 1):
            fetched[track_id] = t
            if on_progress:
                on_progress(n, len(missing))
    tracks = [t for t in (known.get(i) or fetched[i] for i in ids) if t]
    p = Preview(base.url, base.title, tracks, "spotify")
    warnings = []
    if len(tracks) < len(ids):
        warnings.append(f"{len(ids) - len(tracks)} titre(s) introuvable(s) sur Spotify, ignoré(s).")
    if base.total and len(ids) < base.total:
        warnings.append(f"{len(ids)} titres collés sur {base.total} : tous les titres étaient-ils sélectionnés "
                        "(Ctrl+A) dans Spotify ?")
    p.warning = " ".join(warnings) or None
    return _dedup(p, lib, _isrc_lookup(spotify, lib), None)


def preview(api: ApiClient, deezer: DeezerPublic, lib: Library, url: str, on_progress=None,
            spotify: Spotify | None = None) -> Preview:
    url = canonical_source(url)  # sinon chaque lien de partage créerait une nouvelle playlist
    if is_spotify_link(url):
        p = _spotify_preview(url, spotify)
        isrc_lookup = _isrc_lookup(spotify, lib)
    else:
        tracks = [{**t, "duration": parse_duration(t.get("duration"))} for t in api.tracks(url)]
        try:
            title = deezer.collection_title(url)
        except Exception:
            title = url
        p = Preview(url, title, tracks)
        isrc_lookup = lambda track_id: deezer.track(track_id).get("isrc")
    return _dedup(p, lib, isrc_lookup, on_progress)


def is_link(text: str) -> bool:
    """Lien (ou URI Spotify) plutôt qu'une recherche « artiste titre »."""
    return "://" in text or re.search(r"spotify\.com/|deezer\.com/|^spotify:", text) is not None


def track_preview(deezer: DeezerPublic, lib: Library, link: str | None = None, chosen: dict | None = None,
                  spotify: Spotify | None = None) -> Preview | None:
    """Un titre seul : lien de piste Spotify ou Deezer, ou résultat de /search choisi.
    None si le lien n'est pas un lien de piste."""
    if chosen is not None:
        t = {**chosen, "duration": parse_duration(chosen.get("duration")), "isrc": None}
        provider = "deezer"
    elif m := TRACK_LINK_RE.search(link):
        t = _spotify_track(spotify, m[1], lib)
        provider = "spotify"
    elif m := DEEZER_TRACK_RE.search(link):
        d = deezer.track(m[1])
        t = {"id": str(d["id"]), "artist": d["artist"]["name"], "title": d["title"], "duration": float(d["duration"]),
             "url": f"https://www.deezer.com/track/{d['id']}", "isrc": d.get("isrc")}
        provider = "deezer"
    else:
        return None
    p = Preview(t["url"], f"{t['artist']} – {t['title']}", [t], provider, single=True)
    isrc_lookup = _isrc_lookup(spotify, lib) if provider == "spotify" else lambda i: deezer.track(i).get("isrc")
    return _dedup(p, lib, isrc_lookup, None)


def match_collection(entries: list[dict], tracks: list[dict]) -> tuple[dict[str, dict], list[dict]]:
    """Rapproche les fichiers de /collection des pistes attendues : artiste + titre normalisés et durée ± 3 s,
    puis titre seul (artistes écrits autrement). Renvoie ({ID de piste: fichier}, fichiers sans piste attendue)."""
    def title_key(key: str | None) -> str | None:
        return key.split("|", 1)[1] if key else None

    remaining = list(entries)
    matched: dict[str, dict] = {}
    for key_of in (lambda a, t: norm_key(a, t), lambda a, t: title_key(norm_key(a or "?", t))):
        for t in tracks:
            if t["id"] in matched:
                continue
            key = key_of(t["artist"], t["title"])
            e = next((e for e in remaining if key and key_of(e.get("artist"), e.get("title")) == key
                      and same_duration(parse_duration(e.get("duration")), t["duration"])), None)
            if e is not None:
                remaining.remove(e)
                matched[t["id"]] = e
    return matched, remaining


def queue(lib: Library, p: Preview, quality: str) -> list[int]:
    """Crée/maj la playlist (références, jamais de copie) et renvoie les ID des jobs à lancer."""
    playlist_id = None
    if not p.single:
        playlist_id = lib.playlist_for_source(p.url, p.title)
        lib.set_playlist_tracks(playlist_id, [(p.existing[t["id"]], i) for i, t in enumerate(p.tracks)
                                              if t["id"] in p.existing])
        lib.drop_failed_jobs(playlist_id)
    return lib.add_jobs([
        {"playlist_id": playlist_id, "position": i, "provider": p.provider, "external_id": t["id"], "url": t["url"],
         "artist": t["artist"], "title": t["title"], "expected_duration": t["duration"], "quality": quality}
        for i, t in enumerate(p.tracks) if t["id"] not in p.existing
    ])
