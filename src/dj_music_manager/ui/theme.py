"""Thème sombre façon logiciel DJ : palette Fusion + feuille de style."""

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

BG, PANEL, FIELD, BORDER = "#15171c", "#1d2027", "#252932", "#323744"
TEXT, MUTED, ACCENT = "#e6e8ee", "#8b91a0", "#34c3ff"

QSS = f"""
* {{ font-size: 10pt; }}
QMainWindow, QDialog {{ background: {BG}; }}
QToolTip {{ background: {FIELD}; color: {TEXT}; border: 1px solid {BORDER}; padding: 4px; }}
QLineEdit, QSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{
    background: {FIELD}; border: 1px solid {BORDER}; border-radius: 6px; padding: 5px 8px;
    selection-background-color: {ACCENT}; selection-color: #000;
}}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
QLineEdit#link {{ font-size: 11pt; padding: 8px 12px; }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{ background: {PANEL}; border: 1px solid {BORDER}; selection-background-color: {ACCENT};
    selection-color: #000; }}
QPushButton {{ background: {FIELD}; border: 1px solid {BORDER}; border-radius: 6px; padding: 6px 14px; }}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton:pressed {{ background: {BORDER}; }}
QPushButton:disabled {{ color: {MUTED}; }}
QPushButton#primary {{ background: {ACCENT}; color: #000; border: none; font-weight: bold; }}
QPushButton#primary:hover {{ background: #6dd5ff; }}
QPushButton#primary:disabled {{ background: {BORDER}; color: {MUTED}; }}
QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: 6px; top: -1px; background: {PANEL}; }}
QTabBar::tab {{ background: transparent; color: {MUTED}; padding: 8px 18px; border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom-color: {ACCENT}; }}
QTabBar::tab:hover {{ color: {TEXT}; }}
QTableView, QTableWidget, QListWidget {{
    background: {PANEL}; alternate-background-color: #21252d; border: 1px solid {BORDER}; border-radius: 6px;
    gridline-color: transparent; selection-background-color: #1f4d66; selection-color: {TEXT}; outline: 0;
}}
QListWidget::item {{ padding: 6px 8px; border-radius: 4px; }}
QListWidget::item:selected {{ background: #1f4d66; }}
QListWidget::item:hover:!selected {{ background: {FIELD}; }}
QHeaderView::section {{ background: {PANEL}; color: {MUTED}; border: none; border-bottom: 1px solid {BORDER};
    padding: 6px; font-weight: bold; }}
QHeaderView::section:hover {{ color: {TEXT}; }}
QMenuBar {{ background: {BG}; }}
QMenuBar::item {{ padding: 5px 10px; border-radius: 4px; }}
QMenuBar::item:selected {{ background: {FIELD}; }}
QMenu {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 6px; padding: 4px; }}
QMenu::item {{ padding: 6px 28px 6px 14px; border-radius: 4px; }}
QMenu::item:selected {{ background: #1f4d66; }}
QMenu::item:disabled {{ color: {MUTED}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 8px; }}
QProgressBar {{ background: {FIELD}; border: none; border-radius: 4px; height: 8px; text-align: center;
    color: {TEXT}; font-size: 8pt; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 4px; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle {{ background: {BORDER}; border-radius: 4px; min-height: 30px; min-width: 30px; }}
QScrollBar::handle:hover {{ background: #4a5163; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ background: none;
    border: none; width: 0; height: 0; }}
QSplitter::handle {{ background: transparent; width: 6px; }}
QStatusBar {{ background: {BG}; color: {MUTED}; }}
QStatusBar QLabel {{ color: {MUTED}; padding: 0 8px; }}
QLabel#heading {{ color: {MUTED}; font-weight: bold; font-size: 9pt; padding: 4px 2px; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 14px; height: 14px; }}
"""


def apply(app: QApplication):
    app.setStyle("Fusion")
    p = QPalette()
    for role, color in ((QPalette.Window, BG), (QPalette.WindowText, TEXT), (QPalette.Base, PANEL),
                        (QPalette.AlternateBase, FIELD), (QPalette.Text, TEXT), (QPalette.Button, FIELD),
                        (QPalette.ButtonText, TEXT), (QPalette.Highlight, ACCENT), (QPalette.HighlightedText, "#000"),
                        (QPalette.ToolTipBase, FIELD), (QPalette.ToolTipText, TEXT), (QPalette.PlaceholderText, MUTED),
                        (QPalette.Link, ACCENT)):
        p.setColor(role, QColor(color))
    p.setColor(QPalette.Disabled, QPalette.Text, QColor(MUTED))
    p.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(MUTED))
    app.setPalette(p)
    app.setStyleSheet(QSS)
