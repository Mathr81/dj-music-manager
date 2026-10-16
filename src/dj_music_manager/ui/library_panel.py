"""Vue bibliothèque : playlists à gauche, filtres rapides, liste des sons, tags et playlists au clic droit."""

import json

from PySide6.QtCore import QSortFilterProxyModel, Qt
from PySide6.QtGui import QColor, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox, QPushButton, QSpinBox,
                               QSplitter, QTableView, QVBoxLayout, QWidget)

from ..library import Library
from ..quality.badge import BADGES
from .dialogs import fmt_duration
from .smart_dialog import SmartPlaylistDialog

SORT_ROLE = Qt.UserRole + 1
ID_ROLE = Qt.UserRole + 2
TAGS_ROLE = Qt.UserRole + 3
DURATION_WARNING = 10  # s d'écart avec la durée attendue : radio edit, clip, mauvaise version
BADGE_COLORS = {"Lossless": "#2e7d32", "320": "#558b2f", "256": "#9e9d24", "192": "#ef6c00", "128": "#c62828",
                "Faux FLAC": "#c62828", "Transcode": "#c62828"}
COLUMNS = ("#", "Artiste", "Titre", "Qualité", "Durée", "BPM", "Tags", "Format", "Album", "Ajouté le")
COL_POS, COL_QUALITY, COL_BPM, COL_TAGS, COL_ADDED = 0, 3, 5, 6, 9


def _item(text, sort=None) -> QStandardItem:
    item = QStandardItem("" if text is None else str(text))
    item.setEditable(False)
    item.setData(sort if sort is not None else item.text().casefold(), SORT_ROLE)
    return item


class TrackFilter(QSortFilterProxyModel):
    """Filtre par playlist (ensemble d'ID + ordre), texte, tag, qualité, BPM ; colonne « # » = position."""

    def __init__(self):
        super().__init__(sortRole=SORT_ROLE)
        self.order: dict[int, int] | None = None  # ID -> position ; None = toute la bibliothèque
        self.text = self.tag = self.badge = ""
        self.bpm_min = self.bpm_max = 0

    def refilter(self):
        self.beginFilterChange()
        self.endFilterChange()

    def _id(self, source_row: int) -> int:
        return self.sourceModel().item(source_row, 0).data(ID_ROLE)

    def filterAcceptsRow(self, row, parent) -> bool:
        m = self.sourceModel()
        if self.order is not None and self._id(row) not in self.order:
            return False
        bpm = m.item(row, COL_BPM).data(SORT_ROLE)
        if (self.bpm_min and bpm < self.bpm_min) or (self.bpm_max and not 0 < bpm <= self.bpm_max):
            return False
        if self.badge and m.item(row, COL_QUALITY).text() != self.badge:
            return False
        if self.tag and self.tag.casefold() not in m.item(row, COL_TAGS).data(TAGS_ROLE):
            return False
        if self.text:
            haystack = " ".join(m.item(row, c).text() for c in range(1, m.columnCount())).casefold()
            return all(word in haystack for word in self.text.casefold().split())
        return True

    def lessThan(self, left, right) -> bool:
        if left.column() == COL_POS and self.order is not None:
            return self.order.get(self._id(left.row()), 0) < self.order.get(self._id(right.row()), 0)
        return super().lessThan(left, right)

    def data(self, index, role=Qt.DisplayRole):
        if index.column() == COL_POS and role == Qt.DisplayRole:
            pos = None if self.order is None else self.order.get(self._id(self.mapToSource(index).row()))
            return "" if pos is None else pos + 1
        return super().data(index, role)


