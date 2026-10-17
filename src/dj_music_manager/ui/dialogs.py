import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
                               QPushButton, QVBoxLayout)

from .. import config, spotify
from ..api_client import QUALITIES, ApiClient
from ..importer import Preview, parse_duration
from ..library import Library


def fmt_duration(s: float | None) -> str:
    return f"{int(s) // 60}:{int(s) % 60:02d}" if s is not None else ""


def plural(n: int, word: str) -> str:
    return f"{n} {word}{'s' if n > 1 else ''}"


def _quality_combo(current: str) -> QComboBox:
    combo = QComboBox()
    combo.addItems(QUALITIES)
    combo.setCurrentText(current)
    return combo


class _AuthBridge(QObject):
    done = Signal(str, str)  # refresh token, erreur


class SettingsDialog(QDialog):
    def __init__(self, lib: Library, parent=None):
        super().__init__(parent)
        self.lib = lib
        self._auth = _AuthBridge(self)
        self._auth.done.connect(self._on_spotify_auth)
        self.setWindowTitle("Réglages")
        self.raw = QLineEdit(lib.get_setting("raw_dir", ""))
        browse = QPushButton("Parcourir…", clicked=self._browse)
        raw_row = QHBoxLayout()
        raw_row.addWidget(self.raw)
        raw_row.addWidget(browse)
        self.quality = _quality_combo(lib.get_setting("default_quality", config.DEFAULT_QUALITY))
        self.api_url = QLineEdit(lib.get_setting("api_url", ""), placeholderText="https://…")
        self.key = QLineEdit(echoMode=QLineEdit.Password)
        self.key.setPlaceholderText("inchangée" if config.get_api_key() else "obligatoire")
        self.ffmpeg = QLineEdit(lib.get_setting("ffmpeg_dir", ""),
                                placeholderText="vide = ffmpeg/ffprobe trouvés dans le PATH")
        form = QFormLayout(self)
        form.addRow("Dossier RAW", raw_row)
        form.addRow("Qualité par défaut", self.quality)
        form.addRow("URL de l'API", self.api_url)
        form.addRow("Clé API", self.key)
        form.addRow(QLabel("La clé est stockée dans le Gestionnaire d'identifiants Windows."))
        self.spotify_id = QLineEdit(lib.get_setting("spotify_client_id", ""))
        self.connect_btn = QPushButton("Connecter Spotify", clicked=self._connect_spotify)
        self.spotify_status = QLabel("Connecté" if config.get_spotify_token() else "Non connecté")
        spotify_row = QHBoxLayout()
        spotify_row.addWidget(self.connect_btn)
        spotify_row.addWidget(self.spotify_status, 1)
        form.addRow("Spotify Client ID", self.spotify_id)
        form.addRow("", spotify_row)
        help_label = QLabel(
            "Facultatif : les playlists publiques s'importent sans connexion (100 premiers titres).<br>"
            "La connexion sert à tes playlists privées ou de plus de 100 titres : crée une app sur "
            "<a href='https://developer.spotify.com/dashboard'>developer.spotify.com</a> (Web API) avec l'URI "
            f"de redirection <code>{spotify.REDIRECT_URI}</code> ; ton compte doit être Premium.",
            openExternalLinks=True, wordWrap=True)
        form.addRow(help_label)
        form.addRow("Dossier ffmpeg", self.ffmpeg)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Dossier RAW", self.raw.text())
        if d:
            self.raw.setText(d)

    def _connect_spotify(self):
        client_id = self.spotify_id.text().strip()
        if not client_id:
            QMessageBox.warning(self, "Spotify", "Renseigne d'abord le Client ID de ton app Spotify.")
            return
        self.connect_btn.setEnabled(False)
        self.spotify_status.setText("En attente de la connexion dans le navigateur…")

        def work():
            try:
                self._auth.done.emit(spotify.authorize(client_id), "")
            except Exception as e:
                self._auth.done.emit("", str(e))

        threading.Thread(target=work, daemon=True).start()

    def _on_spotify_auth(self, token: str, error: str):
        self.connect_btn.setEnabled(True)
        if error:
            self.spotify_status.setText(f"Échec : {error}")
            return
        config.set_spotify_token(token)
        self.lib.set_setting("spotify_client_id", self.spotify_id.text().strip())
        self.spotify_status.setText("Connecté")

    def accept(self):
        raw = self.raw.text().strip()
        if not raw or not Path(raw).is_dir():
            QMessageBox.warning(self, "Réglages", "Choisis un dossier RAW existant.")
            return
        api_url = self.api_url.text().strip().rstrip("/")
        if not api_url.startswith(("https://", "http://")):
            QMessageBox.warning(self, "Réglages", "L'URL de l'API doit commencer par https://")
            return
        ffmpeg_dir = self.ffmpeg.text().strip()
        if ffmpeg_dir and not (Path(ffmpeg_dir) / "ffprobe.exe").is_file():
            QMessageBox.warning(self, "Réglages", "Ce dossier ne contient pas ffmpeg.exe et ffprobe.exe.")
            return
        if not self.key.text().strip() and not config.get_api_key():
            QMessageBox.warning(self, "Réglages", "La clé API est obligatoire.")
            return
        self.lib.set_setting("raw_dir", str(Path(raw)))
        self.lib.set_setting("default_quality", self.quality.currentText())
        self.lib.set_setting("spotify_client_id", self.spotify_id.text().strip())
        self.lib.set_setting("api_url", api_url)
        self.lib.set_setting("ffmpeg_dir", ffmpeg_dir)
        if self.key.text().strip():
            config.set_api_key(self.key.text().strip())
        super().accept()


