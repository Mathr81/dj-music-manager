"""Édition d'une playlist intelligente : nom, « toutes / au moins une », liste de règles."""

from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLineEdit,
                               QMessageBox, QPushButton, QVBoxLayout, QWidget)

from ..quality.badge import BADGES
from ..smart import FIELDS, OPS


class RuleRow(QWidget):
    def __init__(self, tags: list[str], rule: dict | None = None, parent=None):
        super().__init__(parent)
        self.tags = tags
        self.field = QComboBox()
        for key, (label, _) in FIELDS.items():
            self.field.addItem(label, key)
        self.op = QComboBox()
        self.value = QComboBox(editable=True, minimumWidth=180)
        self.remove = QPushButton("–", maximumWidth=30)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        for w in (self.field, self.op, self.value, self.remove):
            layout.addWidget(w)
        self.field.currentIndexChanged.connect(self._field_changed)
        rule = rule or {"field": "tag", "op": "has", "value": ""}
        self.field.setCurrentIndex(self.field.findData(rule["field"]))
        self._field_changed()
        self.op.setCurrentIndex(max(0, self.op.findData(rule["op"])))
        value = rule["value"]
        self.value.setCurrentText(f"{value:g}" if isinstance(value, float) else str(value))  # 120.0 -> 120

    def _field_changed(self):
        key = self.field.currentData()
        self.op.clear()
        for op, label in OPS[FIELDS[key][1]].items():
            self.op.addItem(label, op)
        self.value.clear()  # suggestions selon le champ
        self.value.addItems(self.tags if key == "tag" else BADGES if key == "badge" else [])
        self.value.setCurrentText("")

    def rule(self) -> dict:
        key, value = self.field.currentData(), self.value.currentText().strip()
        if FIELDS[key][1] == "number":
            value = float(value.replace(",", "."))  # ValueError si ce n'est pas un nombre
        elif not value:
            raise ValueError("valeur vide")
        return {"field": key, "op": self.op.currentData(), "value": value}


class SmartPlaylistDialog(QDialog):
    def __init__(self, tags: list[str], name: str = "", rules: dict | None = None, parent=None):
        super().__init__(parent)
        self.tags = tags
        self.setWindowTitle("Playlist intelligente")
        self.name_edit = QLineEdit(name)
        self.match = QComboBox()
        self.match.addItem("toutes les règles", "all")
        self.match.addItem("au moins une règle", "any")
        if rules:
            self.match.setCurrentIndex(self.match.findData(rules.get("match", "all")))
        self.rows_layout = QVBoxLayout()
        form = QFormLayout()
        form.addRow("Nom", self.name_edit)
        form.addRow("Les sons qui respectent", self.match)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(self.rows_layout)
        layout.addWidget(QPushButton("+ Ajouter une règle", clicked=lambda: self._add_row()))
        layout.addWidget(buttons)
        for rule in (rules or {}).get("rules", []) or [None]:
            self._add_row(rule)
        self.resize(560, 0)

    def _add_row(self, rule: dict | None = None):
        row = RuleRow(self.tags, rule, self)
        row.remove.clicked.connect(lambda: (self.rows_layout.removeWidget(row), row.deleteLater()))
        self.rows_layout.addWidget(row)

    def _rows(self) -> list[RuleRow]:
        return [self.rows_layout.itemAt(i).widget() for i in range(self.rows_layout.count())]

    def name(self) -> str:
        return self.name_edit.text().strip()

    def rules(self) -> dict:
        return {"match": self.match.currentData(), "rules": [r.rule() for r in self._rows()]}

    def accept(self):
        if not self.name():
            QMessageBox.warning(self, "Playlist intelligente", "Donne un nom à la playlist.")
            return
        try:
            if not self.rules()["rules"]:
                raise ValueError
        except ValueError:
            QMessageBox.warning(self, "Playlist intelligente",
                                "Chaque règle doit avoir une valeur (un nombre pour BPM et durée).")
            return
        super().accept()
