import threading

from PySide6.QtCore import QObject, QSortFilterProxyModel, Qt, Signal
from PySide6.QtGui import QColor, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView, QLineEdit, QMainWindow, QMessageBox,
                               QProgressBar, QPushButton, QTableView, QTableWidget, QTableWidgetItem, QTabWidget,
                               QVBoxLayout, QWidget)

from .. import config, importer
from ..api_client import ApiClient
from ..deezer import DeezerPublic
from ..downloader import Downloader
from ..library import Library
from .dialogs import ImportDialog, SearchDialog, SettingsDialog, fmt_duration

SORT_ROLE = Qt.UserRole + 1
DURATION_WARNING = 10  # s d'écart avec la durée attendue : radio edit, clip, mauvaise version
BADGE_COLORS = {"Lossless": "#2e7d32", "320": "#558b2f", "256": "#9e9d24", "192": "#ef6c00", "128": "#c62828",
                "Faux FLAC": "#c62828", "Transcode": "#c62828"}
STATUS = {"queued": "En attente", "running": "En cours", "done": "Terminé", "failed": "Échec"}
TRACK_COLUMNS = ("Artiste", "Titre", "Qualité", "Durée", "BPM", "Format", "Album", "Ajouté le")


class Bridge(QObject):
    """Relaye vers le thread de l'UI les événements émis par les threads de travail."""
    job_event = Signal(str, int, object)
    preview_progress = Signal(int, int)
    preview_done = Signal(object)
    preview_failed = Signal(str)


def _item(text, sort=None) -> QStandardItem:
    item = QStandardItem("" if text is None else str(text))
    item.setEditable(False)
    item.setData(sort if sort is not None else item.text().casefold(), SORT_ROLE)
    return item


