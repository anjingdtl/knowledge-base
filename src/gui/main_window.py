"""主窗口"""
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QStackedWidget, QLabel, QGraphicsDropShadowEffect, QFrame,
)
from PySide6.QtCore import Qt, QTimer, QSettings
from PySide6.QtGui import QIcon, QColor
from pathlib import Path

from src.gui.icons import set_named_icon
from src.services.db import Database
from src.services.llm import register_llm_status_callback
from src.version import VERSION

ICON_PATH = Path(__file__).resolve().parent.parent.parent / "icon" / "knolege.ico"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"泰坦知识库 v{VERSION}")
        self.setMinimumSize(1100, 700)
        self._restore_geometry()
        if ICON_PATH.exists():
            self.setWindowIcon(QIcon(str(ICON_PATH)))
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
        settings = QSettings("ShineHeKnowledge", "MainWindow")
        settings.setValue("geometry", self.saveGeometry())
        # 保存知识库表格列宽
        if hasattr(self, 'knowledge_view'):
            self.knowledge_view._save_column_widths()
        super().closeEvent(event)

    def _init_database(self):
        Database.connect()

    def _setup_ui(self):
        central = QWidget()
        central.setObjectName("contentRoot")
        self.setCentralWidget(central)
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 延迟导入视图
        from src.gui.llm_indicator import LLMIndicator
        from src.gui.knowledge_view import KnowledgeView
        from src.gui.chat_view import ChatView
        from src.gui.catalog_view import CatalogView
        from src.gui.wiki_view import WikiView
        from src.gui.graph_view import GraphView

        # ---- 侧边栏 ----
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(0, 24, 0, 14)
        sidebar_layout.setSpacing(4)

        # 品牌标题（渐变文字）
        brand = QLabel("泰坦知识库")
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
        brand_line.setContentsMargins(24, 0, 24, 0)
        sidebar_layout.addWidget(brand_line)
        sidebar_layout.addSpacing(12)

        def nav_button(text: str, icon_key: str, index: int) -> QPushButton:
            button = QPushButton(text)
            button.setObjectName("navPrimary")
            button.setCheckable(True)
            button.setToolTip(text)
            set_named_icon(button, icon_key, "sidebar_text", 17)
            button.clicked.connect(lambda: self._switch_page(index))
            return button

        self.btn_knowledge = nav_button("知识库", "knowledge", 0)
        self.btn_knowledge.setChecked(True)
        self.btn_chat = nav_button("智能问答", "chat", 1)
        self.btn_catalog = nav_button("知识目录", "catalog", 2)
        self.btn_wiki = nav_button("知识 Wiki", "wiki", 3)
        self.btn_graph = nav_button("知识图谱", "graph", 4)

        sidebar_layout.addWidget(self.btn_knowledge)
        sidebar_layout.addWidget(self.btn_chat)
        sidebar_layout.addWidget(self.btn_catalog)
        sidebar_layout.addWidget(self.btn_wiki)
        sidebar_layout.addWidget(self.btn_graph)
        sidebar_layout.addStretch()

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

        # 侧边栏阴影
        sidebar_shadow = QGraphicsDropShadowEffect(sidebar)
        sidebar_shadow.setBlurRadius(18)
        sidebar_shadow.setOffset(2, 0)
        sidebar_shadow.setColor(QColor(0, 0, 0, 26))
        sidebar.setGraphicsEffect(sidebar_shadow)

        # ---- 内容区 ----
        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)

        self.knowledge_view = KnowledgeView()
        self.chat_view = ChatView(llm_indicator=self.llm_indicator)
        self.catalog_view = CatalogView(llm_indicator=self.llm_indicator)
        self.wiki_view = WikiView()
        self.graph_view = GraphView(llm_indicator=self.llm_indicator)

        self.stack.addWidget(self.knowledge_view)
        self.stack.addWidget(self.chat_view)
        self.stack.addWidget(self.catalog_view)
        self.stack.addWidget(self.wiki_view)
        self.stack.addWidget(self.graph_view)

        # 注册 LLM 状态回调
        register_llm_status_callback(self._on_llm_status)

        # 状态栏
        self.statusBar().showMessage("就绪")
        self._update_status()

        # MCP 心跳轮询
        self._mcp_timer = QTimer(self)
        self._mcp_timer.timeout.connect(self._check_mcp_status)
        self._mcp_timer.start(3000)

    def _switch_page(self, index: int):
        self.stack.setCurrentIndex(index)
        self.btn_knowledge.setChecked(index == 0)
        self.btn_chat.setChecked(index == 1)
        self.btn_catalog.setChecked(index == 2)
        self.btn_wiki.setChecked(index == 3)
        self.btn_graph.setChecked(index == 4)
        # 切换到知识目录时刷新，确保与知识库的标题修改同步
        if index == 2:
            self.catalog_view._load_catalog()

    def _open_settings(self):
        from src.gui.settings_dialog import SettingsDialog
        dialog = SettingsDialog(self)
        dialog.exec()

    def _update_status(self):
        count = Database.count_knowledge()
        self._update_status_bar()

    def _update_status_bar(self):
        count = Database.count_knowledge()
        from src.services.mcp_heartbeat import is_mcp_available
        from src.services.mcp_launcher import is_running as mcp_running
        mcp_online = is_mcp_available() or mcp_running()
        llm_status = self.llm_indicator.property("status") or "idle"
        llm_text = "就绪" if llm_status == "idle" else "运行中" if llm_status in ("running", "dim") else "异常"
        mcp_text = "在线" if mcp_online else "离线"
        self.statusBar().showMessage(
            f"{count} 条知识  |  LLM {llm_text}  |  MCP {mcp_text}"
        )

    def _on_llm_status(self, status: str, detail: str = ""):
        self.llm_indicator.set_status(status, detail)
        self._update_status_bar()

    def _check_mcp_status(self):
        from src.services.mcp_heartbeat import is_mcp_available
        from src.services.mcp_launcher import is_running
        online = is_mcp_available() or is_running()
        new_status = "online" if online else "offline"
        if self.mcp_light.property("status") != new_status:
            self.mcp_light.setProperty("status", new_status)
            self.mcp_light.setText(f"MCP {'活跃' if online else '离线'}")
            self.mcp_light.style().unpolish(self.mcp_light)
            self.mcp_light.style().polish(self.mcp_light)
            # 同步按钮状态
            self.btn_mcp_toggle.blockSignals(True)
            self.btn_mcp_toggle.setChecked(online)
            self.btn_mcp_toggle.setText("停止 MCP" if online else "启动 MCP")
            set_named_icon(self.btn_mcp_toggle, "mcp", "danger" if online else "text_dim", 14)
            self.btn_mcp_toggle.blockSignals(False)
            self._update_status_bar()

    def _toggle_mcp(self, checked: bool):
        from src.services.mcp_launcher import start, stop
        if checked:
            msg = start()
            self.statusBar().showMessage(msg, 5000)
        else:
            msg = stop()
            self.statusBar().showMessage(msg, 5000)
            self.mcp_light.setProperty("status", "offline")
            self.mcp_light.setText("MCP 离线")
            self.mcp_light.style().unpolish(self.mcp_light)
            self.mcp_light.style().polish(self.mcp_light)
