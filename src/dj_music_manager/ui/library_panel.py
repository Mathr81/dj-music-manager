"""Vue bibliothèque : playlists à gauche, filtres rapides, liste des sons, clics droits, glisser-déposer."""

import json
import os
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from importlib.metadata import version
from pathlib import Path
from urllib.parse import quote_plus

from PySide6.QtCore import (QByteArray, QItemSelectionModel, QMimeData, QObject, QSettings, QSize,
                            QSortFilterProxyModel, Qt, QUrl, Signal)
from PySide6.QtGui import QAction, QColor, QDesktopServices, QFont, QIcon, QImage, QPixmap, QKeySequence, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QComboBox, QFileDialog, QHBoxLayout, QHeaderView,
                               QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox,
                               QPushButton, QSpinBox, QSplitter, QTableView, QVBoxLayout, QWidget)
from send2trash import send2trash

from .. import bpm, config, export, key, manual
from ..dedup import norm_text
from ..library import Library
from ..quality.badge import BADGES
from ..tags import cover
from .dialogs import fmt_duration, plural
from .export_dialog import HOW_TO, ExportDialog
from .icons import icon
from .smart_dialog import SmartPlaylistDialog
from .theme import WARN

SORT_ROLE = Qt.UserRole + 1
ID_ROLE = Qt.UserRole + 2
TAGS_ROLE = Qt.UserRole + 3
PATH_ROLE = Qt.UserRole + 4
KEY_ROLE = Qt.UserRole + 5  # tonalité en forme courte (« Am »), la colonne affiche le Camelot
KIND_ROLE = Qt.UserRole + 1  # sur les éléments de la liste des playlists
MIME_IDS = "application/x-djmm-track-ids"  # glisser des sons d'une vue à l'autre
DURATION_WARNING = 10  # s d'écart avec la durée attendue : radio edit, clip, mauvaise version
BADGE_COLORS = {"Lossless": "#5fd068", "320": "#a5d65a", "256": "#d8dc5a", "192": "#ffab40", "128": "#ff6b6b",
                "Faux FLAC": "#ff6b6b", "Transcode": "#ff6b6b"}
COLUMNS = ("#", "Artiste", "Titre", "Qualité", "Durée", "BPM", "Tonalité", "Tags", "Format", "Album", "Ajouté le")
COL_POS, COL_ARTIST, COL_TITLE, COL_QUALITY, COL_DURATION, COL_BPM, COL_KEY, COL_TAGS, COL_ADDED = 0, 1, 2, 3, 4, 5, 6, 7, 10
HEADER_KEY = "library/header_v2"  # v2 : colonne Tonalité ; l'ancien état (une colonne de moins) est ignoré
SEARCH_SITES = {
    "Spotify": "https://open.spotify.com/search/{}",
    "YouTube": "https://www.youtube.com/results?search_query={}",
    "Beatport": "https://www.beatport.com/search?q={}",
    "SoundCloud": "https://soundcloud.com/search?q={}",
    "Deezer": "https://www.deezer.com/search/{}",
    "Google": "https://www.google.com/search?q={}",
}


def reveal(path: Path):
    """Ouvre l'Explorateur sur le fichier sélectionné (ou son dossier s'il a disparu)."""
    if path.exists():
        subprocess.Popen(["explorer", "/select,", str(path)])
    elif path.parent.exists():
        os.startfile(path.parent)


def web_search(site: str, query: str):
    QDesktopServices.openUrl(QUrl(SEARCH_SITES[site].format(quote_plus(query))))


def search_menu(menu: QMenu, query: str) -> QMenu:
    sub = menu.addMenu(icon("globe"), "Rechercher sur")
    for site in SEARCH_SITES:
        sub.addAction(site, lambda s=site: web_search(s, query))
    return sub


def _key_item(k: str | None) -> QStandardItem:
    """Camelot affiché (8A), couleur de sa case sur la roue, forme musicale en infobulle."""
    code = key.camelot(k)
    item = _item(code, key.camelot_sort(k))
    item.setData(k, KEY_ROLE)
    if code:
        item.setToolTip(f"{k}  ·  Camelot {code}")
        item.setForeground(QColor.fromHsv((int(code[:-1]) - 1) * 30, 140 if code.endswith("A") else 90, 255))
    return item


def _item(text, sort=None) -> QStandardItem:
    item = QStandardItem("" if text is None else str(text))
    item.setEditable(False)
    item.setData(sort if sort is not None else norm_text(item.text()), SORT_ROLE)  # « É » trié avec les E
    return item


