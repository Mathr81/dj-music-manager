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
(lecteur « embed »), qui liste titre, artistes et durée des **100 premiers titres**.

**Playlist de plus de 100 titres** : rien à faire, au téléchargement le bot récupère la playlist entière
(`/collection`) et les titres au-delà des 100 sont ajoutés s'ils ne sont pas déjà dans la bibliothèque (artiste,
titre et durée). Pour voir la liste complète **avant** de télécharger : dans l'app Spotify, ouvre la playlist,
**Ctrl+A puis Ctrl+C**, puis clique sur **Coller la liste complète** dans l'aperçu (pages publiques des morceaux,
~1 min 20 pour 200 titres ; quelques secondes si Spotify est connecté).

Connexion facultative (titres plus rapides avec ISRC, playlists privées) :

1. Crée une app sur [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard) (Web API),
   avec l'URI de redirection `http://127.0.0.1:8888/callback`. Ton compte doit être Premium.
2. Dans **Réglages**, colle son Client ID puis **Connecter Spotify** (connexion PKCE dans le navigateur ;
   le refresh token va dans le Gestionnaire d'identifiants Windows).

Depuis février 2026, l'API officielle d'une app en mode développeur ne livre le contenu que des playlists **que tu
possèdes ou co-édites**, même connectée : c'est ce qui rend la copie depuis l'app nécessaire pour les autres.
Les téléchargements passent toujours par `/download?url=` et n'ont pas besoin de Spotify.

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
| `export.py` | export rekordbox.xml, crates Serato, M3U8, conversion FLAC → AIFF |
| `quality/badge.py` | badge unique 128 / 192 / 256 / 320 / Lossless / Faux FLAC / Transcode |
| `ui/` | interface PySide6 (`library_panel.py` : playlists, filtres, tags) |

Dédoublonnage : ID de la source (Deezer ou Spotify) → ISRC → artiste + titre normalisés ± 3 s.
Jamais par nom de fichier.

Téléchargement d'un album ou d'une playlist : **une seule collecte** par le bot pour tout le lien
(`POST /collections`, suivie toutes les 4 s) ; chaque fichier qui arrive est rapproché d'une piste attendue
(artiste + titre normalisés, durée ± 3 s) et part aussitôt en téléchargement par `/file`, sans attendre la fin.
Si le bot de secours prend le relais en cours de route, les pistes déjà servies ne sont pas retéléchargées.
Ce que le bot ne fournit pas (il omet en silence ce qu'il ne trouve pas, surtout le bot de secours) passe piste par
piste : `/download?id=` (Deezer) ou `/download?url=` (Spotify), puis repli `/search` sur la durée la plus proche.
Si `/collection` échoue, tout passe piste par piste. Après chaque téléchargement, le titre du fichier reçu est
comparé au titre attendu : un autre morceau renvoyé par le bot va dans **Échecs** au lieu d'être rangé en silence.
Un même ISRC ne suffit pas à fusionner deux sons dont les titres n'ont rien à voir (ISRC mal étiqueté par l'API) :
le conflit est noté dans le journal de l'API.

## File d'attente, échecs et journal de l'API

- **File d'attente** : *Mettre en pause* (rien de nouveau ne démarre, les téléchargements en cours se terminent),
  *Supprimer la sélection*, *Vider la file* (annule l'attente et l'en-cours, fichiers partiels supprimés).
- **Échecs** : *Relancer*, *Chercher à la main…*, *Supprimer la sélection*, *Tout effacer*.
- **Journal des erreurs API** (`%APPDATA%\dj-music-manager\api_errors.log`, 2 Mo × 4) : une ligne par incident
  — requête exacte (sans la clé), code HTTP ou erreur réseau, durée, message de l'API ; collections incomplètes
  (titres absents) ; fichiers dont le contenu n'est pas le morceau demandé (bot et requête source).
- Un même album/playlist reste une seule playlist, quel que soit le lien partagé (« ?si=… » de Spotify,
  `/intl-fr/`, `/fr/`…) ; les doublons créés avant ce correctif sont fusionnés au démarrage.

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

## Export vers les logiciels DJ

Bouton **Exporter pour logiciel DJ…** sous les playlists (ou clic droit sur une playlist → *Exporter…*).
Aucune base de données de logiciel DJ n'est jamais modifiée.

| Logiciel | Format | Pour l'utiliser |
|---|---|---|
| rekordbox | `rekordbox.xml` (format « playlists sharing » de Pioneer) | Préférences > Avancé > Base de données > *rekordbox xml* : choisir le fichier ; les playlists apparaissent dans *rekordbox xml*, clic droit > Importer |
| Serato DJ | crates `dj-music-manager%%<playlist>.crate` dans le `_Serato_` du disque des fichiers (`Musique\_Serato_` pour C:) | elles apparaissent sous *dj-music-manager* au démarrage ; les autres crates ne sont pas touchées |
| VirtualDJ | `.m3u8` | copier dans `Documents\VirtualDJ\Playlists` ou glisser dans le navigateur |
| Mixxx, Traktor, Engine DJ… | `.m3u8` (UTF-8) | Mixxx : Playlists > clic droit > Importer une playlist |

Option **Convertir les FLAC en AIFF (anciens CDJ)** : conversion dans un dossier à part (`RAW_AIFF` par défaut,
jamais dans RAW), 16 ou 24 bits selon la source, 48 kHz maximum, tags recopiés ; une conversion à jour est
réutilisée. Les exports pointent alors vers les AIFF. Les tags perso sont exportés dans le champ *Comments*
de rekordbox. Le rekordbox.xml produit est relu sans erreur par pyrekordbox.

## Feuille de route

1. ~~MVP : lien Deezer → téléchargement dans RAW avec dédoublonnage → liste avec badge qualité~~
2. ~~Spotify (API Spotify → `/download?url=`, repli `/search` par durée la plus proche)~~
3. ~~Ajout manuel (glisser-déposer, scan) + analyse qualité locale portée de
   [whatsmybitrate](https://github.com/oren-cohen/whatsmybitrate) (MIT), validée contre la CLI d'origine~~
4. ~~Tags, playlists manuelles et intelligentes, filtres~~
5. ~~Remplacement d'un son par une meilleure version (même ID interne)~~
6. ~~Export rekordbox.xml (+ option FLAC → AIFF), Serato, VirtualDJ, Mixxx~~
7. Paquet PyInstaller avec ffmpeg embarqué
