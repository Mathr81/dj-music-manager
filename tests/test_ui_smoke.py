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
    assert menus == ["&Fichier", "&Bibliothèque", "&Lecture", "&Affichage", "&Téléchargements", "&Aide"]
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


def test_accents_sort_with_plain_letters(window):
    w, lib, _ = window
    for name in ("Zouk", "Été", "Eau", "ébène"):
        lib.create_playlist(name)
    assert [p["name"] for p in lib.playlists()] == ["Eau", "ébène", "Été", "Zouk"]
    lib.add_track({"path": "x.mp3", "artist": "Édith Piaf", "title": "T", "source": "manual"})
    w.panel.show_track(lib.track_by_path("x.mp3"))
    w.panel.view.sortByColumn(1, Qt.AscendingOrder)
    artists = [w.panel.proxy.index(r, 1).data() for r in range(w.panel.proxy.rowCount())]
    assert artists == ["Artiste 0", "Artiste 1", "Artiste 2", "Édith Piaf"]


def test_bpm_double_half_and_player_steps(window):
    w, lib, raw = window
    ids = [w.panel.proxy.index(r, 0).data(ID_ROLE) for r in range(3)]
    w.panel.set_bpm(ids[:1], 64)
    w.panel.set_bpm(ids[:1], lambda b: b and b * 2)
    assert lib.track(ids[0])["bpm"] == 128
    w.panel.set_bpm(ids[1:2], lambda b: b and b / 2)  # sans BPM : inchangé
    assert lib.track(ids[1])["bpm"] is None

    played = []
    w.panel.player.play = lambda path, artist, title, cover: played.append(title)
    w.panel.view.selectRow(0)
    w.panel.play_step(1)  # rien en lecture : la sélection
    w.panel.play_step(1)
    w.panel.play_step(-1)
    first, second = (w.panel.proxy.index(r, 2).data() for r in (0, 1))
    assert played == [first, second, first]


def test_waveform_click_seeks(window):
    import numpy as np
    from PySide6.QtCore import QPoint
    from PySide6.QtTest import QTest

    w, *_ = window
    wave = w.player.wave
    wave.resize(400, 44)
    wave.set_data(np.tile(np.linspace(0, 1, 800, dtype=np.float32)[:, None], (1, 3)))
    seeks = []
    wave.seek.connect(seeks.append)
    QTest.mouseClick(wave, Qt.LeftButton, pos=QPoint(100, 20))
    assert seeks == [pytest.approx(0.25, abs=0.01)]
    wave.set_progress(0.5)
    wave.grab()  # dessin avec les deux images en cache, sans erreur


def test_key_column_and_harmonic_filter(window):
    w, lib, _ = window
    ids = [t["id"] for t in lib.tracks()]
    for track_id, k in zip(ids, ("Am", "C", "F#")):  # 8A, 8B (relative), 2B (incompatible)
        w.panel._on_analyzed(track_id, {"key": k})
    assert lib.track(ids[0])["key"] == "Am"
    col = [w.panel.proxy.index(r, 6).data() for r in range(3)]
    assert sorted(col) == ["2B", "8A", "8B"]
    w.panel.show_compatible("Am")
    assert w.panel.proxy.rowCount() == 2 and "compatibles avec 8A" in w.panel.stats.text()
    w.panel.clear_filters()
    assert w.panel.proxy.rowCount() == 3


def test_redownload_menu_and_row_refresh(window, monkeypatch):
    w, lib, _ = window
    shown = []

    class Menu(library_panel.QMenu):
        def exec(self, *_):
            shown.append(self)

    monkeypatch.setattr(library_panel, "QMenu", Menu)
    monkeypatch.setattr(w, "_ensure_api", lambda: False)  # pas de clé API : la fenêtre ouvrirait les réglages
    requested = []
    w.panel.redownload_requested.connect(lambda ids, q: requested.append((sorted(ids), q)))
    w.panel.view.selectAll()
    w.panel._track_menu(w.panel.view.rect().center())
    again = next(a.menu() for a in shown[0].actions() if a.text() == "Retélécharger en")
    next(a for a in again.actions() if a.text() == "FLAC").trigger()
    assert requested == [(sorted(t["id"] for t in lib.tracks()), "flac")]
    # le son retéléchargé change de fichier : sa ligne se met à jour
    t = lib.tracks()[0]
    lib.update_track(t["id"], {"format": "flac", "badge": "Lossless"})
    w.panel.show_track(t["id"])
    assert w.panel.proxy.rowCount() == 3
    row = w.panel._rows[t["id"]]
    assert w.panel.model.item(row, library_panel.COL_QUALITY).text() == "Lossless"