class LibraryPanel(QWidget):
    def __init__(self, lib: Library, parent=None):
        super().__init__(parent)
        self.lib = lib
        self._rows: dict[int, int] = {}  # ID interne -> ligne du modèle source
        self._shown = -1  # playlist affichée (None = toute la bibliothèque)

        # playlists
        self.playlists = QListWidget()
        self.playlists.currentItemChanged.connect(lambda *_: self.refresh_current())
        self.playlists.setContextMenuPolicy(Qt.CustomContextMenu)
        self.playlists.customContextMenuRequested.connect(self._playlist_menu)
        new_buttons = QHBoxLayout()
        new_buttons.addWidget(QPushButton("+ Playlist", clicked=self._new_playlist))
        new_buttons.addWidget(QPushButton("+ Intelligente", clicked=self._new_smart))
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(self.playlists)
        left_layout.addLayout(new_buttons)

        # filtres rapides
        self.model = QStandardItemModel(0, len(COLUMNS))
        self.model.setHorizontalHeaderLabels(COLUMNS)
        self.proxy = TrackFilter()
        self.proxy.setSourceModel(self.model)
        self.search = QLineEdit(placeholderText="Rechercher (artiste, titre, album, tag…)")
        self.search.textChanged.connect(lambda t: self._set_filter(text=t.strip()))
        self.tag_filter = QComboBox(minimumWidth=140)
        self.tag_filter.currentIndexChanged.connect(lambda: self._set_filter(tag=self.tag_filter.currentData() or ""))
        self.badge_filter = QComboBox()
        self.badge_filter.addItem("Toutes qualités", "")
        for b in BADGES:
            self.badge_filter.addItem(b, b)
        self.badge_filter.currentIndexChanged.connect(lambda: self._set_filter(badge=self.badge_filter.currentData()))
        self.bpm_min, self.bpm_max = (QSpinBox(maximum=300, specialValueText="–") for _ in range(2))
        self.bpm_min.valueChanged.connect(lambda v: self._set_filter(bpm_min=v))
        self.bpm_max.valueChanged.connect(lambda v: self._set_filter(bpm_max=v))
        filters = QHBoxLayout()
        filters.addWidget(self.search, 1)
        filters.addWidget(self.tag_filter)
        filters.addWidget(self.badge_filter)
        filters.addWidget(QLabel("BPM"))
        filters.addWidget(self.bpm_min)
        filters.addWidget(QLabel("à"))
        filters.addWidget(self.bpm_max)

        # liste des sons
        self.view = QTableView(sortingEnabled=True)
        self.view.setModel(self.proxy)
        self.view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.view.verticalHeader().hide()
        self.view.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        for col, width in enumerate((40, 180, 250, 90, 65, 50, 160, 70, 180)):
            self.view.setColumnWidth(col, width)
        self.view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._track_menu)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addLayout(filters)
        right_layout.addWidget(self.view)

        splitter = QSplitter()
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([220, 980])
        layout = QVBoxLayout(self)
        layout.addWidget(splitter)

        tags = lib.tags_by_track()
        for t in lib.tracks():
            self._add_row(t, tags.get(t["id"], []))
        self.refresh_tags()
        self.refresh_playlists()

    # --- lignes
    def show_track(self, track_id: int):
        """Affiche un son arrivé dans la bibliothèque (téléchargé ou ajouté à la main)."""
        if track_id not in self._rows:
            self._add_row(self.lib.track(track_id), [])
        self.refresh_playlists()  # compteurs et contenu de la playlist affichée

    def _add_row(self, t, tags: list[str]):
        self._rows[t["id"]] = self.model.rowCount()
        duration = _item(fmt_duration(t["duration"]), t["duration"] or 0)
        if t["expected_duration"] and t["duration"] and abs(t["duration"] - t["expected_duration"]) > DURATION_WARNING:
            duration.setText("⚠ " + duration.text())
            duration.setToolTip(f"Durée attendue : {fmt_duration(t['expected_duration'])}")
        quality = _item(t["badge"] or "?")
        quality.setToolTip(" · ".join(str(v) for v in (t["real_quality"], t["max_frequency"] and
                                                         f"coupure {t['max_frequency']} Hz") if v))
        if t["badge"] in BADGE_COLORS:
            quality.setForeground(QColor(BADGE_COLORS[t["badge"]]))
        first = _item("", 0)
        first.setData(t["id"], ID_ROLE)
        self.model.appendRow([
            first, _item(t["artist"]), _item(t["title"]), quality, duration,
            _item(f"{t['bpm']:.0f}" if t["bpm"] else "", t["bpm"] or 0), self._tags_item(tags),
            _item(t["format"]), _item(t["album"]), _item(t["added_at"].replace("T", " "), t["added_at"]),
        ])

    @staticmethod
    def _tags_item(tags: list[str]) -> QStandardItem:
        item = _item(", ".join(tags))
        item.setData([t.casefold() for t in tags], TAGS_ROLE)
        return item

    def _selected_ids(self) -> list[int]:
        rows = self.view.selectionModel().selectedRows()
        return [self.proxy.index(r.row(), 0).data(ID_ROLE) for r in rows]

    # --- filtres
    def _set_filter(self, **values):
        for k, v in values.items():
            setattr(self.proxy, k, v)
        self.proxy.refilter()

    def refresh_tags(self):
        """Recharge la colonne Tags et la liste du filtre."""
        tags = self.lib.tags_by_track()
        for track_id, row in self._rows.items():
            self.model.setItem(row, COL_TAGS, self._tags_item(tags.get(track_id, [])))
        current = self.tag_filter.currentData()
        self.tag_filter.blockSignals(True)
        self.tag_filter.clear()
        self.tag_filter.addItem("Tous les tags", "")
        for name in self.lib.tags():
            self.tag_filter.addItem(name, name)
        self.tag_filter.setCurrentIndex(max(0, self.tag_filter.findData(current)))
        self.tag_filter.blockSignals(False)
        self._set_filter(tag=self.tag_filter.currentData() or "")
        self.refresh_current()  # une playlist intelligente peut dépendre des tags

    # --- playlists
    def _current_playlist(self):
        item = self.playlists.currentItem()
        return self.lib.playlist(item.data(Qt.UserRole)) if item and item.data(Qt.UserRole) else None

    def refresh_playlists(self):
        current = self.playlists.currentItem().data(Qt.UserRole) if self.playlists.currentItem() else None
        self.playlists.blockSignals(True)
        self.playlists.clear()
        self.playlists.addItem(QListWidgetItem("Toute la bibliothèque"))
        for p in self.lib.playlists():
            icon = "⚙" if p["kind"] == "smart" else "↻" if p["source_url"] else "♪"
            item = QListWidgetItem(f"{icon}  {p['name']}  ({len(self.lib.playlist_track_ids(p['id']))})")
            item.setData(Qt.UserRole, p["id"])
            if p["source_url"]:
                item.setToolTip(f"Importée de {p['source_url']}\nUn nouvel import resynchronise son contenu.")
            self.playlists.addItem(item)
            if p["id"] == current:
                self.playlists.setCurrentItem(item)
        if self.playlists.currentItem() is None:
            self.playlists.setCurrentRow(0)
        self.playlists.blockSignals(False)
        self.refresh_current()

    def refresh_current(self):
        p = self._current_playlist()
        switched = (p and p["id"]) != self._shown
        self._shown = p and p["id"]
        self.proxy.order = None if p is None else {tid: i for i, tid in enumerate(self.lib.playlist_track_ids(p["id"]))}
        self.proxy.invalidate()  # le filtre et le tri « # » dépendent de l'ordre
        if switched:  # sinon on garde le tri choisi par l'utilisateur
            self.view.sortByColumn(*((COL_POS, Qt.AscendingOrder) if p else (COL_ADDED, Qt.DescendingOrder)))

    def _new_playlist(self):
        name, ok = QInputDialog.getText(self, "Nouvelle playlist", "Nom :")
        if ok and name.strip():
            pid = self.lib.create_playlist(name.strip())
            self.refresh_playlists()
            return pid
        return None

    def _new_smart(self):
        dlg = SmartPlaylistDialog(self.lib.tags(), parent=self)
        if dlg.exec():
            self.lib.create_playlist(dlg.name(), dlg.rules())
            self.refresh_playlists()

    def _playlist_menu(self, pos):
        item = self.playlists.itemAt(pos)
        p = self.lib.playlist(item.data(Qt.UserRole)) if item and item.data(Qt.UserRole) else None
        if p is None:
            return
        menu = QMenu(self)
        if p["kind"] == "smart":
            menu.addAction("Modifier les règles…", lambda: self._edit_smart(p))
        menu.addAction("Renommer…", lambda: self._rename(p))
        menu.addAction("Supprimer", lambda: self._delete(p))
        menu.exec(self.playlists.mapToGlobal(pos))

    def _edit_smart(self, p):
        dlg = SmartPlaylistDialog(self.lib.tags(), p["name"], json.loads(p["rules_json"]), self)
        if dlg.exec():
            self.lib.update_playlist(p["id"], dlg.name(), dlg.rules())
            self.refresh_playlists()

    def _rename(self, p):
        name, ok = QInputDialog.getText(self, "Renommer", "Nom :", text=p["name"])
        if ok and name.strip():
            self.lib.update_playlist(p["id"], name.strip())
            self.refresh_playlists()

    def _delete(self, p):
        if QMessageBox.question(self, "Supprimer", f"Supprimer la playlist « {p['name']} » ?\n"
                                "Les sons restent dans la bibliothèque.") == QMessageBox.Yes:
            self.lib.delete_playlist(p["id"])
            self.refresh_playlists()

    # --- clic droit sur les sons
    def _track_menu(self, pos):
        ids = self._selected_ids()
        if not ids:
            return
        menu = QMenu(self)
        menu.addAction("Ajouter des tags…", lambda: self._add_tags(ids))
        tags = self.lib.tags_by_track()
        present = sorted({t for i in ids for t in tags.get(i, [])}, key=str.casefold)
        if present:
            remove = menu.addMenu("Retirer le tag")
            for name in present:
                remove.addAction(name, lambda n=name: (self.lib.remove_tag(ids, n), self.refresh_tags()))
        add_to = menu.addMenu("Ajouter à la playlist")
        for p in self.lib.playlists():
            if p["kind"] == "manual":
                add_to.addAction(p["name"], lambda pid=p["id"]: self._add_to(pid, ids))
        add_to.addSeparator()
        add_to.addAction("Nouvelle playlist…", lambda: self._add_to(self._new_playlist(), ids))
        current = self._current_playlist()
        if current is not None and current["kind"] == "manual":
            menu.addAction(f"Retirer de « {current['name']} »", lambda: self._remove_from(current["id"], ids))
        menu.exec(self.view.viewport().mapToGlobal(pos))

    def _add_tags(self, ids: list[int]):
        text, ok = QInputDialog.getText(self, "Ajouter des tags",
                                        "Tags séparés par des virgules (genre, énergie, moment du set…) :")
        if ok and text.strip():
            self.lib.add_tags(ids, text.split(","))
            self.refresh_tags()

    def _add_to(self, playlist_id: int | None, ids: list[int]):
        if playlist_id is not None:
            self.lib.add_to_playlist(playlist_id, ids)
            self.refresh_playlists()

    def _remove_from(self, playlist_id: int, ids: list[int]):
        self.lib.remove_from_playlist(playlist_id, ids)
        self.refresh_playlists()
