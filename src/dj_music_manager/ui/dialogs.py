from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
                               QPushButton, QVBoxLayout)

from .. import config
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


class SettingsDialog(QDialog):
    def __init__(self, lib: Library, parent=None):
        super().__init__(parent)
        self.lib = lib
        self.setWindowTitle("Réglages")
        self.raw = QLineEdit(lib.get_setting("raw_dir", ""))
        browse = QPushButton("Parcourir…", clicked=self._browse)
        raw_row = QHBoxLayout()
        raw_row.addWidget(self.raw)
        raw_row.addWidget(browse)
        self.quality = _quality_combo(lib.get_setting("default_quality", config.DEFAULT_QUALITY))
        self.key = QLineEdit(echoMode=QLineEdit.Password)
        self.key.setPlaceholderText("inchangée" if config.get_api_key() else "obligatoire")
        form = QFormLayout(self)
        form.addRow("Dossier RAW", raw_row)
        form.addRow("Qualité par défaut", self.quality)
        form.addRow("Clé API", self.key)
        form.addRow(QLabel("La clé est stockée dans le Gestionnaire d'identifiants Windows."))
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Dossier RAW", self.raw.text())
        if d:
            self.raw.setText(d)

    def accept(self):
        raw = self.raw.text().strip()
        if not raw or not Path(raw).is_dir():
            QMessageBox.warning(self, "Réglages", "Choisis un dossier RAW existant.")
            return
        if not self.key.text().strip() and not config.get_api_key():
            QMessageBox.warning(self, "Réglages", "La clé API est obligatoire.")
            return
        self.lib.set_setting("raw_dir", str(Path(raw)))
        self.lib.set_setting("default_quality", self.quality.currentText())
        if self.key.text().strip():
            config.set_api_key(self.key.text().strip())
        super().accept()


class ImportDialog(QDialog):
    def __init__(self, preview: Preview, quality: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import")
        n_new = len(preview.new)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            f"<b>{preview.title}</b> : {plural(len(preview.tracks), 'piste')}<br>"
            f"{len(preview.existing)} déjà dans la bibliothèque, <b>{plural(n_new, 'nouvelle')}</b>"))
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
