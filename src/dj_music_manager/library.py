"""Bibliothèque SQLite : sons, ID externes, playlists, tags, file de téléchargement, réglages."""

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from .dedup import norm_key, same_duration

# une entrée par version du schéma, appliquée selon PRAGMA user_version
MIGRATIONS = [
    """
    CREATE TABLE tracks(
        id INTEGER PRIMARY KEY,
        path TEXT UNIQUE NOT NULL,          -- relatif au dossier RAW
        isrc TEXT, artist TEXT, title TEXT, album TEXT,
        norm_key TEXT,
        duration REAL, expected_duration REAL,
        bpm REAL, format TEXT, quality TEXT,
        source TEXT CHECK(source IN ('deezer','spotify','manual')),
        real_quality TEXT, lossless INTEGER, max_frequency INTEGER,
        badge TEXT CHECK(badge IN ('128','192','256','320','Lossless','Faux FLAC','Transcode')),
        quality_origin TEXT CHECK(quality_origin IN ('api','local')),
        added_at TEXT NOT NULL
    );
    CREATE INDEX ix_tracks_isrc ON tracks(isrc);
    CREATE INDEX ix_tracks_norm ON tracks(norm_key);

    CREATE TABLE external_ids(
        provider TEXT NOT NULL, external_id TEXT NOT NULL,
        track_id INTEGER NOT NULL REFERENCES tracks ON DELETE CASCADE,
        PRIMARY KEY(provider, external_id)
    );

    CREATE TABLE playlists(
        id INTEGER PRIMARY KEY, name TEXT NOT NULL,
        kind TEXT NOT NULL CHECK(kind IN ('manual','smart')),
        source_url TEXT UNIQUE, rules_json TEXT, created_at TEXT NOT NULL
    );
    CREATE TABLE playlist_tracks(
        playlist_id INTEGER NOT NULL REFERENCES playlists ON DELETE CASCADE,
        track_id INTEGER NOT NULL REFERENCES tracks ON DELETE CASCADE,
        position INTEGER,
        PRIMARY KEY(playlist_id, track_id)
    );

    CREATE TABLE tags(id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL COLLATE NOCASE, category TEXT);
    CREATE TABLE track_tags(
        track_id INTEGER NOT NULL REFERENCES tracks ON DELETE CASCADE,
        tag_id INTEGER NOT NULL REFERENCES tags ON DELETE CASCADE,
        PRIMARY KEY(track_id, tag_id)
    );

    CREATE TABLE jobs(
        id INTEGER PRIMARY KEY,
        playlist_id INTEGER REFERENCES playlists ON DELETE SET NULL, position INTEGER,
        provider TEXT NOT NULL, external_id TEXT, url TEXT,
        download_id TEXT,                   -- ID Deezer choisi à la main (sinon external_id)
        artist TEXT, title TEXT, expected_duration REAL, quality TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('queued','running','done','failed')),
        http_status INTEGER, error TEXT, attempts INTEGER NOT NULL DEFAULT 0,
        track_id INTEGER REFERENCES tracks ON DELETE SET NULL,
        updated_at TEXT
    );

    CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT);
    """,
]

TRACK_FIELDS = ("path", "isrc", "artist", "title", "album", "duration", "expected_duration", "bpm", "format",
                "quality", "source", "real_quality", "lossless", "max_frequency", "badge", "quality_origin")
