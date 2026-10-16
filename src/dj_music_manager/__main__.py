import sys
from pathlib import Path

from PySide6.QtCore import QLibraryInfo, QTranslator
from PySide6.QtWidgets import QApplication

from . import config
from .library import Library
from .ui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("DJ Music Manager")
    translator = QTranslator(app)  # boutons standard Qt en français (OK/Annuler…)
    if translator.load("qtbase_fr", QLibraryInfo.path(QLibraryInfo.TranslationsPath)):
        app.installTranslator(translator)
    lib = Library(config.data_dir() / "library.db")
    lib.recover_jobs()
    raw = lib.get_setting("raw_dir")
    if raw and Path(raw).is_dir():
        for part in Path(raw).glob("*.part"):  # téléchargements interrompus
            part.unlink(missing_ok=True)
    window = MainWindow(lib)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
