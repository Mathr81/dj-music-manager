# DJ Music Manager

Bibliothèque musicale pour DJ (Windows). Un seul dossier **RAW** contient chaque son une seule fois ;
playlists et tags sont une couche SQLite par-dessus. Réimporter une playlist ne télécharge que ce qui manque.

## Lancer

```bash
uv sync
uv run dj-music-manager      # ou : uv run python -m dj_music_manager
uv run pytest                # tests (ffmpeg dans le PATH pour le test de bout en bout)
```

Au premier lancement : **Réglages** → dossier RAW, qualité par défaut (`mp3_320`), clé API.
La clé est stockée dans le Gestionnaire d'identifiants Windows, jamais dans le code ni la base.
La base est dans `%APPDATA%\dj-music-manager\library.db`.

## Modules

| Module | Rôle |
|---|---|
| `api_client.py` | API de téléchargement ([docs/API.md](docs/API.md)) : streaming, timeout 150 s, 422 jamais réessayé, 504 une fois |
| `deezer.py` | API publique Deezer : ISRC, titre d'album/playlist (quota 50 req / 5 s) |
| `dedup.py` | normalisation artiste + titre (feat., Original Mix, accents, casse, ponctuation) |
| `library.py` | SQLite : schéma, migrations (`PRAGMA user_version`), requêtes |
| `importer.py` | aperçu dédoublonné d'un lien, création des jobs |
| `downloader.py` | 4 téléchargements simultanés, échecs isolés et relançables |
| `tags.py` | lecture des tags (jamais d'écriture) |
| `quality/badge.py` | badge unique 128 / 192 / 256 / 320 / Lossless / Faux FLAC / Transcode |
| `ui/` | interface PySide6 |

Dédoublonnage : ID Deezer → ISRC → artiste + titre normalisés ± 3 s. Jamais par nom de fichier.

## Feuille de route

1. ~~MVP : lien Deezer → téléchargement dans RAW avec dédoublonnage → liste avec badge qualité~~
2. Spotify (API Spotify → `/download?url=`, repli `/search` par durée la plus proche)
3. Ajout manuel (glisser-déposer, scan) + analyse qualité locale portée de
   [whatsmybitrate](https://github.com/oren-cohen/whatsmybitrate) (MIT), validée contre la CLI d'origine
4. Tags, playlists manuelles et intelligentes, filtres
5. Remplacement d'un son par une meilleure version (même ID interne)
6. Export rekordbox.xml (+ option FLAC → AIFF)
7. Paquet PyInstaller avec ffmpeg embarqué
