# Music DL API — référence pour intégration

Document destiné à un développeur ou un agent IA qui construit un client de cette API.
Tout ce qui est écrit ici reflète le comportement réel du code (`app/main.py`, `app/bot.py`).

## En bref

API HTTP qui télécharge des pistes musicales (FLAC ou MP3) via des bots Telegram.
Elle renvoie un fichier audio tagué, nommé `Artiste - Titre.ext`, avec sa qualité **mesurée** dans les en-têtes.

- **Base URL** : celle de ton serveur, à saisir dans les Réglages de l'app (ci-dessous `$BASE`).
- **Format** : JSON pour tout, sauf `/download` et `/file` qui renvoient le binaire audio.
- **Spécification OpenAPI** : `$BASE/openapi.json` ; doc interactive : `$BASE/docs`.

## Authentification

Chaque requête (sauf `/health`, `/docs`, `/openapi.json`) doit porter l'en-tête :

```
X-API-Key: <clé>
```

Clé absente ou fausse → `401 {"detail": "clé API invalide"}`. Pas de clé en paramètre d'URL.

## Endpoints

### `GET /health`

Sans authentification. `200 {"ok": true}` si la connexion Telegram est active.

### `GET /search?q=<texte>`

Recherche de pistes sur Deezer (seule source de recherche disponible).

| Paramètre | Type | Contraintes |
|---|---|---|
| `q` | string | obligatoire, 1 à 200 caractères |

Réponse `200` : tableau (au plus 7 éléments, ordre de pertinence du bot ; tableau vide si aucun résultat).

```json
[
  {
    "id": "69879250",
    "source": "deezer",
    "type": "track",
    "artist": "Stromae",
    "title": "Papaoutai",
    "duration": "3:52",
    "url": "https://www.deezer.com/track/69879250"
  }
]
```

- `id` est une **chaîne** (ID de piste Deezer), à réutiliser tel quel dans `/download?id=`.
- `duration` est au format `m:ss` (ou `h:mm:ss`).

### `GET /tracks?url=<lien album ou playlist Deezer>`

Liste les pistes d'un album ou d'une playlist Deezer (via l'API publique Deezer, sans passer par le bot : rapide,
pas de limite de débit côté API). Même format que `/search`, dans l'ordre de l'album/playlist, sans limite de nombre.

| Paramètre | Type | Contraintes |
|---|---|---|
| `url` | string | ≤ 500 car., doit contenir `deezer.com/album/<id>` ou `deezer.com/playlist/<id>` (préfixe de langue `/fr/` accepté) |

- Lien non reconnu (Spotify, lien court `deezer.page.link`, artiste…) → `400`.
- Album/playlist inexistant ou privé → `404`. Deezer injoignable → `502`.

Ensuite, télécharger chaque piste avec `/download?id=` (voir la recette « Télécharger un album »).

### `POST /collections?url=<lien album ou playlist>` (recommandé) et `GET /collection?url=…`

Envoie le lien **entier** au bot, qui renvoie toutes les pistes d'un coup : bien plus rapide que piste par piste,
et c'est la seule voie pour une **playlist Spotify**. Les fichiers ne sont pas encore téléchargés : récupérer
chacun avec `GET <file>` (voir `/file`).

- `POST /collections` répond tout de suite (`202`, `status: "running"`). Suivre ensuite avec
  `GET /collections/{id}` toutes les 3 à 5 s : `tracks` grandit au fur et à mesure, et on peut télécharger
  les pistes déjà listées sans attendre la fin. Le suivi se termine quand `status` vaut `done` ou `error`
  (`error` contient alors le message).
- `GET /collection` attend la fin et renvoie le même objet. Au-delà d'environ 100 titres, il risque le timeout du
  proxy (300 s) : utiliser `POST /collections`.
- Si `bot` change pendant le suivi (le bot principal a échoué, le secours prend le relais), la liste `tracks`
  repart de zéro.
