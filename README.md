# DJ Music Manager

Bibliothèque musicale pour DJ (Windows). Un seul dossier **RAW** contient chaque son une seule fois ;
playlists et tags sont une couche SQLite par-dessus. Réimporter une playlist ne télécharge que ce qui manque.

## Lancer

```bash
uv sync
uv run dj-music-manager      # ou : uv run python -m dj_music_manager
uv run pytest                # tests (ffmpeg dans le PATH pour le test de bout en bout)
```

Au premier lancement : **Réglages** → dossier RAW, qualité par défaut (`mp3_320`), URL et clé de l'API,
et éventuellement le dossier de ffmpeg/ffprobe (sinon ceux du PATH).
La clé est stockée dans le Gestionnaire d'identifiants Windows, jamais dans le code ni la base.
La base est dans `%APPDATA%\dj-music-manager\library.db`.

### Spotify

Un lien de playlist ou d'album Spotify s'importe **sans connexion** : l'app lit la page d'intégration publique
(lecteur « embed »), qui liste titre, artistes et durée des **100 premiers titres**. Au-delà, l'aperçu indique
combien de titres manquent.

Connexion facultative, pour tes playlists privées ou de plus de 100 titres :

1. Crée une app sur [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard) (Web API),
   avec l'URI de redirection `http://127.0.0.1:8888/callback`. Ton compte doit être Premium.
2. Dans **Réglages**, colle son Client ID puis **Connecter Spotify** (connexion PKCE dans le navigateur ;
   le refresh token va dans le Gestionnaire d'identifiants Windows).

Depuis février 2026, l'API officielle ne livre que les playlists **que tu possèdes ou co-édites**. Pour importer
en entier la playlist de quelqu'un d'autre, copie ses titres dans une playlist à toi (Ctrl+A, « Ajouter à la
playlist »). Les téléchargements, eux, passent toujours par `/download?url=` et n'ont pas besoin de Spotify.

## Modules

| Module | Rôle |
|---|---|
| `api_client.py` | API de téléchargement ([docs/API.md](docs/API.md)) : streaming, timeout 150 s, 422 jamais réessayé, 504 une fois |
| `spotify.py` | Spotify : API Web (connexion PKCE) ou page publique, pistes d'album/playlist |
| `deezer.py` | API publique Deezer : ISRC, titre d'album/playlist (quota 50 req / 5 s) |
| `dedup.py` | normalisation artiste + titre (feat., Original Mix, accents, casse, ponctuation) |
| `library.py` | SQLite : schéma, migrations (`PRAGMA user_version`), requêtes |
| `importer.py` | aperçu dédoublonné d'un lien, création des jobs |
| `downloader.py` | 4 téléchargements simultanés, échecs isolés et relançables |
| `manual.py` | ajout manuel (glisser-déposer, scan) et remplacement d'un son : même dédoublonnage, copie dans RAW |
| `files.py` | écriture dans RAW sans jamais écraser |
| `tags.py` | lecture des tags (jamais d'écriture) |
| `smart.py` | règles des playlists intelligentes -> SQL paramétré |
| `quality/analysis.py` | analyse spectrale locale, portage numpy de whatsmybitrate |
| `quality/badge.py` | badge unique 128 / 192 / 256 / 320 / Lossless / Faux FLAC / Transcode |
| `ui/` | interface PySide6 (`library_panel.py` : playlists, filtres, tags) |

Dédoublonnage : ID de la source (Deezer ou Spotify) → ISRC → artiste + titre normalisés ± 3 s.
Jamais par nom de fichier. Une piste Spotify est téléchargée par `/download?url=` ; si le bot ne la trouve pas,
repli sur `/search` en prenant la durée la plus proche.

## Analyse qualité locale

Les fichiers ajoutés à la main (glisser-déposer sur la fenêtre, ou **Scanner un dossier…**) sont copiés dans RAW
(l'original n'est jamais modifié ; un fichier déjà dans RAW est référencé sur place) puis analysés.
`quality/analysis.py` reprend les seuils et la logique de whatsmybitrate v0.4 (© Oren Cohen, MIT) :
coupure par trou spectral à −90/−75 dB, recherche de falaise, analyse par tranches de 15 s pour le lossless.
Décodage par ffmpeg et STFT en numpy, sans librosa ni scipy.

Validation contre la CLI d'origine (`tools/compare_whatsmybitrate.py`) sur 20 fichiers — FLAC/WAV/AIFF réels,
MP3 128/192/256/320/V0, AAC, Vorbis, faux FLAC issus de MP3 128 et 192, MP3 128 réencodé en 320 :
**20/20 identiques**, fréquences de coupure comprises.

## Organisation pour les sets

- **Tags libres** (genre, énergie, moment du set…) : clic droit sur une sélection → *Ajouter des tags…*,
  *Retirer le tag*. Insensibles à la casse.
- **Playlists** dans la colonne de gauche : manuelles (♪, clic droit sur des sons → *Ajouter à la playlist*),
  importées (↻, resynchronisées à chaque nouvel import du lien) et intelligentes (⚙, règles sur tag, BPM, durée,
  qualité, artiste, titre, album, format, source ; « toutes » ou « au moins une »), évaluées à l'affichage.
  Une playlist ne contient que des références : la supprimer ne touche jamais aux sons.
- **Filtres rapides** : recherche multi-mots (artiste, titre, album, tags), tag, qualité, plage de BPM.

## Remplacer un son par une meilleure version

Clic droit sur un son → **Remplacer le fichier…** (achat en 320, AIFF…). Le son garde son ID interne : playlists,
tags et ID Deezer/Spotify suivent, et un réimport ne le retélécharge pas. Le nouveau fichier est copié dans RAW et
analysé ; l'ancien part dans la corbeille Windows. Les métadonnées connues sont conservées, le nouveau fichier ne
complète que les champs vides. Avant de remplacer, l'app prévient si le fichier ressemble à un autre son de la
bibliothèque ou si sa durée diffère de plus de 10 s.

## Feuille de route

1. ~~MVP : lien Deezer → téléchargement dans RAW avec dédoublonnage → liste avec badge qualité~~
2. ~~Spotify (API Spotify → `/download?url=`, repli `/search` par durée la plus proche)~~
3. ~~Ajout manuel (glisser-déposer, scan) + analyse qualité locale portée de
   [whatsmybitrate](https://github.com/oren-cohen/whatsmybitrate) (MIT), validée contre la CLI d'origine~~
4. ~~Tags, playlists manuelles et intelligentes, filtres~~
5. ~~Remplacement d'un son par une meilleure version (même ID interne)~~
6. Export rekordbox.xml (+ option FLAC → AIFF)
7. Paquet PyInstaller avec ffmpeg embarqué
