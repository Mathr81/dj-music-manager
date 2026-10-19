"""Dessine l'icône de l'app (vinyle sur fond nuit, anneau néon) : uv run python packaging/make_icon.py
Écrit src/dj_music_manager/icon.ico (16 à 256 px, images PNG dans le conteneur ICO)."""

import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QConicalGradient, QGuiApplication, QImage, QLinearGradient, QPainter, QPen

SIZES = (16, 24, 32, 48, 64, 128, 256)
OUT = Path(__file__).resolve().parents[1] / "src" / "dj_music_manager" / "icon.ico"


def render(size: int) -> QImage:
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(size / 256, size / 256)  # dessin en coordonnées 256 px
    c = QPointF(128, 128)

    bg = QLinearGradient(0, 0, 256, 256)
    bg.setColorAt(0, QColor("#2E1065"))
    bg.setColorAt(1, QColor("#0B0B16"))
    p.setPen(Qt.NoPen)
    p.setBrush(bg)
    p.drawRoundedRect(QRectF(0, 0, 256, 256), 56, 56)

    p.setBrush(QColor("#0A0A0F"))  # disque
    p.drawEllipse(c, 98, 98)
    if size >= 48:  # sillons, invisibles en petit
        p.setBrush(Qt.NoBrush)
        for r, alpha in ((86, 40), (74, 28), (62, 40), (52, 28)):
            p.setPen(QPen(QColor(255, 255, 255, alpha), 1.6))
            p.drawEllipse(c, r, r)
        shine = QConicalGradient(c, 45)  # reflet de lumière sur le vinyle
        shine.setColorAt(0, QColor(255, 255, 255, 0))
        shine.setColorAt(0.06, QColor(255, 255, 255, 46))
        shine.setColorAt(0.12, QColor(255, 255, 255, 0))
        shine.setColorAt(0.5, QColor(255, 255, 255, 0))
        shine.setColorAt(0.56, QColor(255, 255, 255, 30))
        shine.setColorAt(0.62, QColor(255, 255, 255, 0))
        shine.setColorAt(1, QColor(255, 255, 255, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(shine)
        p.drawEllipse(c, 96, 96)

    neon = QConicalGradient(c, 90)  # anneau néon façon jog wheel
    for at, color in ((0, "#22D3EE"), (0.35, "#A855F7"), (0.7, "#EC4899"), (1, "#22D3EE")):
        neon.setColorAt(at, QColor(color))
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(neon, 16 if size >= 32 else 22, Qt.SolidLine, Qt.RoundCap))
    p.drawEllipse(c, 104, 104)

    label = QLinearGradient(96, 96, 160, 160)
    label.setColorAt(0, QColor("#F472B6"))
    label.setColorAt(1, QColor("#FB923C"))
    p.setPen(Qt.NoPen)
    p.setBrush(label)
    p.drawEllipse(c, 36 if size >= 32 else 44, 36 if size >= 32 else 44)
    p.setBrush(QColor("#0A0A0F"))
    p.drawEllipse(c, 7, 7)
    p.end()
    return img


def png_bytes(img: QImage) -> bytes:
    buf = QBuffer()
    buf.open(QBuffer.WriteOnly)
    img.save(buf, "PNG")
    return bytes(buf.data())


def ico(images: list[bytes]) -> bytes:
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, data = b"", b""
    for size, png in zip(SIZES, images):
        dim = 0 if size == 256 else size  # 0 = 256 dans le format ICO
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(png), offset + len(data))
        data += png
    return header + entries + data


if __name__ == "__main__":
    app = QGuiApplication(sys.argv)
    images = [render(s) for s in SIZES]
    OUT.write_bytes(ico([png_bytes(i) for i in images]))
    if len(sys.argv) > 1:  # aperçu : python make_icon.py apercu.png
        sheet = QImage(256 + 16 + 128 + 16 + 48 + 16 + 32 + 16 + 16, 256, QImage.Format_ARGB32)
        sheet.fill(QColor("#F3F4F6"))
        p = QPainter(sheet)
        x = 0
        for img in images[::-1]:
            if img.width() in (256, 128, 48, 32, 16):
                p.drawImage(x, 0, img)
                x += img.width() + 16
        p.end()
        sheet.save(sys.argv[1])
    print(OUT)
