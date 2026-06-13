"""应用初始化"""
import logging
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QApplication, QMessageBox

from src.utils.config import Config
from src.version import APP_NAME

logger = logging.getLogger(__name__)

# Windows 任务栏需要设置 AppUserModelID 才能正确显示自定义图标
if sys.platform == "win32":
    import ctypes
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ShineHe.KnowledgeBase")

ICON_DIR = Path(__file__).resolve().parent.parent / "icon"


def _load_app_icon() -> QIcon:
    """加载应用图标，优先 ICO，备用 PNG，多尺寸确保任务栏清晰"""
    ico_path = ICON_DIR / "knowledge.ico"
    png_path = ICON_DIR / "knowledge.png"

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


def _run_setup_wizard(parent=None) -> bool:
    """运行首次启动配置向导，返回 True 表示用户完成了配置"""
    from src.gui.setup_wizard import SetupWizard
    wizard = SetupWizard(parent)
    result = wizard.exec()
    if result == SetupWizard.DialogCode.Accepted:
        from src.utils.first_run import mark_completed
        mark_completed()
        # 可选导入示例知识包
        if wizard.get_import_samples():
            _import_sample_data()
        return True
    else:
        # 用户跳过向导，也标记为已完成（不再打扰）
        from src.utils.first_run import mark_completed
        mark_completed()
        return False


def _import_sample_data():
    """导入示例知识包"""
    try:
        samples_dir = Path(__file__).resolve().parent / "data" / "samples"  # src/data/samples/
        if not samples_dir.exists():
            logger.debug("示例知识包目录不存在，跳过")
            return

        import json
        import uuid
        from datetime import datetime

        from src.services.db import Database
        from src.services.file_parser import parse_file

        now = datetime.now().isoformat()
        count = 0
        for md_file in sorted(samples_dir.glob("*.md")):
            try:
                parsed_list = parse_file(str(md_file))
                if parsed_list and parsed_list[0].content.strip():
                    parsed = parsed_list[0]
                    Database.insert_knowledge({
                        "id": str(uuid.uuid4()),
                        "title": md_file.stem,
                        "content": parsed.content[:5000],
                        "source_type": "file",
                        "source_path": str(md_file),
                        "file_type": "md",
                        "file_size": len(parsed.content.encode("utf-8")),
                        "content_hash": "",
                        "file_created_at": "",
                        "file_modified_at": "",
                        "tags": json.dumps(["示例"], ensure_ascii=False),
                        "version": 1,
                        "created_at": now,
                        "updated_at": now,
                    })
                    count += 1
            except Exception as e:
                logger.warning("导入示例文件 %s 失败: %s", md_file.name, e)

        if count > 0:
            logger.info("已导入 %d 个示例知识条目", count)
    except Exception as exc:
        logger.warning("示例知识包导入失败（不影响使用）: %s", exc)


class KnowledgeBaseApp:
    def __init__(self, argv):
        self.app = QApplication(argv)
        self.app.setApplicationName(APP_NAME)
        self.app.setStyle("Fusion")
        self.app.setWindowIcon(_load_app_icon())
        self._apply_theme()

        # 首次启动检测 — 在主窗口创建前运行向导
        from src.utils.first_run import is_first_run
        self._is_first_run = is_first_run()
        if self._is_first_run:
            _run_setup_wizard(parent=None)
            # 向导可能修改了配置，重新加载
            Config.load()

        from src.gui.main_window import DatabaseInitError, MainWindow
        try:
            self.window = MainWindow()
        except DatabaseInitError as exc:
            QMessageBox.critical(None, "数据库错误", f"无法连接数据库：\n\n{exc}")
            sys.exit(1)
        self.window.setWindowIcon(self.app.windowIcon())

    def _apply_theme(self):
        from src.gui.theme import apply
        apply(self.app)

    def run(self):
        self.window.show()
        return self.app.exec()