- `GET /collections/{id}` → `404` si l'id est inconnu ou si l'API a redémarré (les collectes sont en mémoire).

| Paramètre | Type | Contraintes |
|---|---|---|
| `url` | string | ≤ 500 car., `http(s)://`. Testé : playlist et album Spotify, album et playlist Deezer |
| `quality` | `flac` \| `mp3_320` \| `mp3_128` | optionnel, comme `/download` (bot principal seulement) |

```json
{
  "id": "411471541bbd635f",
  "status": "done",
  "error": null,
  "bot": "DeezerMusicBot",
  "tracks": [
    {"file": "/file/DeezerMusicBot/4625", "artist": "Daft Punk", "title": "Lose Yourself to Dance",
     "duration": "5:53", "size": 14213781, "mime": "audio/mpeg"}
  ]
}
```

- Ordre de l'album/playlist. Le bot principal retire les doublons, pas le bot de secours.
- Une piste introuvable chez le bot est simplement absente : comparer avec la liste attendue si besoin.
- Durée : 10 à 35 s pour 45 pistes, plus 60 s de silence à la fin quand une piste manque chez le bot de secours.
  Pendant ce temps, les autres requêtes vers le même bot attendent.
- Erreurs : comme `/download` (`422` refus du bot, `504` bot muet).

### `GET /file/{bot}/{id}`

Télécharge un fichier listé par `/collection`, en prenant directement le chemin `file`. Réponse identique à
`/download` (binaire, nom `Artiste - Titre.ext`, mêmes en-têtes `X-…`, même cache). Inconnu → `404`.
Le chemin reste valable tant que le message existe dans la conversation Telegram avec le bot.

### `GET /download`

Télécharge une piste. Fournir **exactement un** des trois paramètres :

| Paramètre | Type | Contraintes | Effet |
|---|---|---|---|
| `id` | string | chiffres uniquement, 1 à 20 | piste Deezer par ID (le plus fiable) |
| `url` | string | `http(s)://…`, ≤ 500 car., pas d'album/playlist/artiste | lien de piste (Deezer, Spotify…) |
| `q` | string | 1 à 200 caractères | recherche puis télécharge le 1er résultat |

Paramètre optionnel, combinable avec les trois ci-dessus :

| Paramètre | Valeurs | Effet |
|---|---|---|
| `quality` | `flac`, `mp3_320`, `mp3_128` | qualité voulue **pour cette requête** ; absent = réglage global en cours |

**Toujours passer `quality`** : sinon le résultat dépend du dernier réglage fait par n'importe quel client.
Si la qualité demandée diffère du réglage en cours, l'API le change avant la demande (~3 s de plus), sans
qu'un autre client puisse s'intercaler. Si la piste n'existe pas en FLAC, le bot de secours fournit un MP3 320
(`X-Bot: DeezerMusicBot`) : vérifier `Content-Type` / `X-Real-Quality`.

Zéro ou plusieurs paramètres parmi `id`/`url`/`q` → `400`. Une URL contenant `/album/`, `/playlist/`, `/artist/` ou `/sets/` → `400` :
**`/download` ne gère que des pistes seules**. Pour un album ou une playlist : `/tracks` puis `/download?id=` par piste.

Réponse `200` : le fichier audio (corps binaire).

| En-tête | Exemple | Sens |
|---|---|---|
| `Content-Type` | `audio/x-flac`, `audio/mpeg` | format du fichier |
| `Content-Disposition` | `attachment; filename="Stromae - Papaoutai.flac"; filename*=utf-8''Stromae%20-%20Papaoutai.flac` | nom standardisé ; préférer `filename*` (UTF-8, accents) et se replier sur `filename` (ASCII seul) |
| `X-Real-Quality` | `Lossless`, `LOSSY ~320kbps`, `~192kbps (Transcoded to Lossless)`, `~192kbps (Re-encoded)`, `unknown` | qualité mesurée par analyse spectrale |
| `X-Lossless` | `true` / `false` / vide | `true` = vrai lossless ; `false` sur un FLAC = faux FLAC issu d'un MP3 |
| `X-Max-Frequency` | `21563` | fréquence de coupure détectée, en Hz (vide si inconnue) |
| `X-Bot` | `MusicsHuntersbot`, `DeezerMusicBot` | bot qui a fourni le fichier |
| `X-Bot-Quality` | `DE FLAC (CD)`, `DE MP3 (320kbps)` | légende brute du bot (indicative, non garantie) |
| `X-Cache` | `HIT` / `MISS` | `HIT` = servi depuis le cache disque |

