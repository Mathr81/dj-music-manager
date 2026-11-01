import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from importlib.metadata import version

from PySide6.QtCore import QObject, QSettings, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QFileDialog, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QMainWindow, QMenu, QMessageBox, QProgressBar, QPushButton, QTableWidget,
                               QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from .. import config, importer, manual
from ..api_client import ApiClient
from ..deezer import DeezerPublic
from ..downloader import Downloader
from ..library import Library
from ..spotify import Spotify, track_ids_in
from .dialogs import ImportDialog, SearchDialog, SettingsDialog, plural
from .icons import icon
from .library_panel import MIME_IDS, LibraryPanel, reveal, search_menu
from .player import PlayerBar

STATUS = {"queued": "En attente", "running": "En cours", "done": "Terminé", "failed": "Échec"}
STATUS_COLORS = {"queued": "#8b91a0", "running": "#34c3ff", "done": "#5fd068", "failed": "#ff6b6b"}
SHORTCUTS = """\
<table cellpadding=3>
<tr><td><b>Ctrl+L</b></td><td>Barre de lien / recherche</td></tr>
<tr><td><b>Ctrl+Maj+V</b></td><td>Coller un lien et l'analyser</td></tr>
<tr><td><b>Ctrl+O</b></td><td>Scanner un dossier</td></tr>
<tr><td><b>Ctrl+F</b></td><td>Rechercher dans la bibliothèque</td></tr>
<tr><td><b>Échap</b></td><td>Effacer les filtres</td></tr>
<tr><td><b>Ctrl+N / Ctrl+Maj+N</b></td><td>Nouvelle playlist / intelligente</td></tr>
<tr><td><b>Ctrl+E</b></td><td>Exporter pour logiciel DJ</td></tr>
<tr><td><b>Ctrl+1 / 2 / 3</b></td><td>Bibliothèque / File d'attente / Échecs</td></tr>
<tr><td><b>Ctrl+P</b></td><td>Pause / reprise des téléchargements</td></tr>
<tr><td><b>Ctrl+,</b></td><td>Réglages</td></tr>
<tr><td><b>Entrée, double-clic</b></td><td>Écouter le son</td></tr>
<tr><td><b>Espace</b></td><td>Lecture / pause</td></tr>
<tr><td><b>Ctrl+→ / Ctrl+←</b></td><td>Son suivant / précédent</td></tr>
<tr><td><b>Ctrl+C</b></td><td>Copier « Artiste – Titre »</td></tr>
<tr><td><b>Ctrl+T</b></td><td>Ajouter des tags</td></tr>
<tr><td><b>Suppr</b></td><td>Retirer de la playlist (dans « Toute la bibliothèque » : supprimer)</td></tr>
<tr><td><b>Maj+Suppr</b></td><td>Supprimer de la bibliothèque</td></tr>
<tr><td><b>F2</b></td><td>Renommer la playlist</td></tr>
</table>
<p>Glisse des sons sur une playlist pour les y ajouter, ou hors de l'app (rekordbox, Explorateur) comme fichiers.
Glisse des fichiers audio sur la fenêtre pour les ajouter à la bibliothèque.</p>"""


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

        self.link = QLineEdit(placeholderText="Colle un lien Deezer ou Spotify (titre, album, playlist), "
                                              "ou cherche : artiste titre", objectName="link", clearButtonEnabled=True)
        self.link.returnPressed.connect(self._analyze)
        self.analyze_btn = QPushButton("Analyser", clicked=self._analyze, objectName="primary")
        top = QHBoxLayout()
        top.addWidget(self.link)
        top.addWidget(self.analyze_btn)
        top.addWidget(QPushButton(icon("folder"), " Scanner…", clicked=self._scan_folder,
                                  toolTip="Ajouter les fichiers audio d'un dossier (Ctrl+O)"))
        top.addWidget(QPushButton(icon("settings"), " Réglages", clicked=self._open_settings, toolTip="Ctrl+,"))

        self.tabs = QTabWidget()
        self.panel = LibraryPanel(lib)
        self.panel.resync_requested.connect(self._resync)
        self.bridge.track_added.connect(self.panel.show_track)
        self.tabs.addTab(self.panel, icon("library"), "Bibliothèque")
        self.queue_tab = self._queue_tab()
        self.tabs.addTab(self.queue_tab, icon("download"), "File d'attente")
        self.tabs.addTab(self._failures_tab(), icon("alert"), "Échecs")

        self.player = PlayerBar(lambda: config.ffmpeg_tools(self.lib.get_setting("ffmpeg_dir"))[0])
        self.panel.player = self.player
        self.player.next_requested.connect(lambda: self.panel.play_step(1))
        self.player.previous_requested.connect(lambda: self.panel.play_step(-1))

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        body = QVBoxLayout()
        body.setContentsMargins(9, 9, 9, 9)
        body.addLayout(top)
        body.addWidget(self.tabs)
        layout.addLayout(body, 1)
        layout.addWidget(self.player)
        self.setCentralWidget(central)

        self._job_rows: dict[int, int] = {}
        self._was_active = False
        self.queue_status = QLabel()
        self.statusBar().addPermanentWidget(self.queue_status)
        self._reload_failures()
        self._build_menus()
        geometry = QSettings().value("window/geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)

    def _build_menus(self):
        bar = self.menuBar()
        m = bar.addMenu("&Fichier")
        m.addAction("Analyser un lien ou chercher…", "Ctrl+L", self._focus_link)
        m.addAction("Coller et analyser", "Ctrl+Shift+V", self._paste_and_analyze)
        m.addAction("Scanner un dossier…", "Ctrl+O", self._scan_folder).setIcon(icon("folder"))
        m.addAction("Ajouter des fichiers…", self._add_files)
        m.addSeparator()
        m.addAction("Exporter pour logiciel DJ…", "Ctrl+E", lambda: self.panel.export()).setIcon(icon("export"))
        m.addSeparator()
        m.addAction("Ouvrir le dossier RAW", lambda: self._open_dir(self.lib.get_setting("raw_dir")))
        m.addAction("Ouvrir le dossier des données", lambda: self._open_dir(config.data_dir()))
        m.addAction("Réglages…", "Ctrl+,", self._open_settings).setIcon(icon("settings"))
        m.addSeparator()
        m.addAction("Quitter", "Ctrl+Q", self.close)

        m = bar.addMenu("&Bibliothèque")
        m.addAction("Rechercher", "Ctrl+F", self._focus_search).setIcon(icon("search"))
        m.addAction("Effacer les filtres", self.panel.clear_filters)
        m.addSeparator()
        m.addAction("Nouvelle playlist…", "Ctrl+N", lambda: self.panel.new_playlist()).setIcon(icon("plus"))
        m.addAction("Nouvelle playlist intelligente…", "Ctrl+Shift+N", self.panel.new_smart).setIcon(icon("smart"))

        m.addSeparator()
        m.addAction(icon("activity"), "Calculer les BPM manquants",
                    lambda: self.panel.detect_bpm([t["id"] for t in self.lib.tracks()]))

        m = bar.addMenu("&Lecture")
        m.addAction("Lecture / pause", "Space", self.player.toggle).setIcon(icon("play"))
        m.addAction("Suivant", "Ctrl+Right", lambda: self.panel.play_step(1)).setIcon(icon("skip-forward"))
        m.addAction("Précédent", "Ctrl+Left", lambda: self.panel.play_step(-1)).setIcon(icon("skip-back"))
        m.addAction("Arrêter", self.player.stop)

        m = bar.addMenu("&Affichage")
        for i, name in enumerate(("Bibliothèque", "File d'attente", "Échecs"), 1):
            m.addAction(name, f"Ctrl+{i}", lambda i=i: self.tabs.setCurrentIndex(i - 1))

        m = bar.addMenu("&Téléchargements")
        self.pause_action = m.addAction("Mettre en pause", "Ctrl+P", self._toggle_pause)
        self.pause_action.setIcon(icon("pause"))
        m.addAction(icon("refresh"), "Relancer tous les échecs", self._retry_all)
        m.addSeparator()
        m.addAction("Retirer les terminés de la file", self._clear_done)
        m.addAction(icon("trash"), "Vider la file…", self._clear_queue)
        m.addSeparator()
        m.addAction(icon("file"), "Journal des erreurs API", self._open_api_log)

        m = bar.addMenu("&Aide")
        m.addAction("Raccourcis clavier", "F1",
                    lambda: QMessageBox.information(self, "Raccourcis clavier", SHORTCUTS)).setIcon(icon("keyboard"))
        m.addAction("Créer l'app Spotify (developer.spotify.com)",
                    lambda: QDesktopServices.openUrl(QUrl("https://developer.spotify.com/dashboard")))
        m.addSeparator()
        m.addAction("À propos", lambda: QMessageBox.about(
            self, "À propos", f"<h3>DJ Music Manager {version('dj-music-manager')}</h3>"
                              "<p>Bibliothèque musicale pour DJ : un dossier RAW, chaque son une seule fois, "
                              "playlists et tags par-dessus, export vers rekordbox, Serato, VirtualDJ et Mixxx.</p>"))

    def _focus_link(self):
        self.link.setFocus()
        self.link.selectAll()

    def _focus_search(self):
        self.tabs.setCurrentWidget(self.panel)
        self.panel.focus_search()

    def _paste_and_analyze(self):
        self.link.setText(QApplication.clipboard().text().strip())
        self._analyze()

    def _resync(self, url: str):
        self.link.setText(url)
        self._analyze()

    def _open_dir(self, path):
        if path and Path(path).is_dir():
            os.startfile(path)
        else:
            QMessageBox.information(self, "Dossier", "Dossier introuvable : vérifie les Réglages.")

    def _add_files(self):
        exts = " ".join(f"*{e}" for e in sorted(manual.AUDIO_EXTS))
        paths, _ = QFileDialog.getOpenFileNames(self, "Ajouter des fichiers", "", f"Audio ({exts})")
        if paths:
            self._add_paths(paths)

    # --- construction
    @staticmethod
    def _table(headers) -> QTableWidget:
        t = QTableWidget(0, len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        t.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        t.horizontalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        t.horizontalHeader().setHighlightSections(False)
        t.verticalHeader().hide()
        t.verticalHeader().setDefaultSectionSize(30)
        t.setAlternatingRowColors(True)
        t.setShowGrid(False)
        return t

    def _queue_tab(self) -> QWidget:
        self.queue = self._table(("Piste", "Qualité", "Statut", "Progression"))
        self.queue.setContextMenuPolicy(Qt.CustomContextMenu)
        self.queue.customContextMenuRequested.connect(self._queue_menu)
        self.pause_btn = QPushButton(icon("pause"), " Mettre en pause", clicked=self._toggle_pause)
        buttons = QHBoxLayout()
        buttons.addWidget(self.pause_btn)
        buttons.addWidget(QPushButton("Supprimer la sélection", clicked=self._remove_queued))
        buttons.addWidget(QPushButton(icon("trash"), " Vider la file", clicked=self._clear_queue))
        buttons.addStretch()
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.addWidget(self.queue)
        layout.addLayout(buttons)
        return w

    def _failures_tab(self) -> QWidget:
        self.failures = self._table(("Piste", "Code", "Message"))
        self.failures.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.failures.setContextMenuPolicy(Qt.CustomContextMenu)
        self.failures.customContextMenuRequested.connect(self._failures_menu)
        self.failures.doubleClicked.connect(lambda _: self._search_selected())
        buttons = QHBoxLayout()
        buttons.addWidget(QPushButton(icon("refresh"), " Relancer la sélection", clicked=self._retry_selected))
        buttons.addWidget(QPushButton(icon("search"), " Chercher à la main…", clicked=self._search_selected))
        buttons.addWidget(QPushButton("Supprimer la sélection", clicked=self._remove_failures))
        buttons.addWidget(QPushButton("Tout effacer", clicked=self._clear_failures))
        buttons.addStretch()
        buttons.addWidget(QPushButton(icon("file"), " Journal des erreurs API", clicked=self._open_api_log))
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
        chosen = None
        if not importer.is_link(url):  # texte libre : recherche, puis le titre choisi
            dlg = SearchDialog(self.api, url, "", None, self)
            if not dlg.exec():
                return
            chosen = dlg.chosen
        self.analyze_btn.setEnabled(False)
        spotify = self._spotify_client()

        def work():
            try:
                p = importer.track_preview(self.deezer, self.lib, url, chosen, spotify) or importer.preview(
                    self.api, self.deezer, self.lib, url, self.bridge.preview_progress.emit, spotify)
                self.bridge.preview_done.emit(p)
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
        if p.single:  # un titre : directement en file, qualité par défaut
            self.link.clear()
            if p.existing:
                self.statusBar().showMessage(f"« {p.title} » est déjà dans la bibliothèque", 8000)
                return
            self.downloader.submit(importer.queue(
                self.lib, p, self.lib.get_setting("default_quality", config.DEFAULT_QUALITY)))
            self.statusBar().showMessage(f"« {p.title} » en file", 8000)
            self.tabs.setCurrentWidget(self.queue_tab)
            return
        dlg = ImportDialog(p, self.lib.get_setting("default_quality", config.DEFAULT_QUALITY), self)
        result = dlg.exec()
        if result == ImportDialog.PASTE:
            self._paste_full(p)
            return
        if not result:
            return
        quality = dlg.quality.currentText()
        job_ids = importer.queue(self.lib, p, quality)
        self.panel.refresh_playlists()
        self.link.clear()
        self.statusBar().showMessage(f"« {p.title} » : {len(job_ids)} pistes en file, {len(p.existing)} déjà là", 8000)
        if job_ids or p.total:  # aperçu tronqué : le bot peut encore apporter des titres
            self.downloader.submit_collection(p, quality, job_ids)
            self.tabs.setCurrentWidget(self.queue_tab)

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
        if event.mimeData().hasUrls() and not event.mimeData().hasFormat(MIME_IDS):  # pas nos propres sons
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
            name = QTableWidgetItem(f"{job['artist']} – {job['title']}")
            name.setData(Qt.UserRole, job["id"])
            self.queue.setItem(row, 0, name)
            self.queue.setItem(row, 1, QTableWidgetItem(job["quality"]))
            self.queue.setItem(row, 2, QTableWidgetItem())
            self.queue.setCellWidget(row, 3, QProgressBar(value=0, textVisible=False))
        return row

    def _on_job_event(self, kind: str, job_id: int, payload):
        if kind == "collection":
            self.statusBar().showMessage(payload)
            return
        job = self.lib.job(job_id)
        if job is None:
            return
        row = self._queue_row(job)
        bar = self.queue.cellWidget(row, 3)
        if kind == "progress":
            bar.setValue(payload)
            bar.setToolTip(f"{payload} %")
            return
        self.queue.item(row, 2).setText(STATUS[job["status"]])
        self.queue.item(row, 2).setForeground(QColor(STATUS_COLORS[job["status"]]))
        self.queue.item(row, 2).setToolTip(job["error"] or "")
        if job["status"] == "queued":
            bar.setValue(0)
        elif job["status"] == "done":
            bar.setValue(100)
            self.panel.show_track(job["track_id"])
        if job["status"] in ("queued", "failed"):
            self._reload_failures()
        self._update_queue_status()

    def _update_queue_status(self):
        counts = {}
        for r in range(self.queue.rowCount()):
            text = self.queue.item(r, 2).text()
            counts[text] = counts.get(text, 0) + 1
        active = counts.get(STATUS["queued"], 0) + counts.get(STATUS["running"], 0)
        self.queue_status.setText(f"Téléchargements : {active} en cours · "
                                  f"{plural(counts.get(STATUS['done'], 0), 'terminé')}" if self.queue.rowCount() else "")
        self.tabs.setTabText(1, f"File d'attente ({active})" if active else "File d'attente")
        if self._was_active and not active:
            self.statusBar().showMessage("Tous les téléchargements sont terminés", 10000)
            QApplication.alert(self)  # fait clignoter l'app dans la barre des tâches
        self._was_active = bool(active)

    def _queue_menu(self, pos):
        rows = sorted({i.row() for i in self.queue.selectedItems()})
        if not rows:
            return
        jobs = [self.lib.job(self.queue.item(r, 0).data(Qt.UserRole)) for r in rows]
        menu = QMenu(self)
        done = [j for j in jobs if j and j["status"] == "done" and j["track_id"]]
        if len(done) == 1 and (t := self.lib.track(done[0]["track_id"])):
            menu.addAction(icon("folder"), "Afficher dans l'Explorateur",
                           lambda: reveal(Path(self.lib.get_setting("raw_dir", "")) / t["path"]))
        failed = [j["id"] for j in jobs if j and j["status"] == "failed"]
        if failed:
            menu.addAction(icon("refresh"), "Relancer", lambda: self._ensure_api() and self.downloader.submit(failed))
        menu.addAction(icon("copy"), "Copier « Artiste – Titre »", lambda: QApplication.clipboard().setText(
            "\n".join(self.queue.item(r, 0).text() for r in rows)))
        if len(rows) == 1:
            search_menu(menu, self.queue.item(rows[0], 0).text().replace(" – ", " "))
        menu.addSeparator()
        menu.addAction(icon("x"), "Retirer de la file (annule si en cours)", self._remove_queued)
        menu.exec(self.queue.viewport().mapToGlobal(pos))

    def _failures_menu(self, pos):
        rows = sorted({i.row() for i in self.failures.selectedItems()})
        if not rows:
            return
        menu = QMenu(self)
        menu.addAction(icon("refresh"), "Relancer", self._retry_selected)
        if len(rows) == 1:
            menu.addAction(icon("search"), "Chercher à la main…", self._search_selected)
            search_menu(menu, self.failures.item(rows[0], 0).text().replace(" – ", " "))
        copy = menu.addMenu(icon("copy"), "Copier")
        copy.addAction("« Artiste – Titre »", lambda: QApplication.clipboard().setText(
            "\n".join(self.failures.item(r, 0).text() for r in rows)))
        copy.addAction("Message d'erreur", lambda: QApplication.clipboard().setText(
            "\n".join(f"{self.failures.item(r, 0).text()} : {self.failures.item(r, 2).toolTip()}" for r in rows)))
        menu.addSeparator()
        menu.addAction(icon("trash"), "Supprimer", self._remove_failures)
        menu.exec(self.failures.viewport().mapToGlobal(pos))

    def _retry_all(self):
        jobs = self.lib.failed_jobs()
        if jobs and self._ensure_api():
            self.downloader.submit([j["id"] for j in jobs])

    def _clear_done(self):
        self._drop_from_queue([r for r in range(self.queue.rowCount())
                               if self.queue.item(r, 2).text() == STATUS["done"]])

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

    def _toggle_pause(self):
        if not self.downloader:
            return
        if self.downloader.paused:
            self.downloader.resume()
        else:
            self.downloader.pause()
        self.pause_btn.setText("Reprendre" if self.downloader.paused else "Mettre en pause")
        self.pause_action.setText(self.pause_btn.text())
        for w in (self.pause_btn, self.pause_action):
            w.setIcon(icon("play" if self.downloader.paused else "pause"))
        self.statusBar().showMessage("File en pause : les téléchargements en cours se terminent"
                                     if self.downloader.paused else "File relancée", 5000)

    def _drop_from_queue(self, rows: list[int]):
        """Retire ces lignes de la file : jobs en attente ou en cours annulés et supprimés, terminés juste masqués."""
        ids = [self.queue.item(r, 0).data(Qt.UserRole) for r in rows]
        pending = [i for i in ids if (job := self.lib.job(i)) and job["status"] in ("queued", "running")]
        if self.downloader:
            self.downloader.cancel(pending)
        self.lib.delete_jobs(pending)
        for r in sorted(rows, reverse=True):
            self.queue.removeRow(r)
        self._job_rows = {self.queue.item(r, 0).data(Qt.UserRole): r for r in range(self.queue.rowCount())}
        self._update_queue_status()

    def _remove_queued(self):
        self._drop_from_queue(sorted({i.row() for i in self.queue.selectedItems()}))

    def _clear_queue(self):
        if self.queue.rowCount() and QMessageBox.question(
                self, "Vider la file", "Annuler tous les téléchargements en attente et en cours ?") == QMessageBox.Yes:
            self._drop_from_queue(list(range(self.queue.rowCount())))

    def _remove_failures(self):
        self.lib.delete_jobs(self._selected_failures())
        self._reload_failures()

    def _clear_failures(self):
        jobs = self.lib.failed_jobs()
        if jobs and QMessageBox.question(self, "Échecs", f"Effacer les {len(jobs)} échecs ?") == QMessageBox.Yes:
            self.lib.delete_jobs([j["id"] for j in jobs])
            self._reload_failures()

    def _open_api_log(self):
        path = config.api_log_path()
        if not path.exists():
            QMessageBox.information(self, "Journal des erreurs API", "Aucune erreur de l'API enregistrée pour l'instant.")
            return
        os.startfile(path)  # éditeur de texte par défaut de Windows

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
        QSettings().setValue("window/geometry", self.saveGeometry())
        self.panel.save_state()
        self.panel.close_workers()
        self.player.stop()
        if self.downloader:
            self.downloader.close()
            self.api.close()  # coupe les téléchargements en cours ; ils seront relançables au prochain démarrage
        super().closeEvent(event)
