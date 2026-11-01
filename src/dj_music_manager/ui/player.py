"""Lecteur intégré : barre en bas de la fenêtre (pochette, titre, transport, forme d'onde, volume)."""

import threading
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, QRectF, QSettings, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSizePolicy, QSlider, QVBoxLayout, QWidget

from .. import waveform
from .dialogs import fmt_duration
from .icons import icon
from .theme import BORDER, TEXT

COVER = 52  # px
BAND_COLORS = ((40, 110, 255), (255, 160, 50), (245, 245, 245))  # graves, médiums, aigus, du fond vers l'avant
BAND_SCALE = (1.0, 0.7, 0.45)  # chaque couche plus courte que la précédente pour laisser voir le fond


class Waveform(QWidget):
    """Forme d'onde cliquable : partie jouée en couleurs, reste atténué, tête de lecture."""
    seek = Signal(float)  # fraction du morceau, 0..1

    def __init__(self):
        super().__init__(minimumHeight=44, mouseTracking=True, cursor=Qt.PointingHandCursor)
        self.levels = None
        self.progress = 0.0
        self._hover: float | None = None
        self._cache: tuple | None = None  # (taille, image)

    def set_data(self, levels: np.ndarray | None):
        self.levels = levels  # (tranches, 3) : graves, médiums, aigus
        self._cache = None
        self.update()

    def set_progress(self, fraction: float):
        self.progress = min(1.0, max(0.0, fraction))
        self.update()

    def _render(self) -> QPixmap:
        ratio = self.devicePixelRatioF()
        pixmap = QPixmap(self.size() * ratio)
        pixmap.setDevicePixelRatio(ratio)
        pixmap.fill(Qt.transparent)
        p = QPainter(pixmap)
        w, h = self.width(), self.height()
        bars = max(1, w // 3)  # barres de 2 px espacées de 1 px
        per_bar = np.array([chunk.max(axis=0) for chunk in np.array_split(self.levels, bars) if len(chunk)])
        for band, (color, scale) in enumerate(zip(BAND_COLORS, BAND_SCALE)):
            c = QColor(*color)
            for i, level in enumerate(per_bar[:, band]):
                bar = max(1.0, level * scale * (h - 4))
                p.fillRect(QRectF(i * w / bars, (h - bar) / 2, 2, bar), c)
        p.end()
        return pixmap

    def paintEvent(self, event):
        p = QPainter(self)
        w, h = self.width(), self.height()
        x = self.progress * w
        if self.levels is None:  # pas encore calculée : simple ligne de progression
            p.fillRect(QRectF(0, h / 2 - 2, w, 4), QColor(BORDER))
            p.fillRect(QRectF(0, h / 2 - 2, x, 4), self.palette().highlight())
        else:
            if self._cache is None or self._cache[0] != self.size():
                self._cache = (self.size(), self._render())
            image = self._cache[1]
            p.setOpacity(0.35)  # reste à jouer : atténué
            p.drawPixmap(0, 0, image)
            p.setOpacity(1)
            p.setClipRect(QRectF(0, 0, x, h))
            p.drawPixmap(0, 0, image)
            p.setClipping(False)
        p.fillRect(QRectF(x - 1, 0, 2, h), QColor(TEXT))
        if self._hover is not None:
            p.fillRect(QRectF(self._hover * w, 0, 1, h), QColor(255, 255, 255, 90))
        p.end()

    def _fraction(self, event) -> float:
        return min(1.0, max(0.0, event.position().x() / max(1, self.width())))

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.seek.emit(self._fraction(event))

    def mouseMoveEvent(self, event):
        self._hover = self._fraction(event)
        if event.buttons() & Qt.LeftButton:
            self.seek.emit(self._hover)
        self.update()

    def leaveEvent(self, event):
        self._hover = None
        self.update()


class _Loaded(QObject):
    waveform = Signal(str, object)  # chemin, niveaux par bande ou None


class PlayerBar(QWidget):
    next_requested = Signal()
    previous_requested = Signal()

    def __init__(self, ffmpeg_lookup=None, parent=None):
        """ffmpeg_lookup() -> chemin de ffmpeg (FileNotFoundError si absent) : sans lui, pas de forme d'onde."""
        super().__init__(parent, objectName="player")
        self.ffmpeg_lookup = ffmpeg_lookup
        self._waves: dict[str, tuple] = {}
        self._loaded = _Loaded(self)
        self._loaded.waveform.connect(self._on_waveform)
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
        self.wave = Waveform()
        self.wave.seek.connect(lambda f: self.player.setPosition(int(f * self.player.duration())))
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
        layout.addWidget(self.wave, 3)
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
        self._load_waveform(path)

    def _load_waveform(self, path: Path):
        key = str(path)
        self.wave.set_data(self._waves.get(key))
        if key in self._waves or self.ffmpeg_lookup is None:
            return

        def work():
            try:
                self._loaded.waveform.emit(key, waveform.compute(path, self.ffmpeg_lookup()))
            except Exception:
                self._loaded.waveform.emit(key, None)  # pas de forme d'onde : la ligne de progression reste

        threading.Thread(target=work, daemon=True).start()

    def _on_waveform(self, key: str, data):
        if data is None:
            return
        self._waves[key] = data
        if self.player.source().toLocalFile() == QUrl.fromLocalFile(key).toLocalFile():
            self.wave.set_data(data)

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
        self.total.setText(fmt_duration(ms / 1000))

    def _on_position(self, ms: int):
        self.wave.set_progress(ms / (self.player.duration() or 1))
        self.elapsed.setText(fmt_duration(ms / 1000))

    def _on_state(self, state):
        playing = state == QMediaPlayer.PlayingState
        self.play_btn.setIcon(icon("pause" if playing else "play", "#000"))
