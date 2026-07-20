"""主窗口"""
import ctypes
import ctypes.wintypes
import logging
import sqlite3
import sys
import time

from PySide6.QtCore import QPoint, QSettings, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QCursor
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from src.gui.icons import set_named_icon
from src.services.db import Database
from src.services.llm import register_llm_status_callback, unregister_llm_status_callback
from src.version import VERSION

logger = logging.getLogger(__name__)


class DatabaseInitError(Exception):
    pass


class MCPStartupWorker(QThread):
    """Run an optional database migration and MCP startup outside the GUI thread."""

    completed = Signal(str, bool)

    def __init__(
        self,
        migration_required: bool,
        *,
        readiness_timeout: float = 8.0,
        readiness_poll_interval: float = 0.25,
    ):
        super().__init__()
        self._migration_required = migration_required
        self._readiness_timeout = max(0.0, readiness_timeout)
        self._readiness_poll_interval = max(0.0, readiness_poll_interval)

    def _wait_for_mcp_available(self, is_mcp_available) -> bool:
        """Poll availability for a bounded period after a successful launch."""
        deadline = time.monotonic() + self._readiness_timeout
        while True:
            if is_mcp_available():
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            poll_interval = self._readiness_poll_interval or 0.01
            time.sleep(min(poll_interval, remaining))

    def run(self) -> None:
        messages: list[str] = []
        try:
            from src.services.mcp_heartbeat import is_mcp_port_available
            from src.services.mcp_launcher import (
                is_start_pending_message,
                is_start_success_message,
                migrate_database_for_mcp,
                start,
            )

            if self._migration_required:
                messages.append(migrate_database_for_mcp())
            launch_message = start()
            messages.append(launch_message)
            launch_pending_or_successful = (
                is_start_success_message(launch_message)
                or is_start_pending_message(launch_message)
            )
            confirmed = launch_pending_or_successful and self._wait_for_mcp_available(
                is_mcp_port_available
            )
            if launch_pending_or_successful and not confirmed:
                messages.append("MCP 服务尚未确认可用，已恢复离线状态。")
            self.completed.emit(
                "\n".join(messages),
                confirmed,
            )
        except Exception as exc:  # noqa: BLE001 - propagate worker errors to the GUI
            logger.exception("MCP startup worker failed")
            self.completed.emit(f"MCP 启动失败：{exc}", False)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self._mcp_start_worker: MCPStartupWorker | None = None
        self.setWindowTitle(f"ShineHeKnowledge v{VERSION}")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        self.setMinimumSize(1100, 700)
        self._drag_start_pos = QPoint()
        self._drag_window_pos = QPoint()
        self._restore_geometry()
        self._init_database()
        self._setup_ui()

    def _restore_geometry(self):
        """从 QSettings 恢复窗口位置和大小"""
        settings = QSettings("ShineHeKnowledge", "MainWindow")
        geo = settings.value("geometry")
        if geo:
            self.restoreGeometry(geo)
        else:
            self.resize(1280, 800)

    def closeEvent(self, event):
        """关闭窗口时保存布局状态"""
        worker = getattr(self, "_mcp_start_worker", None)
        if worker is not None and worker.isRunning():
            QMessageBox.information(
                self,
                "MCP 正在启动",
                "数据库迁移或 MCP 启动仍在进行。请等待完成后再关闭窗口。",
            )
            event.ignore()
            return
        settings = QSettings("ShineHeKnowledge", "MainWindow")
        settings.setValue("geometry", self.saveGeometry())
        if hasattr(self, 'knowledge_view'):
            self.knowledge_view._save_column_widths()
        if hasattr(self, '_on_llm_status'):
            unregister_llm_status_callback(self._on_llm_status)
        super().closeEvent(event)

    def _init_database(self):
        try:
            Database.connect()
        except (sqlite3.Error, OSError) as exc:
            logger.exception("数据库连接失败")
            QMessageBox.critical(
                self,
                "数据库错误",
                f"无法连接数据库，请检查数据文件是否损坏或被占用。\n\n{exc}",
            )
            raise DatabaseInitError(str(exc))

    def _setup_ui(self):
        root = QWidget()
        root.setObjectName("contentRoot")
        self.setCentralWidget(root)
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        root_layout.addWidget(self._build_title_bar())

        central = QWidget()
        central.setObjectName("mainContent")
        root_layout.addWidget(central, 1)

        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 延迟导入视图
        from src.gui.catalog_view import CatalogView
        from src.gui.chat_view import ChatView
        from src.gui.graph_view import GraphView
        from src.gui.knowledge_view import KnowledgeView
        from src.gui.llm_indicator import LLMIndicator
        from src.gui.maintenance_view import MaintenanceView
        from src.gui.trash_view import TrashView
        from src.gui.wiki_view import WikiView

        # ---- 侧边栏 ----
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(0, 28, 0, 20)
        sidebar_layout.setSpacing(6)

        # 品牌标题（渐变文字）
        brand = QLabel("ShineHeKnowledge")
        brand.setObjectName("brandLabel")
        brand.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
        brand.setFixedHeight(36)
        sidebar_layout.addWidget(brand)

        # 品牌 slogan
        brand_slogan = QLabel("ShineHe Knowledge Engine")
        brand_slogan.setObjectName("brandSlogan")
        brand_slogan.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
        sidebar_layout.addWidget(brand_slogan)

        # 品牌分割线（渐变色条）
        brand_line = QFrame()
        brand_line.setObjectName("brandLine")
        brand_line.setFixedHeight(2)
        brand_line.setContentsMargins(28, 0, 28, 0)
        sidebar_layout.addWidget(brand_line)
        sidebar_layout.addSpacing(16)

        def nav_button(text: str, icon_key: str, index: int) -> QPushButton:
            button = QPushButton(text)
            button.setObjectName("navPrimary")
            button.setCheckable(True)
            button.setToolTip(text)
            set_named_icon(button, icon_key, "sidebar_text", 16)
            button.clicked.connect(lambda: self._switch_page(index))
            return button

        self.btn_knowledge = nav_button("知识库", "knowledge", 0)
        self.btn_knowledge.setChecked(True)
        self.btn_chat = nav_button("智能问答", "chat", 1)
        self.btn_catalog = nav_button("知识目录", "catalog", 2)
        self.btn_wiki = nav_button("知识 Wiki", "wiki", 3)
        self.btn_graph = nav_button("知识图谱", "graph", 4)
        self.btn_trash = nav_button("回收站", "trash", 5)
        self.btn_maintenance = nav_button("维护中心", "maintenance", 6)

        sidebar_layout.addWidget(self.btn_knowledge)
        sidebar_layout.addWidget(self.btn_chat)
        sidebar_layout.addWidget(self.btn_catalog)
        sidebar_layout.addWidget(self.btn_wiki)
        sidebar_layout.addWidget(self.btn_graph)
        sidebar_layout.addWidget(self.btn_trash)
        sidebar_layout.addWidget(self.btn_maintenance)
        sidebar_layout.addStretch()
        sidebar_layout.addSpacing(16)

        # 状态区分隔线
        status_sep = QFrame()
        status_sep.setObjectName("statusSep")
        status_sep.setFixedHeight(1)
        status_sep.setContentsMargins(20, 0, 20, 0)
        sidebar_layout.addWidget(status_sep)
        sidebar_layout.addSpacing(8)

        # 状态灯区域（MCP + LLM 统一风格）
        self.mcp_light = QLabel("MCP 离线")
        self.mcp_light.setObjectName("mcpLight")
        self.mcp_light.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
        self.mcp_light.setProperty("status", "offline")
        sidebar_layout.addWidget(self.mcp_light)

        # MCP 一键启动/停止按钮
        self.btn_mcp_toggle = QPushButton("启动 MCP")
        self.btn_mcp_toggle.setObjectName("mcpToggle")
        self.btn_mcp_toggle.setCheckable(True)
        self.btn_mcp_toggle.setToolTip("一键启动/停止 MCP Server (streamable-http :9000)")
        set_named_icon(self.btn_mcp_toggle, "mcp", "text_dim", 14)
        self.btn_mcp_toggle.clicked.connect(self._toggle_mcp)
        sidebar_layout.addWidget(self.btn_mcp_toggle)

        sidebar_layout.addSpacing(4)

        self.llm_indicator = LLMIndicator()
        sidebar_layout.addWidget(self.llm_indicator)

        sidebar_layout.addSpacing(8)

        btn_settings = QPushButton("设置")
        btn_settings.setToolTip("设置")
        set_named_icon(btn_settings, "settings", "sidebar_text", 15)
        btn_settings.clicked.connect(self._open_settings)
        sidebar_layout.addWidget(btn_settings)

        # 底部版本号
        version_label = QLabel(f"v{VERSION}")
        version_label.setObjectName("versionLabel")
        version_label.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
        sidebar_layout.addWidget(version_label)

        layout.addWidget(sidebar)

        # ---- 内容区 ----
        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)

        # 仅首页 View 同步创建，其余按需懒加载
        self.knowledge_view = KnowledgeView()
        self.stack.addWidget(self.knowledge_view)

        # 懒加载占位：先加空 QWidget 占位，切换时才创建真正 View
        self._lazy_views: dict[int, tuple[type, dict, QWidget]] = {}
        for idx, (name, cls, kwargs) in enumerate([
            (1, ChatView, {"llm_indicator": self.llm_indicator}),
            (2, CatalogView, {"llm_indicator": self.llm_indicator}),
            (3, WikiView, {}),
            (4, GraphView, {"llm_indicator": self.llm_indicator}),
            (5, TrashView, {}),
            (6, MaintenanceView, {}),
        ], start=1):
            placeholder = QWidget()
            self.stack.addWidget(placeholder)
            self._lazy_views[idx] = (cls, kwargs, placeholder)

        # 注册 LLM 状态回调
        register_llm_status_callback(self._on_llm_status)

        # 状态栏更新防抖定时器 + count 缓存必须在 _update_status() 调用前就绪
        self._status_bar_dirty = False
        self._status_bar_timer = QTimer(self)
        self._status_bar_timer.setSingleShot(True)
        self._status_bar_timer.timeout.connect(self._flush_status_bar)
        # 知识条目数缓存 — 5s 内复用，避免每次 status bar 更新都重查 DB
        self._cached_count: int | None = None
        self._cached_count_ts: float = 0.0

        # 状态栏
        self.statusBar().showMessage("就绪")
        # 延迟 200ms 再更新状态栏，让窗口先渲染出来
        QTimer.singleShot(200, self._update_status)

        # MCP 心跳轮询 — 5s 一次（从 3s 放宽），减少端口探测 + DB 计数频率
        self._mcp_timer = QTimer(self)
        self._mcp_timer.timeout.connect(self._check_mcp_status)
        self._mcp_timer.start(5000)

    def _build_title_bar(self) -> QWidget:
        title_bar = QFrame()
        title_bar.setObjectName("titleBar")
        title_bar.setFixedHeight(34)
        title_bar.mousePressEvent = self._title_mouse_press  # type: ignore[method-assign]
        title_bar.mouseMoveEvent = self._title_mouse_move  # type: ignore[method-assign]
        title_bar.mouseDoubleClickEvent = self._title_mouse_double_click  # type: ignore[method-assign]

        row = QHBoxLayout(title_bar)
        row.setContentsMargins(12, 0, 6, 0)
        row.setSpacing(6)

        title = QLabel(f"ShineHeKnowledge v{VERSION}")
        title.setObjectName("windowTitleLabel")
        title.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(title)
        row.addStretch()

        self.btn_window_min = QPushButton("-")
        self.btn_window_min.setObjectName("windowControl")
        self.btn_window_min.setFixedSize(34, 26)
        self.btn_window_min.clicked.connect(self.showMinimized)
        row.addWidget(self.btn_window_min)

        self.btn_window_max = QPushButton("[]")
        self.btn_window_max.setObjectName("windowControl")
        self.btn_window_max.setFixedSize(34, 26)
        self.btn_window_max.clicked.connect(self._toggle_window_maximized)
        row.addWidget(self.btn_window_max)

        self.btn_window_close = QPushButton("x")
        self.btn_window_close.setObjectName("windowClose")
        self.btn_window_close.setFixedSize(34, 26)
        self.btn_window_close.clicked.connect(self.close)
        row.addWidget(self.btn_window_close)

        return title_bar

    def _title_mouse_press(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = event.globalPosition().toPoint()
            self._drag_window_pos = self.frameGeometry().topLeft()
            event.accept()

    def _title_mouse_move(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton and not self.isMaximized():
            delta = event.globalPosition().toPoint() - self._drag_start_pos
            self.move(self._drag_window_pos + delta)
            event.accept()

    def _title_mouse_double_click(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._toggle_window_maximized()
            event.accept()

    def _toggle_window_maximized(self):
        if self.isMaximized():
            self.showNormal()
            self.btn_window_max.setText("[]")
        else:
            self.showMaximized()
            self.btn_window_max.setText("[ ]")

    def nativeEvent(self, eventType, message):
        if sys.platform != "win32" or self.isMaximized():
            return super().nativeEvent(eventType, message)
        if eventType not in ("windows_generic_MSG", "windows_dispatcher_MSG"):
            return super().nativeEvent(eventType, message)

        msg = ctypes.wintypes.MSG.from_address(int(message))
        if msg.message != 0x0084:  # WM_NCHITTEST
            return super().nativeEvent(eventType, message)

        pos = self.mapFromGlobal(QCursor.pos())
        x, y = pos.x(), pos.y()
        w, h = self.width(), self.height()
        margin = 8
        left = x < margin
        right = x > w - margin
        top = y < margin
        bottom = y > h - margin

        if top and left:
            return True, 13  # HTTOPLEFT
        if top and right:
            return True, 14  # HTTOPRIGHT
        if bottom and left:
            return True, 16  # HTBOTTOMLEFT
        if bottom and right:
            return True, 17  # HTBOTTOMRIGHT
        if left:
            return True, 10  # HTLEFT
        if right:
            return True, 11  # HTRIGHT
        if top:
            return True, 12  # HTTOP
        if bottom:
            return True, 15  # HTBOTTOM
        return super().nativeEvent(eventType, message)

    def _switch_page(self, index: int):
        # 懒加载：首次切换到非首页时才创建 View
        if index in self._lazy_views:
            cls, kwargs, placeholder = self._lazy_views.pop(index)
            real_view = cls(**kwargs)
            # 替换 stack 中的 placeholder
            stack_idx = self.stack.indexOf(placeholder)
            self.stack.removeWidget(placeholder)
            placeholder.deleteLater()
            self.stack.insertWidget(stack_idx, real_view)
            # 存为属性，方便后续引用
            attr_names = {1: "chat_view", 2: "catalog_view", 3: "wiki_view", 4: "graph_view", 5: "trash_view", 6: "maintenance_view"}
            setattr(self, attr_names[index], real_view)

        self.stack.setCurrentIndex(index)
        self.btn_knowledge.setChecked(index == 0)
        self.btn_chat.setChecked(index == 1)
        self.btn_catalog.setChecked(index == 2)
        self.btn_wiki.setChecked(index == 3)
        self.btn_graph.setChecked(index == 4)
        self.btn_trash.setChecked(index == 5)
        self.btn_maintenance.setChecked(index == 6)
        # 切换到知识目录时刷新，确保与知识库的标题修改同步
        if index == 2 and hasattr(self, 'catalog_view'):
            self.catalog_view._load_catalog()
        # 切换到回收站时刷新列表
        if index == 5 and hasattr(self, 'trash_view'):
            self.trash_view.refresh()
        # 切换到维护中心时刷新历史会话与忽略列表
        if index == 6 and hasattr(self, 'maintenance_view'):
            self.maintenance_view.refresh_on_show()

    def _open_settings(self):
        from src.gui.settings_dialog import SettingsDialog
        dialog = SettingsDialog(self)
        dialog.exec()

    def _update_status(self):
        # 强制失效缓存（导入/删除后会调用）
        self._cached_count = None
        self._schedule_status_bar()

    def _schedule_status_bar(self):
        """300ms 防抖：短时间内多次状态变化只刷一次 status bar"""
        if self._status_bar_timer.isActive():
            return
        self._status_bar_timer.start(300)

    def _flush_status_bar(self):
        """真正更新 status bar 文本"""
        self._update_status_bar_now()

    def _count_knowledge_cached(self) -> int:
        """5s 缓存的 Database.count_knowledge 调用"""
        import time as _time
        now = _time.monotonic()
        if self._cached_count is None or (now - self._cached_count_ts) > 5.0:
            self._cached_count = Database.count_knowledge()
            self._cached_count_ts = now
        return self._cached_count

    def _update_status_bar_now(self):
        count = self._count_knowledge_cached()
        from src.services.mcp_heartbeat import is_mcp_available
        mcp_online = is_mcp_available()
        llm_status = self.llm_indicator.property("status") or "idle"
        llm_text = "就绪" if llm_status == "idle" else "运行中" if llm_status in ("running", "dim") else "异常"
        mcp_text = "在线" if mcp_online else "离线"
        self.statusBar().showMessage(
            f"{count} 条知识  |  LLM {llm_text}  |  MCP {mcp_text}"
        )

    # 兼容旧调用名：内部走防抖路径
    def _update_status_bar(self):
        self._schedule_status_bar()

    def _on_llm_status(self, status: str, detail: str = ""):
        self.llm_indicator.set_status(status, detail)
        self._update_status_bar()

    def _check_mcp_status(self, *, force: bool = False) -> bool:
        from src.services.mcp_heartbeat import is_mcp_available
        online = is_mcp_available()
        new_status = "online" if online else "offline"
        if force or self.mcp_light.property("status") != new_status:
            self.mcp_light.setProperty("status", new_status)
            self.mcp_light.setText(f"MCP {'活跃' if online else '离线'}")
            # polish() 单次足够：dynamic property 变化后 QSS 选择器自动重算。
            self.mcp_light.style().polish(self.mcp_light)
            # 同步按钮状态
            self.btn_mcp_toggle.blockSignals(True)
            self.btn_mcp_toggle.setChecked(online)
            self.btn_mcp_toggle.setText("停止 MCP" if online else "启动 MCP")
            set_named_icon(self.btn_mcp_toggle, "mcp", "danger" if online else "text_dim", 14)
            self.btn_mcp_toggle.blockSignals(False)
            self._update_status_bar()
        return online

    def _toggle_mcp(self, checked: bool):
        if checked:
            from src.services.mcp_launcher import get_migration_requirement

            try:
                migration_requirement = get_migration_requirement()
            except Exception as exc:  # noqa: BLE001 - keep the toggle recoverable
                logger.exception("MCP migration preflight failed")
                self._set_mcp_toggle_checked(False)
                self.statusBar().showMessage(f"无法检查 MCP 启动条件：{exc}", 8000)
                return

            if migration_requirement:
                reply = QMessageBox.question(
                    self,
                    "需要数据库迁移",
                    f"{migration_requirement}\n\n"
                    "继续将先创建数据库备份，再执行迁移和校验。是否继续？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    self._set_mcp_toggle_checked(False)
                    self.statusBar().showMessage("已取消 MCP 启动", 5000)
                    return

            self._start_mcp_worker(migration_required=bool(migration_requirement))
        else:
            from src.services.mcp_launcher import stop

            msg = stop()
            self.statusBar().showMessage(msg, 5000)
            self.mcp_light.setProperty("status", "offline")
            self.mcp_light.setText("MCP 离线")
            self.mcp_light.style().polish(self.mcp_light)

    def _set_mcp_toggle_checked(self, checked: bool) -> None:
        """Update the toggle without recursively invoking the click handler."""
        self.btn_mcp_toggle.blockSignals(True)
        self.btn_mcp_toggle.setChecked(checked)
        self.btn_mcp_toggle.setText("停止 MCP" if checked else "启动 MCP")
        set_named_icon(self.btn_mcp_toggle, "mcp", "danger" if checked else "text_dim", 14)
        self.btn_mcp_toggle.blockSignals(False)

    def _start_mcp_worker(self, *, migration_required: bool) -> None:
        """Start the potentially slow migration/launch sequence in a QThread."""
        self.setEnabled(False)
        self.btn_mcp_toggle.setEnabled(False)
        self.statusBar().showMessage(
            "正在备份、迁移并启动 MCP…" if migration_required else "正在启动 MCP…"
        )
        worker = MCPStartupWorker(migration_required=migration_required)
        self._mcp_start_worker = worker
        worker.completed.connect(self._on_mcp_startup_completed)
        worker.finished.connect(self._clear_mcp_start_worker)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _on_mcp_startup_completed(self, message: str, _ok: bool) -> None:
        self.setEnabled(True)
        self.btn_mcp_toggle.setEnabled(True)
        self._set_mcp_toggle_checked(_ok)
        self.mcp_light.setProperty("status", "online" if _ok else "offline")
        self.mcp_light.setText(f"MCP {'活跃' if _ok else '离线'}")
        self.mcp_light.style().polish(self.mcp_light)
        self.statusBar().showMessage(message, 8000)

    def _clear_mcp_start_worker(self) -> None:
        """Release the worker only after its QThread has fully stopped."""
        self._mcp_start_worker = None