Pour connaître la qualité, **se fier à `X-Real-Quality`/`X-Lossless`**, pas à `X-Bot-Quality`.
`unknown` arrive pour les formats non analysables (AAC/M4A).

Le fichier est déjà tagué : titre, artiste(s), album, artiste d'album, n° de piste/total, disque, date `AAAA-MM-JJ`,
genre(s), ISRC, code-barres UPC, label, BPM, compositeur, paroles, copyright, pochette (~1200 px).
Aucun retaggage n'est nécessaire côté client. Rare exception : si la piste est introuvable chez Deezer (recherche
sans ISRC qui ne concorde pas en album et en durée), les champs ISRC, UPC, label et total de pistes peuvent manquer.

### `GET /quality`

Qualité actuellement réglée chez le bot principal, par source.

```json
{"apple": "AAC (256kbps)", "deezer": "FLAC (CD)", "qobuz": "FLAC (Hi-Res)", "tidal": "AAC (320kbps)",
 "beatport": "AAC (256kbps)", "amazon": "Opus (SD)", "kkbox": "AAC (320kbps)"}
```

### `GET /quality/{source}`

`source` ∈ `amazon`, `apple`, `beatport`, `deezer`, `kkbox`, `qobuz`, `tidal` (autre valeur → `404`).

```json
[
  {"value": 0, "label": "MP3 (128kbps)", "selected": false},
  {"value": 1, "label": "MP3 (320kbps)", "selected": false},
  {"value": 2, "label": "FLAC (CD)", "selected": true}
]
```

### `PUT /quality/{source}/{value}`

Change la qualité. `value` = un `value` renvoyé par `GET /quality/{source}`. Pas de corps de requête.
Réponse `200` : la liste d'options mise à jour (même format que ci-dessus, la nouvelle option `selected: true`).
Option inexistante ou refusée par le bot (option réservée aux comptes payants) → `422` avec le message.

