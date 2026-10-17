"""Export des playlists vers rekordbox, Serato, VirtualDJ, Mixxx…"""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QGroupBox, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout)

from ..library import Library

FORMATS = {
    "rekordbox": "rekordbox (rekordbox.xml)",
    "serato": "Serato (crates)",
    "m3u8": "Playlists M3U8 (VirtualDJ, Mixxx, Traktor, Engine DJ…)",
}
HOW_TO = {
    "rekordbox": "rekordbox : Préférences > Avancé > Base de données > « rekordbox xml » : choisis {xml}. "
                 "Les playlists apparaissent dans « rekordbox xml » (barre de gauche) : clic droit > Importer.",
    "serato": "Serato : les crates apparaissent sous « dj-music-manager » au prochain démarrage.",
    "m3u8": "VirtualDJ : copie les .m3u8 dans Documents\\VirtualDJ\\Playlists (ou glisse-les dans le navigateur). "
            "Mixxx : Playlists > clic droit > Importer une playlist.",
}


def _folder_row(edit: QLineEdit, parent) -> QHBoxLayout:
    row = QHBoxLayout()
    row.addWidget(edit)

    def browse():
        d = QFileDialog.getExistingDirectory(parent, "Dossier", edit.text())
        if d:
            edit.setText(d)

    row.addWidget(QPushButton("Parcourir…", clicked=browse))
    return row


class ExportDialog(QDialog):
    def __init__(self, lib: Library, selected: int | None = None, parent=None):
        super().__init__(parent)
        self.lib = lib
        self.setWindowTitle("Exporter pour logiciel DJ")
        raw_parent = Path(lib.get_setting("raw_dir", ".")).parent

        self.playlists = QListWidget()
        for p in lib.playlists():
            item = QListWidgetItem(p["name"])
            item.setData(Qt.UserRole, p["id"])
            item.setCheckState(Qt.Checked if p["id"] == selected else Qt.Unchecked)
            self.playlists.addItem(item)

        saved = (lib.get_setting("export_formats") or "rekordbox").split(",")
        self.formats = {key: QCheckBox(label, checked=key in saved) for key, label in FORMATS.items()}
        formats_box = QGroupBox("Formats")
        formats_layout = QVBoxLayout(formats_box)
        for box in self.formats.values():
            formats_layout.addWidget(box)

        self.export_dir = QLineEdit(lib.get_setting("export_dir") or str(raw_parent / "Export DJ"))
        self.convert = QCheckBox("Convertir les FLAC en AIFF (anciens CDJ)",
                                 checked=lib.get_setting("export_convert_flac") == "1")
        self.aiff_dir = QLineEdit(lib.get_setting("aiff_dir") or str(raw_parent / "RAW_AIFF"))

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Playlists à exporter :"))
        layout.addWidget(self.playlists)
        layout.addWidget(formats_box)
        layout.addWidget(QLabel("Dossier d'export (rekordbox.xml, M3U8) :"))
        layout.addLayout(_folder_row(self.export_dir, self))
        layout.addWidget(self.convert)
        layout.addWidget(QLabel("Dossier des AIFF convertis (jamais dans RAW) :"))
        layout.addLayout(_folder_row(self.aiff_dir, self))
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Exporter")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(520, 560)

    def playlist_ids(self) -> list[int]:
        items = (self.playlists.item(i) for i in range(self.playlists.count()))
        return [it.data(Qt.UserRole) for it in items if it.checkState() == Qt.Checked]

    def chosen_formats(self) -> list[str]:
        return [key for key, box in self.formats.items() if box.isChecked()]

    def accept(self):
        raw = Path(self.lib.get_setting("raw_dir", ".")).resolve()
        if not self.playlist_ids() or not self.chosen_formats():
            QMessageBox.warning(self, "Export", "Coche au moins une playlist et un format.")
            return
        if self.convert.isChecked() and Path(self.aiff_dir.text()).resolve().is_relative_to(raw):
            QMessageBox.warning(self, "Export", "Le dossier des AIFF doit être en dehors de RAW.")
            return
        self.lib.set_setting("export_formats", ",".join(self.chosen_formats()))
        self.lib.set_setting("export_dir", self.export_dir.text().strip())
        self.lib.set_setting("export_convert_flac", "1" if self.convert.isChecked() else "0")
        self.lib.set_setting("aiff_dir", self.aiff_dir.text().strip())
        super().accept()
