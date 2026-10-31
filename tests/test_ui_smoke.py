"""La fenêtre se construit hors écran, avec thème, menus, glisser-déposer et suppression de sons."""

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from dj_music_manager.library import Library
from dj_music_manager.ui import library_panel, theme
from dj_music_manager.ui.library_panel import ID_ROLE, MIME_IDS
from dj_music_manager.ui.main_window import MainWindow


@pytest.fixture
def window(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(tmp_path))  # pas le registre de l'utilisateur
    app.setOrganizationName("dj-music-manager-tests")
    theme.apply(app)
    lib = Library(tmp_path / "db")
    raw = tmp_path / "raw"
    raw.mkdir()
    lib.set_setting("raw_dir", str(raw))
    for i in range(3):
        (raw / f"{i}.mp3").write_bytes(b"x")
        lib.add_track({"path": f"{i}.mp3", "artist": f"Artiste {i}", "title": f"Titre {i}", "duration": 200,
                       "badge": "320", "source": "manual"})
    w = MainWindow(lib)
    yield w, lib, raw
    w.close()


def test_menus_and_stats(window):
    w, lib, _ = window
    menus = [a.text() for a in w.menuBar().actions()]
    assert menus == ["&Fichier", "&Bibliothèque", "&Affichage", "&Téléchargements", "&Aide"]
    assert w.panel.stats.text().startswith("3 sons")
    w.panel.search.setText("introuvable")
    assert w.panel.proxy.rowCount() == 0 and "(sur 3)" in w.panel.stats.text()
    w.panel.clear_filters()
    assert w.panel.proxy.rowCount() == 3


def test_drag_tracks_onto_playlist(window):
    w, lib, raw = window
    pid = lib.create_playlist("Set")
    w.panel.refresh_playlists()
    w.panel.view.selectAll()
    mime = w.panel.proxy.mimeData(w.panel.view.selectionModel().selectedIndexes())
    ids = json.loads(bytes(mime.data(MIME_IDS)))
    assert sorted(ids) == sorted(t["id"] for t in lib.tracks())
    assert {u.toLocalFile() for u in mime.urls()} == {(raw / f"{i}.mp3").as_posix() for i in range(3)}
    w.panel.playlists.tracks_dropped.emit(pid, ids[:2])
    assert lib.playlist_track_ids(pid) == ids[:2]


def test_delete_tracks_sends_files_to_trash(window, monkeypatch):
    w, lib, raw = window
    trashed = []
    monkeypatch.setattr(library_panel, "send2trash", trashed.append)
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.Yes)
    first = w.panel.proxy.index(0, 0).data(ID_ROLE)
    w.panel._delete_tracks([first])
    assert lib.track(first) is None and len(trashed) == 1
    assert w.panel.proxy.rowCount() == 2
    remaining = {w.panel.model.item(r, 0).data(ID_ROLE): r for r in range(w.panel.model.rowCount())}
    assert w.panel._rows == remaining
