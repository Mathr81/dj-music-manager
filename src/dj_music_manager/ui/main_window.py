import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QFileDialog, QHBoxLayout, QHeaderView, QLineEdit,
                               QMainWindow, QMessageBox, QProgressBar, QPushButton, QTableWidget, QTableWidgetItem,
                               QTabWidget, QVBoxLayout, QWidget)

from .. import config, importer, manual
from ..api_client import ApiClient
from ..deezer import DeezerPublic
from ..downloader import Downloader
from ..library import Library
from ..spotify import Spotify, is_spotify_link, track_ids_in
from .dialogs import ImportDialog, SearchDialog, SettingsDialog
from .library_panel import LibraryPanel

STATUS = {"queued": "En attente", "running": "En cours", "done": "Terminé", "failed": "Échec"}


class Bridge(QObject):
    """Relaye vers le thread de l'UI les événements émis par les threads de travail."""
    job_event = Signal(str, int, object)
    preview_progress = Signal(int, int)
    preview_done = Signal(object)
    preview_failed = Signal(str)
    manual_progress = Signal(int, int)
    track_added = Signal(int)
    manual_done = Signal(int, int, object)  # ajoutés, déjà présents, erreurs


class MainWindow(QMainWindow):
    def __init__(self, lib: Library):
        super().__init__()
        self.lib = lib
        self.api: ApiClient | None = None
        self.downloader: Downloader | None = None
        self.deezer = DeezerPublic()
        self.spotify: Spotify | None = None
        self.setWindowTitle("DJ Music Manager")
        self.resize(1200, 750)

        self.bridge = Bridge()
        self.bridge.job_event.connect(self._on_job_event)
        self.bridge.preview_progress.connect(
            lambda i, n: self.statusBar().showMessage(f"Analyse : {i}/{n} pistes…"))
        self.bridge.preview_done.connect(self._on_preview)
        self.bridge.preview_failed.connect(self._on_preview_failed)
        self.bridge.manual_progress.connect(
            lambda i, n: self.statusBar().showMessage(f"Ajout et analyse : {i}/{n} fichiers…"))
        self.bridge.manual_done.connect(self._on_manual_done)
        self.setAcceptDrops(True)

        self.link = QLineEdit(placeholderText="Lien d'album ou de playlist Deezer ou Spotify")
        self.link.returnPressed.connect(self._analyze)
        self.analyze_btn = QPushButton("Analyser", clicked=self._analyze)
        top = QHBoxLayout()
        top.addWidget(self.link)
        top.addWidget(self.analyze_btn)
        top.addWidget(QPushButton("Scanner un dossier…", clicked=self._scan_folder))
        top.addWidget(QPushButton("Réglages", clicked=self._open_settings))

        self.tabs = QTabWidget()
        self.panel = LibraryPanel(lib)
        self.bridge.track_added.connect(self.panel.show_track)
        self.tabs.addTab(self.panel, "Bibliothèque")
        self.queue = self._table(("Piste", "Qualité", "Statut", "Progression"))
        self.tabs.addTab(self.queue, "File d'attente")
        self.tabs.addTab(self._failures_tab(), "Échecs")

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.addLayout(top)
        layout.addWidget(self.tabs)
        self.setCentralWidget(central)

        self._job_rows: dict[int, int] = {}
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

    # --- API et réglages
    def _ensure_api(self) -> bool:
        if not (config.get_api_key() and self.lib.get_setting("raw_dir") and self.lib.get_setting("api_url")):
            if not SettingsDialog(self.lib, self).exec():
                return False
        if self.api is None:
            self.api = ApiClient(config.get_api_key(), self.lib.get_setting("api_url"))
            self.downloader = Downloader(self.api, self.lib, self.bridge.job_event.emit)
        return True

    def _open_settings(self):
        if SettingsDialog(self.lib, self).exec() and self.downloader:
            self.api = ApiClient(config.get_api_key(), self.lib.get_setting("api_url"))  # clé ou URL ont pu changer
            self.downloader.api = self.api

    def _spotify_client(self) -> Spotify | None:
        client_id, token = self.lib.get_setting("spotify_client_id"), config.get_spotify_token()
        if not (client_id and token):
            return None
        if self.spotify is None or (self.spotify.client_id, self.spotify.refresh_token) != (client_id, token):
            self.spotify = Spotify(client_id, token, config.set_spotify_token)
        return self.spotify

    # --- import
    def _analyze(self):
        url = self.link.text().strip()
        if not url or not self._ensure_api():
            return
        self.analyze_btn.setEnabled(False)
        spotify = self._spotify_client() if is_spotify_link(url) else None

        def work():
            try:
                self.bridge.preview_done.emit(importer.preview(
                    self.api, self.deezer, self.lib, url, self.bridge.preview_progress.emit, spotify))
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
        result = dlg.exec()
        if result == ImportDialog.PASTE:
            self._paste_full(p)
            return
        if not result:
            return
        job_ids = importer.queue(self.lib, p, dlg.quality.currentText())
        self.panel.refresh_playlists()
        self.downloader.submit(job_ids)
        self.link.clear()
        self.statusBar().showMessage(f"« {p.title} » : {len(job_ids)} pistes en file, {len(p.existing)} déjà là", 8000)
        if job_ids:
            self.tabs.setCurrentWidget(self.queue)

    def _paste_full(self, base: importer.Preview):
        """Liste complète d'une playlist Spotify tronquée, depuis les liens copiés dans l'app Spotify."""
        text = QApplication.clipboard().text()
        if not track_ids_in(text):
            QMessageBox.information(self, "Liste complète", importer.PASTE_HINT)
            self._on_preview(base)  # on revient à l'aperçu pour recliquer une fois la copie faite
            return
        self.analyze_btn.setEnabled(False)
        spotify = self._spotify_client()

        def work():
            try:
                self.bridge.preview_done.emit(importer.preview_pasted(
                    base, text, self.lib, spotify, self.bridge.preview_progress.emit))
            except Exception as e:
                self.bridge.preview_failed.emit(str(e))

        threading.Thread(target=work, daemon=True).start()

    # --- ajout manuel (glisser-déposer ou scan)
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        self._add_paths([u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()])

    def _scan_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Dossier à scanner")
        if folder:
            self._add_paths([folder])

    def _add_paths(self, paths: list[str]):
        if not self.lib.get_setting("raw_dir") and not SettingsDialog(self.lib, self).exec():
            return
        try:
            ffmpeg, ffprobe = config.ffmpeg_tools(self.lib.get_setting("ffmpeg_dir"))
        except FileNotFoundError as e:
            QMessageBox.warning(self, "Ajout", str(e))
            return
        files = manual.collect(paths)
        if not files:
            QMessageBox.information(self, "Ajout", "Aucun fichier audio trouvé.")
            return
        raw = Path(self.lib.get_setting("raw_dir"))

        def work():
            added = known = 0
            errors = []
            # l'analyse spectrale est gourmande en CPU et en mémoire (~250 Mo par fichier en cours)
            with ThreadPoolExecutor(min(4, max(1, (os.cpu_count() or 2) // 2))) as pool:
                futures = {pool.submit(manual.add_file, self.lib, f, raw, ffmpeg, ffprobe): f for f in files}
                for i, future in enumerate(as_completed(futures), 1):
                    try:
                        created, track_id = future.result()
                        if created:
                            added += 1
                            self.bridge.track_added.emit(track_id)
                        else:
                            known += 1
                    except Exception as e:
                        errors.append(f"{futures[future].name} : {e}")
                    self.bridge.manual_progress.emit(i, len(files))
            self.bridge.manual_done.emit(added, known, errors)

        threading.Thread(target=work, daemon=True).start()

    def _on_manual_done(self, added: int, known: int, errors: list[str]):
        self.statusBar().clearMessage()
        text = f"{added} ajouté(s), {known} déjà dans la bibliothèque, {len(errors)} erreur(s)."
        if errors:
            text += "\n\n" + "\n".join(errors[:20])
        QMessageBox.information(self, "Ajout terminé", text)

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
            self.panel.show_track(job["track_id"])
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