class MainWindow(QMainWindow):
    def __init__(self, lib: Library):
        super().__init__()
        self.lib = lib
        self.api: ApiClient | None = None
        self.downloader: Downloader | None = None
        self.deezer = DeezerPublic()
        self.setWindowTitle("DJ Music Manager")
        self.resize(1200, 750)

        self.bridge = Bridge()
        self.bridge.job_event.connect(self._on_job_event)
        self.bridge.preview_progress.connect(
            lambda i, n: self.statusBar().showMessage(f"Analyse : {i}/{n} pistes…"))
        self.bridge.preview_done.connect(self._on_preview)
        self.bridge.preview_failed.connect(self._on_preview_failed)

        self.link = QLineEdit(placeholderText="Lien d'album ou de playlist Deezer")
        self.link.returnPressed.connect(self._analyze)
        self.analyze_btn = QPushButton("Analyser", clicked=self._analyze)
        top = QHBoxLayout()
        top.addWidget(self.link)
        top.addWidget(self.analyze_btn)
        top.addWidget(QPushButton("Réglages", clicked=self._open_settings))

        self.tabs = QTabWidget()
        self.tabs.addTab(self._library_tab(), "Bibliothèque")
        self.queue = self._table(("Piste", "Qualité", "Statut", "Progression"))
        self.tabs.addTab(self.queue, "File d'attente")
        self.tabs.addTab(self._failures_tab(), "Échecs")

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.addLayout(top)
        layout.addWidget(self.tabs)
        self.setCentralWidget(central)

        self._job_rows: dict[int, int] = {}
        self._track_ids: set[int] = set()
        for row in lib.tracks():
            self._add_track_row(row)
        self._reload_failures()

    # --- construction
    @staticmethod
    def _table(headers) -> QTableWidget:
        t = QTableWidget(0, len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        t.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        t.verticalHeader().hide()
        return t

    def _library_tab(self) -> QWidget:
        self.model = QStandardItemModel(0, len(TRACK_COLUMNS))
        self.model.setHorizontalHeaderLabels(TRACK_COLUMNS)
        self.proxy = QSortFilterProxyModel(filterKeyColumn=-1, sortRole=SORT_ROLE)
        self.proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.proxy.setSourceModel(self.model)
        search = QLineEdit(placeholderText="Rechercher (artiste, titre, album, qualité…)")
        search.textChanged.connect(self.proxy.setFilterFixedString)
        view = QTableView(sortingEnabled=True)
        view.setModel(self.proxy)
        view.setSelectionBehavior(QAbstractItemView.SelectRows)
        view.verticalHeader().hide()
        view.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        for col, width in enumerate((200, 280, 90, 70, 60, 70, 200)):
            view.setColumnWidth(col, width)
        view.sortByColumn(7, Qt.DescendingOrder)
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.addWidget(search)
        layout.addWidget(view)
        return w

    def _failures_tab(self) -> QWidget:
        self.failures = self._table(("Piste", "Code", "Message"))
        self.failures.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        buttons = QHBoxLayout()
        buttons.addWidget(QPushButton("Relancer la sélection", clicked=self._retry_selected))
        buttons.addWidget(QPushButton("Chercher à la main…", clicked=self._search_selected))
        buttons.addStretch()
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.addWidget(self.failures)
        layout.addLayout(buttons)
        return w

    def _add_track_row(self, t):
        self._track_ids.add(t["id"])
        duration = _item(fmt_duration(t["duration"]), t["duration"] or 0)
        if t["expected_duration"] and t["duration"] and abs(t["duration"] - t["expected_duration"]) > DURATION_WARNING:
            duration.setText("⚠ " + duration.text())
            duration.setToolTip(f"Durée attendue : {fmt_duration(t['expected_duration'])}")
        quality = _item(t["badge"] or "?")
        quality.setToolTip(" · ".join(str(v) for v in (t["real_quality"], t["max_frequency"] and
                                                         f"coupure {t['max_frequency']} Hz") if v))
        if t["badge"] in BADGE_COLORS:
            quality.setForeground(QColor(BADGE_COLORS[t["badge"]]))
        self.model.appendRow([
            _item(t["artist"]), _item(t["title"]), quality, duration,
            _item(f"{t['bpm']:.0f}" if t["bpm"] else "", t["bpm"] or 0), _item(t["format"]), _item(t["album"]),
            _item(t["added_at"].replace("T", " "), t["added_at"]),
        ])

    # --- API et réglages
    def _ensure_api(self) -> bool:
        if not (config.get_api_key() and self.lib.get_setting("raw_dir")):
            if not SettingsDialog(self.lib, self).exec():
                return False
        if self.api is None:
            self.api = ApiClient(config.get_api_key())
            self.downloader = Downloader(self.api, self.lib, self.bridge.job_event.emit)
        return True

    def _open_settings(self):
        if SettingsDialog(self.lib, self).exec() and self.downloader:
            self.api = ApiClient(config.get_api_key())  # la clé a pu changer
            self.downloader.api = self.api

    # --- import
    def _analyze(self):
        url = self.link.text().strip()
        if not url or not self._ensure_api():
            return
        self.analyze_btn.setEnabled(False)

        def work():
            try:
                self.bridge.preview_done.emit(
                    importer.preview(self.api, self.deezer, self.lib, url, self.bridge.preview_progress.emit))
            except Exception as e:
                self.bridge.preview_failed.emit(str(e))

        threading.Thread(target=work, daemon=True).start()

    def _on_preview_failed(self, message: str):
        self.analyze_btn.setEnabled(True)
        self.statusBar().clearMessage()
        QMessageBox.warning(self, "Import", message)

    def _on_preview(self, p: importer.Preview):
        self.analyze_btn.setEnabled(True)
        self.statusBar().clearMessage()
        dlg = ImportDialog(p, self.lib.get_setting("default_quality", config.DEFAULT_QUALITY), self)
        if not dlg.exec():
            return
        job_ids = importer.queue(self.lib, p, dlg.quality.currentText())
        self.downloader.submit(job_ids)
        self.link.clear()
        self.statusBar().showMessage(f"« {p.title} » : {len(job_ids)} pistes en file, {len(p.existing)} déjà là", 8000)
        if job_ids:
            self.tabs.setCurrentWidget(self.queue)

    # --- file d'attente et échecs
    def _queue_row(self, job) -> int:
        row = self._job_rows.get(job["id"])
        if row is None:
            row = self._job_rows[job["id"]] = self.queue.rowCount()
            self.queue.insertRow(row)
            self.queue.setItem(row, 0, QTableWidgetItem(f"{job['artist']} – {job['title']}"))
            self.queue.setItem(row, 1, QTableWidgetItem(job["quality"]))
            self.queue.setItem(row, 2, QTableWidgetItem())
            self.queue.setCellWidget(row, 3, QProgressBar(value=0))
        return row

    def _on_job_event(self, kind: str, job_id: int, payload):
        job = self.lib.job(job_id)
        if job is None:
            return
        row = self._queue_row(job)
        bar = self.queue.cellWidget(row, 3)
        if kind == "progress":
            bar.setValue(payload)
            return
        self.queue.item(row, 2).setText(STATUS[job["status"]])
        self.queue.item(row, 2).setToolTip(job["error"] or "")
        if job["status"] == "queued":
            bar.setValue(0)
        elif job["status"] == "done":
            bar.setValue(100)
            if job["track_id"] not in self._track_ids:
                self._add_track_row(self.lib.track(job["track_id"]))
        if job["status"] in ("queued", "failed"):
            self._reload_failures()

    def _reload_failures(self):
        jobs = self.lib.failed_jobs()
        self.failures.setRowCount(len(jobs))
        for row, job in enumerate(jobs):
            name = QTableWidgetItem(f"{job['artist']} – {job['title']}")
            name.setData(Qt.UserRole, job["id"])
            self.failures.setItem(row, 0, name)
            self.failures.setItem(row, 1, QTableWidgetItem(str(job["http_status"] or "")))
            message = QTableWidgetItem((job["error"] or "").replace("\n", " "))
            message.setToolTip(job["error"] or "")
            self.failures.setItem(row, 2, message)
        self.tabs.setTabText(2, f"Échecs ({len(jobs)})" if jobs else "Échecs")

    def _selected_failures(self) -> list[int]:
        rows = sorted({i.row() for i in self.failures.selectedItems()})
        return [self.failures.item(r, 0).data(Qt.UserRole) for r in rows]

    def _retry_selected(self):
        ids = self._selected_failures()
        if ids and self._ensure_api():
            self.downloader.submit(ids)

    def _search_selected(self):
        ids = self._selected_failures()
        if not ids or not self._ensure_api():
            return
        job = self.lib.job(ids[0])
        dlg = SearchDialog(self.api, job["artist"], job["title"], job["expected_duration"], self)
        if dlg.exec():
            self.lib.update_job(job["id"], download_id=dlg.chosen["id"])
            self.downloader.submit([job["id"]])

    def closeEvent(self, event):
        if self.downloader:
            self.downloader.close()
            self.api.close()  # coupe les téléchargements en cours ; ils seront relançables au prochain démarrage
        super().closeEvent(event)