class TrackFilter(QSortFilterProxyModel):
    """Filtre par playlist (ensemble d'ID + ordre), texte, tag, qualité, BPM ; colonne « # » = position."""

    def __init__(self, lib: Library):
        super().__init__(sortRole=SORT_ROLE)
        self.lib = lib
        self.order: dict[int, int] | None = None  # ID -> position ; None = toute la bibliothèque
        self.text = self.tag = self.badge = self.key_label = ""
        self.bpm_min = self.bpm_max = 0
        self.keys: set[str] | None = None  # filtre harmonique : tonalités compatibles avec un son

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
        if self.keys is not None and m.item(row, COL_KEY).data(KEY_ROLE) not in self.keys:
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

    def mimeTypes(self):
        return [MIME_IDS, "text/uri-list"]

    def mimeData(self, indexes):
        """Les sons glissés : leurs ID (vers une playlist) et leurs fichiers (vers rekordbox, l'Explorateur…)."""
        rows = sorted({i.row() for i in indexes})
        ids = [self.index(r, 0).data(ID_ROLE) for r in rows]
        raw = Path(self.lib.get_setting("raw_dir", ""))
        data = QMimeData()
        data.setData(MIME_IDS, QByteArray(json.dumps(ids).encode()))
        data.setUrls([QUrl.fromLocalFile(str(raw / self.index(r, 0).data(PATH_ROLE))) for r in rows])
        return data


class PlaylistList(QListWidget):
    """Liste des playlists : on y dépose des sons pour les ajouter à une playlist manuelle."""
    tracks_dropped = Signal(int, list)

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)

    def _target(self, event):
        item = self.itemAt(event.position().toPoint())
        ok = event.mimeData().hasFormat(MIME_IDS) and item is not None and item.data(KIND_ROLE) == "manual"
        return item if ok else None

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(MIME_IDS):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        item = self._target(event)
        if item:
            self.setCurrentItem(item, QItemSelectionModel.NoUpdate)  # surligne la cible sans changer de playlist
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        item = self._target(event)
        if item:
            self.tracks_dropped.emit(item.data(Qt.UserRole), json.loads(bytes(event.mimeData().data(MIME_IDS))))
            event.acceptProposedAction()


class _Signals(QObject):
    replaced = Signal(int, str)  # ID du son, message d'erreur (vide si réussi)
    export_progress = Signal(int, int)
    exported = Signal(object, str)  # résultat de export.run, message d'erreur
    cover_loaded = Signal(int, object)  # QImage déjà réduite
    analyzed = Signal(int, object)  # ID du son, {"bpm": …, "key": …} trouvés
    analysis_done = Signal(int)