class ImportDialog(QDialog):
    PASTE = 2  # code de retour : coller la liste complète copiée dans l'app Spotify

    def __init__(self, preview: Preview, quality: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import")
        n_new = len(preview.new)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            f"<b>{preview.title}</b> : {plural(len(preview.tracks), 'piste')}<br>"
            f"{len(preview.existing)} déjà dans la bibliothèque, <b>{plural(n_new, 'nouvelle')}</b>"))
        if preview.warning:
            warning = QLabel(f"⚠ {preview.warning}", wordWrap=True, maximumWidth=480)
            warning.setStyleSheet("color: #ef6c00")
            layout.addWidget(warning)
        if preview.total:  # liste tronquée : la copie depuis l'app Spotify donne tous les titres
            layout.addWidget(QPushButton(f"Coller la liste complète ({preview.total} titres)…",
                                         clicked=lambda: self.done(self.PASTE)))
        self.quality = _quality_combo(quality)
        row = QHBoxLayout()
        row.addWidget(QLabel("Qualité"))
        row.addWidget(self.quality)
        layout.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText(f"Télécharger {plural(n_new, 'piste')}" if n_new else "Mettre à jour la playlist")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class SearchDialog(QDialog):
    """Recherche manuelle d'une piste en échec ; self.chosen = résultat de /search retenu."""

    def __init__(self, api: ApiClient, artist: str, title: str, expected: float | None, parent=None):
        super().__init__(parent)
        self.api = api
        self.chosen = None
        self.setWindowTitle("Chercher à la main")
        self.query = QLineEdit(f"{artist} {title}")
        self.query.returnPressed.connect(self._search)
        row = QHBoxLayout()
        row.addWidget(self.query)
        row.addWidget(QPushButton("Chercher", clicked=self._search))
        self.results = QListWidget()
        self.results.itemDoubleClicked.connect(self.accept)
        layout = QVBoxLayout(self)
        layout.addLayout(row)
        layout.addWidget(QLabel(f"Durée attendue : {fmt_duration(expected) or 'inconnue'}"))
        layout.addWidget(self.results)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Télécharger celle-ci")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(520, 360)
        self._search()

    def _search(self):
        self.results.clear()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            found = self.api.search(self.query.text().strip())
        except Exception as e:
            found = []
            QMessageBox.warning(self, "Recherche", str(e))
        finally:
            QApplication.restoreOverrideCursor()
        for r in found:
            item = QListWidgetItem(f"{r['artist']} – {r['title']}  ({fmt_duration(parse_duration(r['duration']))})")
            item.setData(Qt.UserRole, r)
            self.results.addItem(item)
        if found:
            self.results.setCurrentRow(0)

    def accept(self):
        item = self.results.currentItem()
        if item is None:
            return
        self.chosen = item.data(Qt.UserRole)
        super().accept()
