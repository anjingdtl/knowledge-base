"""应用初始化"""
import sys
from pathlib import Path
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon

from src.version import APP_NAME, VERSION
from src.utils.config import Config


class KnowledgeBaseApp:
    def __init__(self, argv):
        self.app = QApplication(argv)
        self.app.setApplicationName(APP_NAME)
        self.app.setStyle("Fusion")
        icon_path = Path(__file__).resolve().parent.parent / "icon" / "knolege.ico"
        if icon_path.exists():
            self.app.setWindowIcon(QIcon(str(icon_path)))
        self._apply_theme()
        from src.gui.main_window import MainWindow
        self.window = MainWindow()

    def _apply_theme(self):
        from src.gui.theme import apply
        apply(self.app)

    def run(self):
        self.window.show()
        return self.app.exec()