class LibraryPanel(QWidget):
    resync_requested = Signal(str)  # lien source d'une playlist importée

    def __init__(self, lib: Library, parent=None):
        super().__init__(parent)
        self.lib = lib
        self._signals = _Signals(self)
        self._signals.replaced.connect(self._on_replaced)
        self._signals.export_progress.connect(
            lambda n, total: self.window().statusBar().showMessage(f"Conversion en AIFF : {n}/{total}…"))
        self._signals.exported.connect(self._on_exported)
        self._signals.cover_loaded.connect(self._on_cover)
        self._signals.analyzed.connect(self._on_analyzed)
        self._signals.analysis_done.connect(self._on_analysis_done)
        self._covers: dict[int, QPixmap] = {}
        self._analysis_pool = ThreadPoolExecutor(2)  # décodage ffmpeg + numpy : 2 en parallèle suffisent
        self._analysis_pending: set[int] = set()
        self.player = None  # PlayerBar, fourni par la fenêtre principale
        self._playing: int | None = None
        self._rows: dict[int, int] = {}  # ID interne -> ligne du modèle source
        self._shown = -1  # playlist affichée (None = toute la bibliothèque)

        # playlists
        self.playlists = PlaylistList()
        self.playlists.currentItemChanged.connect(lambda *_: self.refresh_current())
        self.playlists.setContextMenuPolicy(Qt.CustomContextMenu)
        self.playlists.customContextMenuRequested.connect(self._playlist_menu)
        self.playlists.tracks_dropped.connect(self._add_to)
        self.playlists.setIconSize(QSize(18, 18))
        self.playlists.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.playlists.setTextElideMode(Qt.ElideRight)
        self._shortcut(self.playlists, "F2", lambda: self._on_current_playlist(self._rename))
        self._shortcut(self.playlists, QKeySequence.Delete, lambda: self._on_current_playlist(self._delete))
        new_buttons = QHBoxLayout()
        new_buttons.addWidget(QPushButton(icon("plus"), " Playlist", clicked=lambda: self.new_playlist(),
                                          toolTip="Nouvelle playlist (Ctrl+N)"))
        new_buttons.addWidget(QPushButton(icon("smart"), " Intelligente", clicked=self.new_smart,
                                          toolTip="Playlist à règles : BPM, tags, qualité… (Ctrl+Maj+N)"))
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 8, 0)
        left_layout.addWidget(QLabel("PLAYLISTS", objectName="heading"))
        left_layout.addWidget(self.playlists)
        left_layout.addLayout(new_buttons)
        left_layout.addWidget(QPushButton(icon("export"), " Exporter…", clicked=lambda: self.export(),
                                          toolTip="Exporter pour rekordbox, Serato, VirtualDJ, Mixxx… (Ctrl+E)"))
        left.setMinimumWidth(left.sizeHint().width())  # les boutons ne débordent jamais sur la liste

        # filtres rapides
        self.model = QStandardItemModel(0, len(COLUMNS))
        self.model.setHorizontalHeaderLabels(COLUMNS)
        self.proxy = TrackFilter(lib)
        self.proxy.setSourceModel(self.model)
        self.search = QLineEdit(placeholderText="Rechercher…  (Ctrl+F)", clearButtonEnabled=True)
        self.search.addAction(icon("search"), QLineEdit.LeadingPosition)
        self.search.textChanged.connect(lambda t: self._set_filter(text=t.strip()))
        self.tag_filter = QComboBox(minimumWidth=140)
        self.tag_filter.currentIndexChanged.connect(lambda: self._set_filter(tag=self.tag_filter.currentData() or ""))
        self.badge_filter = QComboBox()
        self.badge_filter.addItem("Toutes qualités", "")
        for b in BADGES:
            self.badge_filter.addItem(b, b)
        self.badge_filter.currentIndexChanged.connect(lambda: self._set_filter(badge=self.badge_filter.currentData()))
        self.bpm_min, self.bpm_max = (QSpinBox(maximum=300, specialValueText="–", maximumWidth=60,
                                               buttonSymbols=QSpinBox.NoButtons) for _ in range(2))
        self.bpm_min.valueChanged.connect(lambda v: self._set_filter(bpm_min=v))
        self.bpm_max.valueChanged.connect(lambda v: self._set_filter(bpm_max=v))
        reset = QPushButton(icon("x"), "", toolTip="Effacer les filtres (Échap)", clicked=self.clear_filters,
                            objectName="icon")
        filters = QHBoxLayout()
        filters.addWidget(self.search, 1)
        filters.addWidget(self.tag_filter)
        filters.addWidget(self.badge_filter)
        filters.addWidget(QLabel("BPM"))
        filters.addWidget(self.bpm_min)
        filters.addWidget(QLabel("à"))
        filters.addWidget(self.bpm_max)
        filters.addWidget(reset)

        # liste des sons
        self.view = QTableView(sortingEnabled=True, alternatingRowColors=True, showGrid=False, wordWrap=False)
        self.view.setModel(self.proxy)
        self.view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.view.setDragDropMode(QAbstractItemView.DragOnly)
        self.view.setDragEnabled(True)
        self.view.verticalHeader().hide()
        self.view.verticalHeader().setDefaultSectionSize(36)
        self.view.setIconSize(QSize(28, 28))
        header = self.view.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setHighlightSections(False)
        header.setSectionsMovable(True)
        header.setContextMenuPolicy(Qt.CustomContextMenu)
        header.customContextMenuRequested.connect(self._header_menu)
        for col, width in enumerate((40, 180, 250, 105, 75, 60, 95, 160, 85, 180)):
            self.view.setColumnWidth(col, width)
        self.view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._track_menu)
        self.view.doubleClicked.connect(lambda _: self._play_selected())
        self._shortcut(self.view, Qt.Key_Return, self._play_selected)
        self._shortcut(self.view, QKeySequence.Copy, lambda: self._copy(self._names()))
        self._shortcut(self.view, QKeySequence.Delete, self._delete_key)
        self._shortcut(self.view, "Shift+Del", lambda: self._delete_tracks(self._selected_ids()))
        self._shortcut(self.view, "Ctrl+T", lambda: self._selected_ids() and self._add_tags(self._selected_ids()))
        self._shortcut(self, Qt.Key_Escape, self.clear_filters)
        self.stats = QLabel(objectName="hint")
        self.proxy.rowsInserted.connect(self._update_stats)
        self.proxy.rowsRemoved.connect(self._update_stats)
        self.proxy.layoutChanged.connect(self._update_stats)
        self.proxy.modelReset.connect(self._update_stats)
        self.view.selectionModel().selectionChanged.connect(self._update_stats)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addLayout(filters)
        right_layout.addWidget(self.view)
        right_layout.addWidget(self.stats)

        self.splitter = QSplitter()
        self.splitter.addWidget(left)
        self.splitter.addWidget(right)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([240, 960])
        layout = QVBoxLayout(self)
        layout.addWidget(self.splitter)

        tags = lib.tags_by_track()
        for t in lib.tracks():
            self._set_row(t, tags.get(t["id"], []))
        self.refresh_tags()
        self.refresh_playlists()
        self._restore_state()
        self._load_covers([t["id"] for t in lib.tracks()])
        self.analyze_audio([t["id"] for t in lib.tracks() if not (t["bpm"] and t["key"])])

    @staticmethod
    def _shortcut(widget: QWidget, keys, slot) -> QAction:
        action = QAction(widget, shortcut=QKeySequence(keys), shortcutContext=Qt.WidgetWithChildrenShortcut)
        action.triggered.connect(slot)
        widget.addAction(action)
        return action

    # --- état mémorisé (colonnes, séparateur)
    def _restore_state(self):
        s = QSettings()
        if s.value(HEADER_KEY) is not None:
            self.view.horizontalHeader().restoreState(s.value(HEADER_KEY))
        if s.value("library/splitter") is not None:
            self.splitter.restoreState(s.value("library/splitter"))

    def close_workers(self):
        self._analysis_pool.shutdown(wait=False, cancel_futures=True)

    def save_state(self):
        s = QSettings()
        s.setValue(HEADER_KEY, self.view.horizontalHeader().saveState())
        s.setValue("library/splitter", self.splitter.saveState())

    def _header_menu(self, pos):
        menu = QMenu(self)
        for col, name in enumerate(COLUMNS):
            action = menu.addAction(name)
            action.setCheckable(True)
            action.setChecked(not self.view.isColumnHidden(col))
            action.toggled.connect(lambda on, c=col: self.view.setColumnHidden(c, not on))
        menu.addSeparator()
        menu.addAction("Ajuster les largeurs", self.view.resizeColumnsToContents)
        menu.exec(self.view.horizontalHeader().mapToGlobal(pos))

    # --- lignes
    def show_track(self, track_id: int):
        """Affiche un son arrivé dans la bibliothèque (téléchargé ou ajouté à la main)."""
        if track_id not in self._rows:
            t = self.lib.track(track_id)
            self._set_row(t, [])
            self._load_covers([track_id])
            if not (t["bpm"] and t["key"]):
                self.analyze_audio([track_id])
        self.refresh_playlists()  # compteurs et contenu de la playlist affichée

    # --- pochettes (lues et réduites en arrière-plan : décoder 500 JPEG sur le fil de l'UI la fige)
    def _load_covers(self, ids: list[int]):
        raw = Path(self.lib.get_setting("raw_dir", ""))
        paths = [(i, raw / t["path"]) for i in ids if (t := self.lib.track(i))]

        def work():
            for track_id, path in paths:
                time.sleep(0.01)  # cède le GIL : lecteur et forme d'onde restent réactifs pendant le chargement
                try:
                    data = cover(path)
                except Exception:
                    data = None  # fichier absent ou illisible : pas de pochette
                image = QImage.fromData(data) if data else QImage()
                if not image.isNull():
                    self._signals.cover_loaded.emit(
                        track_id, image.scaled(128, 128, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation))

        threading.Thread(target=work, daemon=True).start()

    def _on_cover(self, track_id: int, image: QImage):
        self._covers[track_id] = QPixmap.fromImage(image)
        row = self._rows.get(track_id)
        if row is not None:
            self.model.item(row, COL_TITLE).setIcon(QIcon(self._covers[track_id]))

    # --- BPM et tonalité : calculés quand le fichier n'en a pas (jamais écrits dans le fichier)
    def analyze_audio(self, ids: list[int], force: bool = False):
        try:
            ffmpeg, _ = config.ffmpeg_tools(self.lib.get_setting("ffmpeg_dir"))
        except FileNotFoundError:
            return
        raw = Path(self.lib.get_setting("raw_dir", ""))
        for track_id in ids:
            t = self.lib.track(track_id)
            if t is None or track_id in self._analysis_pending:
                continue
            need = [f for f in ("bpm", "key") if force or not t[f]]
            if need:
                self._analysis_pending.add(track_id)
                self._analysis_pool.submit(self._analyze_one, track_id, raw / t["path"], ffmpeg, t["duration"], need)
        self._update_stats()

    def _analyze_one(self, track_id: int, path: Path, ffmpeg: str, duration, need: list[str]):
        try:
            y = bpm.decode_excerpt(path, ffmpeg, duration)  # un seul décodage pour les deux
            found = {f: (bpm.estimate if f == "bpm" else key.estimate)(y) for f in need}
            self._signals.analyzed.emit(track_id, {f: v for f, v in found.items() if v})
        except Exception:
            pass  # shortcut: un échec n'est retenté qu'au prochain démarrage ; à mémoriser si ça devient gênant
        finally:
            self._signals.analysis_done.emit(track_id)

    def _on_analyzed(self, track_id: int, found: dict):
        if "bpm" in found:
            self.set_bpm([track_id], found["bpm"], refresh=False)
        if "key" in found:
            self.lib.update_track(track_id, {"key": found["key"]})
            row = self._rows.get(track_id)
            if row is not None:
                self.model.setItem(row, COL_KEY, _key_item(found["key"]))

    def _on_analysis_done(self, track_id: int):
        self._analysis_pending.discard(track_id)
        if not self._analysis_pending:
            self.refresh_current()  # les playlists intelligentes sur le BPM se mettent à jour
        self._update_stats()

    def show_compatible(self, k: str):
        """Filtre harmonique : ne montre que les sons qui se mixent avec la tonalité k (roue Camelot)."""
        self._set_filter(keys=key.compatible(k), key_label=key.camelot(k))

    def set_bpm(self, ids: list[int], value, refresh: bool = True):
        """value : un nombre, ou une fonction de l'ancien BPM (×2, ÷2)."""
        for track_id in ids:
            t = self.lib.track(track_id)
            new = t and (value(t["bpm"]) if callable(value) else value)
            if not new:
                continue
            self.lib.update_track(track_id, {"bpm": round(new, 2)})
            row = self._rows.get(track_id)
            if row is not None:
                self.model.setItem(row, COL_BPM, _item(f"{new:.0f}", new))
        if refresh:
            self.refresh_current()

    def _ask_bpm(self, ids: list[int]):
        current = self.lib.track(ids[0])["bpm"] or 120
        value, ok = QInputDialog.getDouble(self, "BPM", "BPM :", current, 40, 250, 2)
        if ok:
            self.set_bpm(ids, value)

    def _set_row(self, t, tags: list[str]):
        """Ajoute la ligne du son, ou la remplace si elle existe déjà."""
        duration = _item(fmt_duration(t["duration"]), t["duration"] or 0)
        if t["expected_duration"] and t["duration"] and abs(t["duration"] - t["expected_duration"]) > DURATION_WARNING:
            duration.setForeground(QColor(WARN))
            duration.setToolTip(f"Durée attendue : {fmt_duration(t['expected_duration'])} (radio edit, clip, "
                                "autre version ?)")
        quality = _item(t["badge"] or "?")
        quality.setToolTip(" · ".join(str(v) for v in (t["real_quality"], t["max_frequency"] and
                                                         f"coupure {t['max_frequency']} Hz") if v))
        if t["badge"] in BADGE_COLORS:
            quality.setForeground(QColor(BADGE_COLORS[t["badge"]]))
            bold = QFont()
            bold.setBold(True)
            quality.setFont(bold)
        first = _item("", 0)
        first.setData(t["id"], ID_ROLE)
        first.setData(t["path"], PATH_ROLE)
        title = _item(t["title"])
        if t["id"] in self._covers:
            title.setIcon(QIcon(self._covers[t["id"]]))
        items = [
            first, _item(t["artist"]), title, quality, duration,
            _item(f"{t['bpm']:.0f}" if t["bpm"] else "", t["bpm"] or 0), _key_item(t["key"]), self._tags_item(tags),
            _item(t["format"]), _item(t["album"]), _item(t["added_at"].replace("T", " "), t["added_at"]),
        ]
        items[COL_TITLE].setToolTip(t["path"])
        row = self._rows.get(t["id"])
        if row is None:
            self._rows[t["id"]] = self.model.rowCount()
            self.model.appendRow(items)
        else:
            for col, item in enumerate(items):
                self.model.setItem(row, col, item)

    @staticmethod
    def _tags_item(tags: list[str]) -> QStandardItem:
        item = _item(", ".join(tags))
        item.setData([t.casefold() for t in tags], TAGS_ROLE)
        return item

    def _selected_rows(self) -> list[int]:
        return sorted(r.row() for r in self.view.selectionModel().selectedRows())

    def _selected_ids(self) -> list[int]:
        return [self.proxy.index(r, 0).data(ID_ROLE) for r in self._selected_rows()]

    def _path(self, row: int) -> Path:
        return Path(self.lib.get_setting("raw_dir", "")) / self.proxy.index(row, 0).data(PATH_ROLE)

    def _names(self) -> str:
        return "\n".join(f"{self.proxy.index(r, COL_ARTIST).data()} – {self.proxy.index(r, COL_TITLE).data()}"
                         for r in self._selected_rows())

    def _update_stats(self):
        def total(rows):
            s = sum(self.proxy.index(r, COL_DURATION).data(SORT_ROLE) or 0 for r in rows)
            return f"{int(s // 3600)} h {int(s % 3600 // 60):02d}" if s >= 3600 else fmt_duration(s)

        n = self.proxy.rowCount()
        text = f"{plural(n, 'son')} · {total(range(n))}"
        if n != self.model.rowCount():
            text += f"   (sur {self.model.rowCount()})"
        if self.proxy.keys is not None:
            text += f"   —   compatibles avec {self.proxy.key_label} (Échap pour tout revoir)"
        if self._analysis_pending:
            text += f"   —   analyse BPM et tonalité : {len(self._analysis_pending)} restant(s)"
        selected = self._selected_rows()
        if len(selected) > 1:
            text += f"   —   {plural(len(selected), 'sélectionné')} · {total(selected)}"
        self.stats.setText(text)

    # --- filtres
    def _set_filter(self, **values):
        for k, v in values.items():
            setattr(self.proxy, k, v)
        self.proxy.refilter()
        self._update_stats()

    def clear_filters(self):
        for w in (self.search, self.tag_filter, self.badge_filter, self.bpm_min, self.bpm_max):
            w.blockSignals(True)
        self.search.clear()
        self.tag_filter.setCurrentIndex(0)
        self.badge_filter.setCurrentIndex(0)
        self.bpm_min.setValue(0)
        self.bpm_max.setValue(0)
        for w in (self.search, self.tag_filter, self.badge_filter, self.bpm_min, self.bpm_max):
            w.blockSignals(False)
        self._set_filter(text="", tag="", badge="", bpm_min=0, bpm_max=0, keys=None)

    def focus_search(self):
        self.search.setFocus()
        self.search.selectAll()

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

    def _on_current_playlist(self, action):
        p = self._current_playlist()
        if p is not None:
            action(p)

    def refresh_playlists(self):
        current = self.playlists.currentItem().data(Qt.UserRole) if self.playlists.currentItem() else None
        self.playlists.blockSignals(True)
        self.playlists.clear()
        everything = QListWidgetItem(icon("library"), f"Toute la bibliothèque  ({self.model.rowCount()})")
        self.playlists.addItem(everything)
        for p in self.lib.playlists():
            kind = "smart" if p["kind"] == "smart" else "refresh" if p["source_url"] else "music"
            item = QListWidgetItem(icon(kind), f"{p['name']}  ({len(self.lib.playlist_track_ids(p['id']))})")
            item.setData(Qt.UserRole, p["id"])
            item.setData(KIND_ROLE, p["kind"])
            if p["source_url"]:
                item.setToolTip(f"Importée de {p['source_url']}\nClic droit > Resynchroniser pour la mettre à jour.")
            elif p["kind"] == "manual":
                item.setToolTip("Glisse des sons ici pour les ajouter.")
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
        self._update_stats()

    def new_playlist(self, ids: list[int] | None = None):
        name, ok = QInputDialog.getText(self, "Nouvelle playlist", "Nom :")
        if ok and name.strip():
            pid = self.lib.create_playlist(name.strip())
            if ids:
                self.lib.add_to_playlist(pid, ids)
            self.refresh_playlists()
            return pid
        return None

    def new_smart(self):
        dlg = SmartPlaylistDialog(self.lib.tags(), parent=self)
        if dlg.exec():
            self.lib.create_playlist(dlg.name(), dlg.rules())
            self.refresh_playlists()

    def _playlist_menu(self, pos):
        item = self.playlists.itemAt(pos)
        p = self.lib.playlist(item.data(Qt.UserRole)) if item and item.data(Qt.UserRole) else None
        menu = QMenu(self)
        if p is None:
            menu.addAction(icon("plus"), "Nouvelle playlist…", lambda: self.new_playlist())
            menu.addAction(icon("smart"), "Nouvelle playlist intelligente…", self.new_smart)
            menu.addAction(icon("export"), "Tout exporter…", lambda: self.export(None, everything=True))
            menu.exec(self.playlists.mapToGlobal(pos))
            return
        if p["kind"] == "smart":
            menu.addAction(icon("smart"), "Modifier les règles…", lambda: self._edit_smart(p))
        if p["source_url"]:
            menu.addAction(icon("refresh"), "Resynchroniser avec la source",
                           lambda: self.resync_requested.emit(p["source_url"]))
            menu.addAction(icon("link"), "Ouvrir la source dans le navigateur",
                           lambda: QDesktopServices.openUrl(QUrl(p["source_url"])))
            menu.addAction(icon("copy"), "Copier le lien source", lambda: self._copy(p["source_url"]))
        menu.addSeparator()
        menu.addAction(icon("export"), "Exporter…", lambda: self.export(p["id"]))
        menu.addAction(icon("copy"), "Copier la liste des titres", lambda: self._copy(self._playlist_text(p["id"])))
        menu.addAction(icon("plus"), "Dupliquer…", lambda: self._duplicate(p))
        menu.addAction(icon("pencil"), "Renommer…\tF2", lambda: self._rename(p))
        menu.addSeparator()
        menu.addAction(icon("trash"), "Supprimer\tSuppr", lambda: self._delete(p))
        menu.exec(self.playlists.mapToGlobal(pos))

    def _playlist_text(self, playlist_id: int) -> str:
        return "\n".join(f"{t['artist']} – {t['title']}" for t in map(self.lib.track,
                                                                       self.lib.playlist_track_ids(playlist_id)))

    def _edit_smart(self, p):
        dlg = SmartPlaylistDialog(self.lib.tags(), p["name"], json.loads(p["rules_json"]), self)
        if dlg.exec():
            self.lib.update_playlist(p["id"], dlg.name(), dlg.rules())
            self.refresh_playlists()

    def _duplicate(self, p):
        name, ok = QInputDialog.getText(self, "Dupliquer", "Nom de la copie :", text=f"{p['name']} (copie)")
        if ok and name.strip():
            self.lib.duplicate_playlist(p["id"], name.strip())
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

    # --- clic droit et raccourcis sur les sons
    def _track_menu(self, pos):
        ids = self._selected_ids()
        if not ids:
            return
        rows = self._selected_rows()
        menu = QMenu(self)
        menu.addAction(icon("play"), "Écouter\tEntrée", self._play_selected)
        menu.addAction(icon("folder"), "Afficher dans l'Explorateur", lambda: reveal(self._path(rows[0])))
        copy = menu.addMenu(icon("copy"), "Copier")
        copy.addAction("« Artiste – Titre »\tCtrl+C", lambda: self._copy(self._names()))
        copy.addAction("Chemin du fichier", lambda: self._copy("\n".join(str(self._path(r)) for r in rows)))
        copy.addAction("Fichier (à coller dans l'Explorateur)", lambda: self._copy_files(rows))
        if len(ids) == 1:
            search_menu(menu, f"{self.proxy.index(rows[0], COL_ARTIST).data()} "
                              f"{self.proxy.index(rows[0], COL_TITLE).data()}")
        menu.addSeparator()
        menu.addAction(icon("tag"), "Ajouter des tags…\tCtrl+T", lambda: self._add_tags(ids))
        tags = self.lib.tags_by_track()
        present = sorted({t for i in ids for t in tags.get(i, [])}, key=str.casefold)
        existing = [t for t in self.lib.tags() if t not in present]
        if existing:
            quick = menu.addMenu(icon("tag"), "Tag rapide")
            for name in existing:
                quick.addAction(name, lambda n=name: (self.lib.add_tags(ids, [n]), self.refresh_tags()))
        if present:
            remove = menu.addMenu(icon("x"), "Retirer le tag")
            for name in present:
                remove.addAction(name, lambda n=name: (self.lib.remove_tag(ids, n), self.refresh_tags()))
        add_to = menu.addMenu(icon("list-plus"), "Ajouter à la playlist")
        for p in self.lib.playlists():
            if p["kind"] == "manual":
                add_to.addAction(p["name"], lambda pid=p["id"]: self._add_to(pid, ids))
        add_to.addSeparator()
        add_to.addAction(icon("plus"), "Nouvelle playlist…", lambda: self.new_playlist(ids))
        current = self._current_playlist()
        if current is not None and current["kind"] == "manual":
            menu.addAction(icon("list-minus"), f"Retirer de « {current['name']} »\tSuppr",
                           lambda: self._remove_from(current["id"], ids))
        tempo = menu.addMenu(icon("activity"), "BPM")
        tempo.addAction("Doubler (× 2)", lambda: self.set_bpm(ids, lambda b: b and b * 2))
        tempo.addAction("Diviser par 2 (÷ 2)", lambda: self.set_bpm(ids, lambda b: b and b / 2))
        tempo.addAction("Saisir…", lambda: self._ask_bpm(ids))
        tempo.addAction("Recalculer BPM et tonalité", lambda: self.analyze_audio(ids, force=True))
        k = self.proxy.index(rows[0], COL_KEY).data(KEY_ROLE)
        if len(ids) == 1 and k:
            menu.addAction(icon("music"), f"Sons compatibles en harmonie ({key.camelot(k)})",
                           lambda: self.show_compatible(k))
        menu.addSeparator()
        if len(ids) == 1:
            menu.addAction(icon("replace"), "Remplacer le fichier…", lambda: self._replace(ids[0]))
        menu.addAction(icon("trash"), f"Supprimer de la bibliothèque…\t{'Maj+' if current else ''}Suppr",
                       lambda: self._delete_tracks(ids))
        menu.exec(self.view.viewport().mapToGlobal(pos))

    @staticmethod
    def _copy(text: str):
        QApplication.clipboard().setText(text)

    def _copy_files(self, rows: list[int]):
        data = QMimeData()
        data.setUrls([QUrl.fromLocalFile(str(self._path(r))) for r in rows])
        QApplication.clipboard().setMimeData(data)

    # --- lecture dans le lecteur intégré
    def _play_selected(self):
        rows = self._selected_rows()
        if rows:
            self._play_row(rows[0])

    def _play_row(self, row: int):
        if self.player is None or not 0 <= row < self.proxy.rowCount():
            return
        path = self._path(row)
        if not path.exists():
            QMessageBox.warning(self, "Lecture", f"Fichier introuvable :\n{path}")
            return
        self._playing = self.proxy.index(row, 0).data(ID_ROLE)
        self.view.selectRow(row)
        self.player.play(path, self.proxy.index(row, COL_ARTIST).data(), self.proxy.index(row, COL_TITLE).data(),
                         self._covers.get(self._playing))

    def play_step(self, step: int):
        """Son suivant (1) ou précédent (-1) dans la liste affichée, triée et filtrée ; sinon la sélection."""
        rows = [r for r in range(self.proxy.rowCount()) if self.proxy.index(r, 0).data(ID_ROLE) == self._playing]
        if rows:
            self._play_row(rows[0] + step)
        else:
            self._play_row((self._selected_rows() or [0])[0])

    def _delete_key(self):
        ids = self._selected_ids()
        current = self._current_playlist()
        if not ids:
            return
        if current is not None and current["kind"] == "manual":
            self._remove_from(current["id"], ids)
        elif current is None:
            self._delete_tracks(ids)

    def _delete_tracks(self, ids: list[int]):
        if not ids or QMessageBox.question(
                self, "Supprimer", f"Supprimer {plural(len(ids), 'son')} de la bibliothèque ?\n\n"
                "Les fichiers iront dans la corbeille ; ils disparaissent de toutes les playlists.") != QMessageBox.Yes:
            return
        raw = Path(self.lib.get_setting("raw_dir", ""))
        errors = []
        for rel in self.lib.delete_tracks(ids):
            try:
                if (raw / rel).exists():
                    send2trash(str(raw / rel))
            except OSError as e:
                errors.append(f"{rel} : {e}")
        for row in sorted((self._rows[i] for i in ids if i in self._rows), reverse=True):
            self.model.removeRow(row)
        self._rows = {self.model.item(r, 0).data(ID_ROLE): r for r in range(self.model.rowCount())}
        self.refresh_tags()
        self.refresh_playlists()
        self.window().statusBar().showMessage(f"{plural(len(ids), 'son')} supprimé(s)", 5000)
        if errors:
            QMessageBox.warning(self, "Supprimer", "Fichiers non mis à la corbeille :\n" + "\n".join(errors[:15]))

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
            self.window().statusBar().showMessage(
                f"{plural(len(ids), 'son')} ajouté(s) à « {self.lib.playlist(playlist_id)['name']} »", 5000)

    def _remove_from(self, playlist_id: int, ids: list[int]):
        self.lib.remove_from_playlist(playlist_id, ids)
        self.refresh_playlists()

    # --- remplacement par une meilleure version
    def _replace(self, track_id: int):
        exts = " ".join(f"*{e}" for e in sorted(manual.AUDIO_EXTS))
        path, _ = QFileDialog.getOpenFileName(self, "Nouvelle version du son", "", f"Audio ({exts})")
        if not path:
            return
        try:
            ffmpeg, ffprobe = config.ffmpeg_tools(self.lib.get_setting("ffmpeg_dir"))
            warnings = manual.replace_warnings(self.lib, track_id, Path(path))
        except Exception as e:
            QMessageBox.warning(self, "Remplacer", str(e))
            return
        t = self.lib.track(track_id)
        text = (f"Remplacer le fichier de « {t['artist']} – {t['title']} » par « {Path(path).name} » ?\n\n"
                "Playlists et tags sont conservés ; l'ancien fichier ira dans la corbeille.")
        if warnings:
            text += "\n\nAttention :\n" + "\n".join(f"• {w}" for w in warnings)
        if QMessageBox.question(self, "Remplacer", text) != QMessageBox.Yes:
            return
        self.window().statusBar().showMessage(f"Analyse de {Path(path).name}…")
        raw = Path(self.lib.get_setting("raw_dir"))

        def work():
            try:
                manual.replace_file(self.lib, track_id, Path(path), raw, ffmpeg, ffprobe)
                self._signals.replaced.emit(track_id, "")
            except Exception as e:
                self._signals.replaced.emit(track_id, str(e))

        threading.Thread(target=work, daemon=True).start()

    def _on_replaced(self, track_id: int, error: str):
        self.window().statusBar().clearMessage()
        if error:
            QMessageBox.warning(self, "Remplacer", f"Remplacement impossible : {error}")
            return
        self._set_row(self.lib.track(track_id), self.lib.tags_by_track().get(track_id, []))
        self.refresh_playlists()  # une playlist intelligente peut dépendre de la qualité
        self.window().statusBar().showMessage("Fichier remplacé", 5000)

    # --- export vers les logiciels DJ
    def export(self, selected: int | None = None, everything: bool = False):
        if selected is None and not everything and self._current_playlist() is not None:
            selected = self._current_playlist()["id"]
        dlg = ExportDialog(self.lib, selected, self)
        if not dlg.exec():
            return
        aiff_dir = Path(dlg.aiff_dir.text()) if dlg.convert.isChecked() else None
        try:
            ffmpeg, ffprobe = config.ffmpeg_tools(self.lib.get_setting("ffmpeg_dir")) if aiff_dir else (None, None)
        except FileNotFoundError as e:
            QMessageBox.warning(self, "Export", str(e))
            return
        args = (self.lib, dlg.playlist_ids(), Path(self.lib.get_setting("raw_dir")), dlg.chosen_formats(),
                Path(dlg.export_dir.text()), aiff_dir, ffmpeg, ffprobe, self._signals.export_progress.emit,
                version("dj-music-manager"))
        self.window().statusBar().showMessage("Export en cours…")

        def work():
            try:
                self._signals.exported.emit(export.run(*args), "")
            except Exception as e:
                self._signals.exported.emit({}, str(e))

        threading.Thread(target=work, daemon=True).start()

    def _on_exported(self, result: dict, error: str):
        self.window().statusBar().clearMessage()
        if error:
            QMessageBox.warning(self, "Export", f"Export impossible : {error}")
            return
        lines = []
        for key in ("rekordbox", "serato", "m3u8"):
            if key in result:
                where = result[key][0].parent if result[key] else "—"
                lines.append(f"• {len(result[key])} fichier(s) dans {where}\n  "
                             + HOW_TO[key].format(xml=result[key][0] if result[key] else ""))
        if result["missing"]:
            lines.append(f"{len(result['missing'])} son(s) sans fichier, ignoré(s) :\n"
                         + "\n".join(result["missing"][:15]))
        QMessageBox.information(self, "Export terminé", "\n\n".join(lines))
