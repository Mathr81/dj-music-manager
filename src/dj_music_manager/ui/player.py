"""Lecteur intégré : barre en bas de la fenêtre (pochette, titre, transport, position, volume)."""

from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QUrl, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSizePolicy, QSlider, QVBoxLayout, QWidget

from .dialogs import fmt_duration
from .icons import icon

COVER = 52  # px


class PlayerBar(QWidget):
    next_requested = Signal()
    previous_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent, objectName="player")
        self.setAttribute(Qt.WA_StyledBackground)  # fond et bordure de la feuille de style
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.audio = QAudioOutput(self)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio)

        self.cover = QLabel(objectName="cover")
        self.cover.setFixedSize(COVER, COVER)
        self.title = QLabel("Aucun son en lecture", objectName="playerTitle")
        self.artist = QLabel("Double-clic ou Entrée sur un son pour l'écouter", objectName="hint")
        text = QVBoxLayout()
        text.setSpacing(2)
        text.addStretch()
        text.addWidget(self.title)
        text.addWidget(self.artist)
        text.addStretch()

        self.prev_btn = QPushButton(icon("skip-back"), "", objectName="icon", toolTip="Précédent (Ctrl+←)",
                                    clicked=self.previous_requested.emit)
        self.play_btn = QPushButton(icon("play", "#000"), "", objectName="playButton",
                                    toolTip="Lecture / pause (Espace)", clicked=self.toggle)
        self.next_btn = QPushButton(icon("skip-forward"), "", objectName="icon", toolTip="Suivant (Ctrl+→)",
                                    clicked=self.next_requested.emit)
        self.elapsed, self.total = QLabel("0:00", objectName="hint"), QLabel("0:00", objectName="hint")
        self.seek = QSlider(Qt.Horizontal)
        self.seek.sliderMoved.connect(self.player.setPosition)
        self.volume = QSlider(Qt.Horizontal, maximumWidth=100, toolTip="Volume")
        self.volume.setRange(0, 100)
        self.volume.valueChanged.connect(self._set_volume)
        self.volume.setValue(int(QSettings().value("player/volume", 80)))

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 14, 6)
        layout.addWidget(self.cover)
        layout.addLayout(text, 2)
        for w in (self.prev_btn, self.play_btn, self.next_btn):
            layout.addWidget(w)
        layout.addSpacing(8)
        layout.addWidget(self.elapsed)
        layout.addWidget(self.seek, 3)
        layout.addWidget(self.total)
        layout.addSpacing(12)
        volume_icon = QLabel()
        volume_icon.setPixmap(icon("volume").pixmap(16, 16))
        layout.addWidget(volume_icon)
        layout.addWidget(self.volume)
        self._set_cover(None)

        self.player.durationChanged.connect(self._on_duration)
        self.player.positionChanged.connect(self._on_position)
        self.player.playbackStateChanged.connect(self._on_state)
        self.player.mediaStatusChanged.connect(
            lambda s: s == QMediaPlayer.EndOfMedia and self.next_requested.emit())
        self.player.errorOccurred.connect(lambda _, message: self.artist.setText(f"Lecture impossible : {message}"))

    def play(self, path: Path, artist: str, title: str, cover: QPixmap | None):
        self.title.setText(title or path.stem)
        self.artist.setText(artist or "")
        self._set_cover(cover)
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        self.player.play()

    def toggle(self):
        if self.player.source().isEmpty():
            self.next_requested.emit()  # rien de chargé : on lance la sélection
        elif self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def stop(self):
        self.player.stop()

    def _set_cover(self, pixmap: QPixmap | None):
        if pixmap is None:
            self.cover.setPixmap(icon("music").pixmap(28, 28))
            self.cover.setAlignment(Qt.AlignCenter)
        else:
            self.cover.setPixmap(pixmap.scaled(COVER, COVER, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation))

    def _set_volume(self, value: int):
        self.audio.setVolume(value / 100)
        QSettings().setValue("player/volume", value)

    def _on_duration(self, ms: int):
        self.seek.setRange(0, ms)
        self.total.setText(fmt_duration(ms / 1000))

    def _on_position(self, ms: int):
        if not self.seek.isSliderDown():
            self.seek.setValue(ms)
        self.elapsed.setText(fmt_duration(ms / 1000))

    def _on_state(self, state):
        playing = state == QMediaPlayer.PlayingState
        self.play_btn.setIcon(icon("pause" if playing else "play", "#000"))
