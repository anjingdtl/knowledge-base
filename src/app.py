"""应用初始化"""
import sys
import logging
from pathlib import Path
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPixmap

from src.version import APP_NAME, VERSION
from src.utils.config import Config

logger = logging.getLogger(__name__)

# Windows 任务栏需要设置 AppUserModelID 才能正确显示自定义图标
if sys.platform == "win32":
    import ctypes
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ShineHe.KnowledgeBase")

ICON_DIR = Path(__file__).resolve().parent.parent / "icon"


def _load_app_icon() -> QIcon:
    """加载应用图标，优先 ICO，备用 PNG，多尺寸确保任务栏清晰"""
    ico_path = ICON_DIR / "knolege.ico"
    png_path = ICON_DIR / "knolege.png"

    # 尝试加载 ICO
    if ico_path.exists():
        icon = QIcon(str(ico_path))
        if not icon.isNull() and icon.availableSizes():
            return icon
        logger.warning("ICO 图标加载失败，尝试 PNG")

    # 备用：从 PNG 手动构建多尺寸图标
    if png_path.exists():
        icon = QIcon()
        for size in (16, 24, 32, 48, 64, 128, 256):
            pixmap = QPixmap(str(png_path))
            if not pixmap.isNull():
                icon.addPixmap(pixmap.scaled(
                    size, size, Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                ))
        if not icon.isNull():
            return icon

    return QIcon()


class KnowledgeBaseApp:
    def __init__(self, argv):
        self.app = QApplication(argv)
        self.app.setApplicationName(APP_NAME)
        self.app.setStyle("Fusion")
        self.app.setWindowIcon(_load_app_icon())
        self._apply_theme()
        from src.gui.main_window import MainWindow
        self.window = MainWindow()

    def _apply_theme(self):
        from src.gui.theme import apply
        apply(self.app)

    def run(self):
        self.window.show()
        return self.app.exec()