En pratique, seul **Deezer** compte (c'est la source de tous les téléchargements sur le compte actuel) :
`0` = MP3 128 kbps, `1` = MP3 320 kbps, `2` = FLAC (défaut).

## Erreurs

Toujours du JSON avec un champ `detail`.

| Code | Quand | `detail` |
|---|---|---|
| `400` | paramètres de `/download` incorrects ; lien d'album/playlist passé à `/download` (→ `/collection`) | chaîne |
| `401` | clé API absente ou fausse | chaîne |
| `404` | `/download?q=` sans résultat ; source de qualité inconnue ; album/playlist introuvable ; `/file` inconnu | chaîne |
| `422` | le bot refuse (piste introuvable, réservée Premium/donation, option de qualité invalide) | **chaîne** : message du bot, ex. `"Track not found or unavailable.\nDebugID: 123"` |
| `422` | validation FastAPI (paramètre trop long, `id` non numérique, `value` non entier…) | **tableau** d'objets `{loc, msg, type}` |
| `502` | `/tracks` : API Deezer injoignable | chaîne |
| `504` | le bot n'a pas répondu à temps | chaîne |
| `500` | erreur interne | — |

Attention : `detail` est une chaîne **ou** un tableau selon l'origine du `422`. Tester le type.
Ne pas réessayer automatiquement un `422` du bot (même réponse garantie) ; un `504` peut être réessayé une fois.

## Comportement à connaître

**Durées typiques**

| Opération | Durée |
|---|---|
| `/search` | 0,3 à 1,5 s |
| `/download`, `X-Cache: HIT` | < 0,5 s |
| `/download`, `MISS`, MP3 (~10 Mo) | 2 à 4 s |
| `/download`, `MISS`, FLAC (30–45 Mo) | 5 à 10 s |
| changement de qualité (`quality=` différent du réglage en cours) | + ~3 s |
| `/download` en échec (bot principal + bot de secours) | jusqu'à ~25 s |
| pire cas avant `504` | ~110 s |
| `/collection`, 45 pistes | 10 à 35 s (puis `/file` : ~90 s pour 450 Mo, 4 en parallèle) |

→ Régler le timeout HTTP du client à **au moins 150 s**, et streamer le corps vers un fichier plutôt que de le garder en mémoire.

**Concurrence**

- Les appels parallèles sont acceptés. Les échanges avec le bot sont sérialisés côté serveur (le bot ignore
  les messages trop rapprochés), soit environ 1 demande traitée par seconde ; les téléchargements de fichiers,
  eux, tournent en parallèle.
- Ne pas dépasser ~4 téléchargements simultanés : le débit Telegram est plafonné à ~6 Mo/s pour tout le compte,
  au-delà ça ne va pas plus vite.

**Bot principal en panne**

Le bot principal peut se bloquer plusieurs minutes, par exemple après une rafale de fichiers, ou sur un
téléchargement qui ne finit pas. Il répond alors « Please wait for the current download… » ou plus rien du tout.
La première requête qui le constate attend son timeout (~25 à 90 s) et passe au bot de secours. Le bot principal
est ensuite mis de côté 2 min (puis 4, 8… jusqu'à 30 min s'il reste muet) : les requêtes vont directement au
secours (`X-Bot: DeezerMusicBot`, MP3 320 seulement, `quality` ignoré). `/search` bascule alors sur l'API Deezer
(mêmes résultats). Aucune action à faire côté client.

**Qualité : utiliser `quality` sur `/download`, pas `PUT /quality`**

Le réglage de qualité du bot est global (un seul compte Telegram pour tous les clients). Le paramètre `quality`
de `/download` le règle et télécharge d'un seul tenant : c'est fiable même avec plusieurs clients en parallèle.
`PUT /quality` ne fait que changer le réglage par défaut, que le prochain `quality=` d'un autre client peut
modifier. Changer de qualité coûte ~3 s : regrouper les téléchargements par qualité quand c'est possible
(les requêtes d'une même qualité à la suite ne paient rien).

**Cache**

Une même piste dans une même qualité est servie depuis le cache (`X-Cache: HIT`). Inutile de mettre en cache
côté client pour la vitesse ; relancer `/download` est bon marché.

**Ce qui marche avec le compte actuel (gratuit)**

- Recherche : Deezer uniquement.
- Téléchargement : IDs et liens Deezer ; liens Spotify (convertis en Deezer par le bot, pas toujours trouvés) ;
  albums et playlists Deezer/Spotify via `/collection`.
- Tidal, Apple Music, Qobuz… : `422` (réservé Premium/donation).
- Le bot de secours ne fournit que du MP3 320 : un `X-Bot: DeezerMusicBot` signifie pas de FLAC pour cette piste.

## Recettes

**Chercher puis télécharger un choix précis** (recommandé : contrôle du résultat, contrairement à `q=`)

```bash
curl -s -H "X-API-Key: $KEY" "$BASE/search?q=daft%20punk%20one%20more%20time"
curl -s -H "X-API-Key: $KEY" -OJ "$BASE/download?id=3135553&quality=flac"   # -J : nom depuis Content-Disposition
```

**Python** (`httpx`)

```python
import re
from urllib.parse import unquote

import httpx

client = httpx.Client(base_url=BASE, headers={"X-API-Key": KEY}, timeout=150)


def filename(resp: httpx.Response) -> str:
    cd = resp.headers["content-disposition"]
    m = re.search(r"filename\*=utf-8''([^;]+)", cd)
    return unquote(m[1]) if m else re.search(r'filename="([^"]*)"', cd)[1]


results = client.get("/search", params={"q": "stromae papaoutai"}).json()
with client.stream("GET", "/download", params={"id": results[0]["id"], "quality": "flac"}) as r:
    if r.status_code != 200:
        r.read()
        raise RuntimeError(f"{r.status_code}: {r.json()['detail']}")
    name = filename(r)
    with open(name, "wb") as f:
        for chunk in r.iter_bytes():
            f.write(chunk)
    print(name, r.headers["x-real-quality"], r.headers["x-lossless"])
```

**JavaScript** (Node 18+)

```js
import fs from "node:fs";

const res = await fetch(`${BASE}/download?id=3135553&quality=flac`, {
  headers: { "X-API-Key": KEY },
  signal: AbortSignal.timeout(150_000),
});
if (!res.ok) throw new Error(`${res.status}: ${JSON.stringify((await res.json()).detail)}`);
const cd = res.headers.get("content-disposition");
const name = decodeURIComponent(cd.match(/filename\*=utf-8''([^;]+)/)[1]);
await fs.promises.writeFile(name, Buffer.from(await res.arrayBuffer()));
console.log(name, res.headers.get("x-real-quality"));
```

**Télécharger un album ou une playlist** (4 en parallèle ; une piste en échec n'arrête pas les autres)

```python
from concurrent.futures import ThreadPoolExecutor


def save(track: dict) -> str:
    with client.stream("GET", "/download", params={"id": track["id"], "quality": "flac"}) as r:
        if r.status_code != 200:
            r.read()
            return f"ÉCHEC {track['artist']} - {track['title']} : {r.json()['detail']}"
        name = filename(r)
        with open(name, "wb") as f:
            for chunk in r.iter_bytes():
                f.write(chunk)
        return f"{name} [{r.headers['x-real-quality']}]"


tracks = client.get("/tracks", params={"url": "https://www.deezer.com/album/302127"}).json()
with ThreadPoolExecutor(4) as pool:
    for line in pool.map(save, tracks):
        print(line)
```

Préfixer les noms par un numéro si l'ordre compte : `f"{i:02d} - {name}"` avec `enumerate(tracks, 1)`.
Compter ~1 min pour un album FLAC de 12 titres hors cache (débit Telegram plafonné).

**Télécharger une playlist Spotify** (un seul appel au bot, téléchargements dès que les pistes arrivent)

```python
import time


def save_file(track: dict) -> str:
    with client.stream("GET", track["file"]) as r:
        if r.status_code != 200:
            r.read()
            return f"ÉCHEC {track['artist']} - {track['title']} : {r.json()['detail']}"
        name = filename(r)
        with open(name, "wb") as f:
            for chunk in r.iter_bytes():
                f.write(chunk)
        return f"{name} [{r.headers['x-real-quality']}]"


job = client.post("/collections", params={"url": "https://open.spotify.com/playlist/2jTy5QwqWJ1ZUv2XeJPYbn",
                                          "quality": "mp3_320"}).json()
seen, futures = set(), []
with ThreadPoolExecutor(4) as pool:
    while True:
        job = client.get(f"/collections/{job['id']}").json()
        for t in job["tracks"]:
            if t["file"] not in seen:
                seen.add(t["file"])
                futures.append(pool.submit(save_file, t))
        if job["status"] != "running":
            break
        time.sleep(4)
for f in futures:
    print(f.result())
if job["status"] == "error":
    print("collecte en échec :", job["error"])
```

**Télécharger en MP3 320**

```bash
curl -s -H "X-API-Key: $KEY" -OJ "$BASE/download?id=69879250&quality=mp3_320"
```