JOB_FIELDS = ("playlist_id", "position", "provider", "external_id", "url", "artist", "title",
              "expected_duration", "quality")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Library:
    def __init__(self, db_path: Path | str):
        self._db = sqlite3.connect(db_path, check_same_thread=False)  # partagé entre threads sous verrou
        self._db.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.execute("PRAGMA journal_mode = WAL")
        self._migrate()

    def _migrate(self):
        version = self._db.execute("PRAGMA user_version").fetchone()[0]
        for i, script in enumerate(MIGRATIONS[version:], start=version + 1):
            self._db.executescript(f"BEGIN; {script}; PRAGMA user_version = {i}; COMMIT;")

    @contextmanager
    def _tx(self):
        with self._lock, self._db:
            yield self._db

    def _one(self, sql: str, *args):
        with self._lock:
            return self._db.execute(sql, args).fetchone()

    def _all(self, sql: str, *args) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute(sql, args).fetchall()

    # --- réglages (la clé API n'est jamais ici : voir config.py)
    def get_setting(self, key: str, default: str | None = None) -> str | None:
        row = self._one("SELECT value FROM settings WHERE key = ?", key)
        return row[0] if row else default

    def set_setting(self, key: str, value: str):
        with self._tx() as db:
            db.execute("INSERT INTO settings VALUES(?, ?) ON CONFLICT DO UPDATE SET value = excluded.value",
                       (key, value))

    # --- sons et dédoublonnage
    def track_by_external(self, provider: str, external_id: str) -> int | None:
        row = self._one("SELECT track_id FROM external_ids WHERE provider = ? AND external_id = ?",
                        provider, str(external_id))
        return row[0] if row else None

    def add_external(self, provider: str, external_id: str, track_id: int):
        with self._tx() as db:
            db.execute("INSERT OR IGNORE INTO external_ids VALUES(?, ?, ?)", (provider, str(external_id), track_id))

    def find_track(self, isrc: str | None, artist: str | None, title: str | None,
                   duration: float | None) -> int | None:
        """ISRC, puis artiste + titre normalisés avec durée ±3 s."""
        if isrc:
            row = self._one("SELECT id FROM tracks WHERE isrc = ? LIMIT 1", isrc.upper())
            if row:
                return row[0]
        key = norm_key(artist, title)
        if key:
            for row in self._all("SELECT id, duration FROM tracks WHERE norm_key = ?", key):
                if same_duration(row["duration"], duration):
                    return row["id"]
        return None

    def add_track(self, fields: dict, provider: str | None = None, external_id: str | None = None) -> tuple[int, bool]:
        """Ajoute le son sauf s'il existe déjà (vérifié sous verrou). Renvoie (id, créé)."""
        with self._tx() as db:
            existing = (provider and self.track_by_external(provider, external_id)) or self.find_track(
                fields.get("isrc"), fields.get("artist"), fields.get("title"), fields.get("duration"))
            if existing:
                track_id, created = existing, False
            else:
                values = {k: fields.get(k) for k in TRACK_FIELDS}
                if values["isrc"]:
                    values["isrc"] = values["isrc"].upper()
                values["norm_key"] = norm_key(values["artist"], values["title"])
                values["added_at"] = _now()
                cols = ", ".join(values)
                cur = db.execute(f"INSERT INTO tracks({cols}) VALUES({', '.join('?' * len(values))})",
                                 tuple(values.values()))
                track_id, created = cur.lastrowid, True
            if provider:
                db.execute("INSERT OR IGNORE INTO external_ids VALUES(?, ?, ?)", (provider, str(external_id), track_id))
        return track_id, created

    def track_by_path(self, path: str) -> int | None:
        row = self._one("SELECT id FROM tracks WHERE path = ?", path)
        return row[0] if row else None

    def track(self, track_id: int) -> sqlite3.Row | None:
        return self._one("SELECT * FROM tracks WHERE id = ?", track_id)

    def tracks(self) -> list[sqlite3.Row]:
        return self._all("SELECT * FROM tracks ORDER BY added_at DESC, id DESC")

    # --- playlists
    def playlist_for_source(self, source_url: str, name: str) -> int:
        with self._tx() as db:
            row = db.execute("SELECT id FROM playlists WHERE source_url = ?", (source_url,)).fetchone()
            if row:
                return row[0]
            return db.execute("INSERT INTO playlists(name, kind, source_url, created_at) VALUES(?, 'manual', ?, ?)",
                              (name, source_url, _now())).lastrowid

    def set_playlist_tracks(self, playlist_id: int, items: list[tuple[int, int]]):
        """Remplace le contenu : [(track_id, position)]."""
        with self._tx() as db:
            db.execute("DELETE FROM playlist_tracks WHERE playlist_id = ?", (playlist_id,))
            db.executemany("INSERT OR IGNORE INTO playlist_tracks VALUES(?, ?, ?)",
                           [(playlist_id, t, p) for t, p in items])

    def link(self, playlist_id: int, track_id: int, position: int | None):
        with self._tx() as db:
            db.execute("INSERT INTO playlist_tracks VALUES(?, ?, ?) "
                       "ON CONFLICT DO UPDATE SET position = excluded.position", (playlist_id, track_id, position))

    def playlist_track_ids(self, playlist_id: int) -> list[int]:
        rows = self._all("SELECT track_id FROM playlist_tracks WHERE playlist_id = ? ORDER BY position", playlist_id)
        return [r[0] for r in rows]

    # --- file de téléchargement
    def add_jobs(self, jobs: list[dict]) -> list[int]:
        with self._tx() as db:
            return [db.execute(f"INSERT INTO jobs({', '.join(JOB_FIELDS)}, status, updated_at) "
                               f"VALUES({', '.join('?' * len(JOB_FIELDS))}, 'queued', ?)",
                               (*(j.get(k) for k in JOB_FIELDS), _now())).lastrowid for j in jobs]

    def update_job(self, job_id: int, **fields):
        fields["updated_at"] = _now()
        with self._tx() as db:
            db.execute(f"UPDATE jobs SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?",
                       (*fields.values(), job_id))

    def start_job(self, job_id: int):
        with self._tx() as db:
            db.execute("UPDATE jobs SET status = 'running', attempts = attempts + 1, updated_at = ? WHERE id = ?",
                       (_now(), job_id))

    def job(self, job_id: int) -> sqlite3.Row | None:
        return self._one("SELECT * FROM jobs WHERE id = ?", job_id)

    def failed_jobs(self) -> list[sqlite3.Row]:
        return self._all("SELECT * FROM jobs WHERE status = 'failed' ORDER BY id")

    def drop_failed_jobs(self, playlist_id: int):
        """Un nouvel import de la même playlist remplace ses anciens échecs."""
        with self._tx() as db:
            db.execute("DELETE FROM jobs WHERE playlist_id = ? AND status = 'failed'", (playlist_id,))

    def recover_jobs(self):
        """Au démarrage : les jobs interrompus passent en échec (relançables), les terminés sont purgés."""
        with self._tx() as db:
            db.execute("DELETE FROM jobs WHERE status = 'done'")
            db.execute("UPDATE jobs SET status = 'failed', error = 'Interrompu (fermeture de l''application)' "
                       "WHERE status IN ('queued', 'running')")
