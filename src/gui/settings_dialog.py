"""设置对话框"""
import time

from PySide6.QtCore import QThread, Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from src.gui.icons import set_named_icon
from src.utils.config import Config


class ConnectionTestWorker(QThread):
    """Run one provider connection test without saving settings first."""

    completed = Signal(bool, str)

    def __init__(self, request, label: str, parent=None):
        super().__init__(parent)
        self._request = request
        self._label = label

    def run(self) -> None:
        from src.services.provider_runtime import run_provider_operation

        started = time.monotonic()
        try:
            response = run_provider_operation(
                "connection_test",
                self._request,
                isolation_mode="process",
                timeout=self._request.timeout_seconds,
            )
            elapsed = response.elapsed_ms or int((time.monotonic() - started) * 1000)
            if response.ok:
                self.completed.emit(True, f"{self._label}连接成功（{elapsed} ms）")
            else:
                self.completed.emit(
                    False,
                    f"{self._label}连接失败：{response.error_message or response.error_type or '未知错误'}",
                )
        except Exception as exc:  # noqa: BLE001 - surface a concise UI diagnostic
            self.completed.emit(False, f"{self._label}连接失败：{str(exc)[:300]}")


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setMinimumWidth(720)
        self.setMinimumHeight(680)
        self.resize(820, 760)
        self._setup_ui()
        self._load_values()

        # 服务操作异步轮询:ShellExecuteW runas 触发 UAC 后不等待操作完成,
        # 立即刷新会读到旧状态。用 QTimer 周期性刷新,跟踪真实结果。
        self._svc_poll = QTimer(self)
        self._svc_poll.setInterval(1000)
        self._svc_poll.timeout.connect(self._on_svc_poll_tick)
        self._svc_poll_ticks = 0
        self._svc_poll_expect_running: bool | None = None

    def reject(self) -> None:
        """Keep the dialog alive while a worker owns a provider subprocess."""
        worker = getattr(self, "_connection_test_worker", None)
        if worker is not None and worker.isRunning():
            QMessageBox.information(self, "连接测试中", "请等待当前连接测试完成后再关闭设置窗口。")
            return
        super().reject()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)
        tabs = QTabWidget()
        layout.addWidget(tabs)

        # ---- LLM 设置 ----
        llm_tab = QWidget()
        llm_form = QFormLayout(llm_tab)

        self.llm_provider = QLineEdit()
        self.llm_provider.setPlaceholderText("例如: openai, deepseek, zhipu, moonshot, ollama ...")
        self.llm_api_key = QLineEdit()
        self.llm_api_key.setEchoMode(QLineEdit.Password)
        self.llm_api_key.setPlaceholderText("sk-...")
        self.llm_base_url = QLineEdit()
        self.llm_base_url.setPlaceholderText("例如: https://api.deepseek.com/v1")
        self.llm_model = QLineEdit()
        self.llm_model.setPlaceholderText("例如: gpt-4o-mini, deepseek-chat, glm-4-flash ...")
        self.llm_temperature = QSpinBox()
        self.llm_temperature.setRange(0, 100)
        self.llm_temperature.setValue(70)
        self.llm_temperature.setSuffix("  (×0.01)")
        self.llm_max_tokens = QSpinBox()
        self.llm_max_tokens.setRange(256, 16384)
        self.llm_max_tokens.setSingleStep(256)

        llm_form.addRow("供应商名称：", self.llm_provider)
        llm_form.addRow("API Key：", self.llm_api_key)
        llm_form.addRow("API 地址：", self.llm_base_url)
        llm_form.addRow("模型：", self.llm_model)
        llm_form.addRow("Temperature：", self.llm_temperature)
        llm_form.addRow("Max Tokens：", self.llm_max_tokens)

        self.llm_test_button, self.llm_test_status = self._add_connection_test_row(
            llm_form, "测试 LLM 连接", self._test_llm_connection
        )

        hint_llm = QLabel("说明：API 地址填写供应商的 OpenAI 兼容接口地址。\n"
                          "国内常见：DeepSeek(https://api.deepseek.com/v1)、智谱(https://open.bigmodel.cn/api/paas/v4)、\n"
                          "Moonshot(https://api.moonshot.cn/v1)、硅基流动(https://api.siliconflow.cn/v1) 等。\n"
                          "本地 Ollama 填 http://localhost:11434/v1，Key 随意填。")
        hint_llm.setObjectName("hintLabel")
        hint_llm.setWordWrap(True)
        llm_form.addRow(hint_llm)
        tabs.addTab(llm_tab, "LLM")

        # ---- Embedding 设置 ----
        emb_tab = QWidget()
        emb_form = QFormLayout(emb_tab)

        self.emb_reuse_llm = QCheckBox("与 LLM 使用相同供应商（仅填写下方不同的字段覆盖）")
        self.emb_provider = QLineEdit()
        self.emb_provider.setPlaceholderText("留空则与 LLM 相同")
        self.emb_api_key = QLineEdit()
        self.emb_api_key.setEchoMode(QLineEdit.Password)
        self.emb_api_key.setPlaceholderText("留空则复用 LLM 的 Key")
        self.emb_base_url = QLineEdit()
        self.emb_base_url.setPlaceholderText("留空则复用 LLM 的地址")
        self.emb_model = QLineEdit()
        self.emb_model.setPlaceholderText("例如: text-embedding-3-small, embedding-3 ...")

        emb_form.addRow(self.emb_reuse_llm)
        emb_form.addRow("供应商名称：", self.emb_provider)
        emb_form.addRow("API Key：", self.emb_api_key)
        emb_form.addRow("API 地址：", self.emb_base_url)
        emb_form.addRow("模型：", self.emb_model)

        self.embedding_test_button, self.embedding_test_status = self._add_connection_test_row(
            emb_form, "测试 Embedding 连接", self._test_embedding_connection
        )

        hint_emb = QLabel("说明：大多数供应商的 Embedding 接口与 LLM 接口共享同一个地址和 Key，\n"
                          '只需改模型名即可。勾选「与 LLM 相同」后留空的字段会自动复用 LLM 设置。')
        hint_emb.setObjectName("hintLabel")
        hint_emb.setWordWrap(True)
        emb_form.addRow(hint_emb)
        tabs.addTab(emb_tab, "Embedding")

        # ---- Reranker 设置 ----
        rerank_tab = QWidget()
        rerank_form = QFormLayout(rerank_tab)

        self.rerank_enabled = QCheckBox("启用重排序")
        self.rerank_use_llm_fallback = QCheckBox("专用模型失败时回退到 LLM 打分")

        self.rerank_reuse_llm = QCheckBox("与 LLM 使用相同供应商（仅填写下方不同的字段覆盖）")
        self.rerank_provider = QLineEdit()
        self.rerank_provider.setPlaceholderText("留空则复用 LLM 的供应商")
        self.rerank_api_key = QLineEdit()
        self.rerank_api_key.setEchoMode(QLineEdit.Password)
        self.rerank_api_key.setPlaceholderText("留空则复用 LLM 的 Key")
        self.rerank_base_url = QLineEdit()
        self.rerank_base_url.setPlaceholderText("留空则复用 LLM 的地址")
        self.rerank_model = QLineEdit()
        self.rerank_model.setPlaceholderText("例如: BAAI/bge-reranker-v2-mini, bge-reranker-base ...")

        rerank_form.addRow(self.rerank_enabled)
        rerank_form.addRow(self.rerank_use_llm_fallback)
        rerank_form.addRow(self.rerank_reuse_llm)
        rerank_form.addRow("供应商名称：", self.rerank_provider)
        rerank_form.addRow("API Key：", self.rerank_api_key)
        rerank_form.addRow("API 地址：", self.rerank_base_url)
        rerank_form.addRow("模型：", self.rerank_model)

        self.rerank_test_button, self.rerank_test_status = self._add_connection_test_row(
            rerank_form, "测试重排序连接", self._test_rerank_connection
        )

        hint_rerank = QLabel("说明：专用重排序模型（如 BAAI/bge-reranker-v2-mini）比 LLM 打分更快更准。\n"
                             "如不配置模型，将使用 LLM 打分作为重排序方式。\n"
                             "硅基流动支持 rerank API，请确保模型名称正确。")
        hint_rerank.setObjectName("hintLabel")
        hint_rerank.setWordWrap(True)
        rerank_form.addRow(hint_rerank)
        tabs.addTab(rerank_tab, "Reranker")

        # ---- RAG 设置 ----
        rag_tab = QWidget()
        rag_form = QFormLayout(rag_tab)
        self.rag_top_k = QSpinBox()
        self.rag_top_k.setRange(1, 20)
        self.rag_chunk_size = QSpinBox()
        self.rag_chunk_size.setRange(100, 5000)
        self.rag_chunk_size.setSingleStep(100)
        self.rag_chunk_overlap = QSpinBox()
        self.rag_chunk_overlap.setRange(0, 500)
        self.rag_chunk_overlap.setSingleStep(10)
        self.rag_score_threshold = QSpinBox()
        self.rag_score_threshold.setRange(0, 100)
        self.rag_score_threshold.setValue(50)
        self.rag_score_threshold.setSuffix("  (×0.01)")

        rag_form.addRow("Top K：", self.rag_top_k)
        rag_form.addRow("Chunk Size：", self.rag_chunk_size)
        rag_form.addRow("Chunk Overlap：", self.rag_chunk_overlap)
        rag_form.addRow("Score Threshold：", self.rag_score_threshold)
        self.rag_test_button, self.rag_test_status = self._add_connection_test_row(
            rag_form, "测试 RAG 向量连接", self._test_embedding_connection
        )
        rag_hint = QLabel("说明：RAG 的联网能力由 Embedding（向量模型）提供；此测试会验证当前输入的向量模型地址、Key 和模型名，无需先保存设置。")
        rag_hint.setObjectName("hintLabel")
        rag_hint.setWordWrap(True)
        rag_form.addRow(rag_hint)
        tabs.addTab(rag_tab, "RAG")

        # ---- 外观设置 ----
        appearance_tab = QWidget()
        appearance_form = QFormLayout(appearance_tab)

        self.theme_combo = QComboBox()
        self.theme_combo.addItem("浅色", "light")
        self.theme_combo.addItem("暗色", "dark")

        self.font_size = QSpinBox()
        self.font_size.setRange(10, 24)
        self.font_size.setValue(14)
        self.font_size.setSuffix(" px")

        appearance_form.addRow("主题配色：", self.theme_combo)
        appearance_form.addRow("字体大小：", self.font_size)

        hint_appearance = QLabel("说明：切换主题配色后立即生效。字体大小影响全局文字显示。")
        hint_appearance.setObjectName("hintLabel")
        hint_appearance.setWordWrap(True)
        appearance_form.addRow(hint_appearance)
        tabs.addTab(appearance_tab, "外观")

        # ---- MCP 工具配置档 ----
        from src.mcp.tool_profiles import PROFILE_INFO

        self._profile_info = PROFILE_INFO

        mcp_tab = QWidget()
        mcp_outer = QVBoxLayout(mcp_tab)
        mcp_outer.setContentsMargins(0, 0, 0, 0)
        mcp_outer.setSpacing(0)

        mcp_scroll = QScrollArea()
        mcp_scroll.setWidgetResizable(True)
        mcp_scroll.setFrameShape(QFrame.Shape.NoFrame)
        mcp_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        mcp_container = QWidget()
        mcp_layout = QVBoxLayout(mcp_container)
        mcp_layout.setContentsMargins(12, 12, 12, 12)
        mcp_layout.setSpacing(12)

        # 档位选择
        profile_group = QGroupBox("MCP 工具配置档")
        profile_group.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        profile_form = QFormLayout(profile_group)

        self.mcp_profile_combo = QComboBox()
        for key in ("core", "extended", "admin", "full", "legacy"):
            self.mcp_profile_combo.addItem(self._profile_info[key]["label"], key)
        self.mcp_profile_combo.currentIndexChanged.connect(self._on_mcp_profile_changed)
        profile_form.addRow("档位选择：", self.mcp_profile_combo)

        mcp_layout.addWidget(profile_group)

        # 档位详情
        self._mcp_detail_group = QGroupBox("当前档位详情")
        self._mcp_detail_group.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        detail_form = QFormLayout(self._mcp_detail_group)
        detail_form.setLabelAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)

        self._mcp_summary_label = QLabel("—")
        self._mcp_summary_label.setWordWrap(True)
        self._mcp_scope_label = QLabel("—")
        self._mcp_scope_label.setWordWrap(True)
        self._mcp_scope_label.setObjectName("hintLabel")
        self._mcp_usecase_label = QLabel("—")
        self._mcp_usecase_label.setWordWrap(True)
        self._mcp_writes_label = QLabel("—")
        self._mcp_writes_label.setWordWrap(True)
        self._mcp_writes_label.setObjectName("hintLabel")

        detail_form.addRow("概述：", self._mcp_summary_label)
        detail_form.addRow("工具范围：", self._mcp_scope_label)
        detail_form.addRow("适用场景：", self._mcp_usecase_label)
        detail_form.addRow("写权限：", self._mcp_writes_label)

        mcp_layout.addWidget(self._mcp_detail_group)

        # 辅助开关
        switches_group = QGroupBox("辅助开关")
        switches_group.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        switches_layout = QVBoxLayout(switches_group)

        self.mcp_enable_aliases = QCheckBox("启用 legacy 别名(注册 kb.search / kb.ask 等命名空间别名)")
        self.mcp_enable_experimental = QCheckBox("启用 experimental 工具(Wiki、图谱、Agent Memory)")
        self.mcp_enable_wiki = QCheckBox("启用 Wiki 系统(知识体检、死链修复、自动编译)")
        switches_layout.addWidget(self.mcp_enable_aliases)
        switches_layout.addWidget(self.mcp_enable_experimental)
        switches_layout.addWidget(self.mcp_enable_wiki)

        switches_hint = QLabel(
            "说明：legacy 别名仅在客户端依赖 kb.* 命名时打开;"
            "experimental 工具默认隐藏,启用后会暴露 Wiki / 图谱 / Agent Memory 相关工具(对应需求时再开)。"
            "Wiki 系统是知识体检、死链修复、自动编译的总开关 —— "
            "experimental 仅负责把工具暴露给 MCP 客户端,需同时打开此开关,后端才真正启用(即时生效,无需重启)。"
        )
        switches_hint.setObjectName("hintLabel")
        switches_hint.setWordWrap(True)
        switches_layout.addWidget(switches_hint)

        mcp_layout.addWidget(switches_group)

        hint_mcp = QLabel(
            "说明:修改配置档后需重启 MCP server 才能生效(关闭并重新启动侧边栏 MCP 进程,"
            "或重启 Windows 服务)。\n"
            "当前活跃档位可通过任何 MCP 客户端调用 `kb_capabilities` 工具查看。"
        )
        hint_mcp.setObjectName("hintLabel")
        hint_mcp.setWordWrap(True)
        mcp_layout.addWidget(hint_mcp)

        mcp_layout.addStretch()
        mcp_scroll.setWidget(mcp_container)
        mcp_outer.addWidget(mcp_scroll)
        tabs.addTab(mcp_tab, "MCP")

        # 图谱后端设置
        graph_tab = QWidget()
        graph_outer = QVBoxLayout(graph_tab)
        graph_outer.setContentsMargins(0, 0, 0, 0)
        graph_outer.setSpacing(0)

        graph_scroll = QScrollArea()
        graph_scroll.setObjectName("graphBackendScroll")
        graph_scroll.setWidgetResizable(True)
        graph_scroll.setFrameShape(QFrame.Shape.NoFrame)
        graph_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        graph_container = QWidget()
        graph_layout = QVBoxLayout(graph_container)
        graph_layout.setContentsMargins(12, 12, 12, 12)
        graph_layout.setSpacing(12)

        # SQLite 图存储说明
        provider_group = QGroupBox("SQLite 图谱存储")
        provider_group.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        provider_layout = QVBoxLayout(provider_group)

        provider_hint = QLabel(
            "图谱数据统一保存在本地 SQLite 数据库中。Page、Block、Tag、引用关系"
            "和语义关系共用 data/kb.db，无需部署外部图数据库服务。"
        )
        provider_hint.setObjectName("hintLabel")
        provider_hint.setWordWrap(True)
        provider_layout.addWidget(provider_hint)

        graph_layout.addWidget(provider_group)

        hint_graph = QLabel(
            "说明：统一图谱、来源图谱和多跳遍历都从 SQLite 表动态构建；"
            "导入、编辑和删除知识时无需执行额外迁移或同步。"
        )
        hint_graph.setObjectName("hintLabel")
        hint_graph.setWordWrap(True)
        graph_layout.addWidget(hint_graph)

        graph_scroll.setWidget(graph_container)
        graph_outer.addWidget(graph_scroll)

        tabs.addTab(graph_tab, "图谱存储")

        # ---- 服务设置 ----
        service_tab = QWidget()
        service_layout = QVBoxLayout(service_tab)

        # 服务状态组
        status_group = QGroupBox("Windows 服务状态")
        status_grid = QGridLayout(status_group)

        self._svc_status_label = QLabel("检测中...")
        self._svc_status_label.setFont(QFont("", -1, QFont.Weight.Bold))
        status_grid.addWidget(QLabel("服务状态："), 0, 0)
        status_grid.addWidget(self._svc_status_label, 0, 1)

        self._svc_failure_label = QLabel("检测中...")
        status_grid.addWidget(QLabel("崩溃重启："), 1, 0)
        status_grid.addWidget(self._svc_failure_label, 1, 1)

        self._svc_mode_label = QLabel("服务模式")
        status_grid.addWidget(QLabel("启动方式："), 2, 0)
        status_grid.addWidget(self._svc_mode_label, 2, 1)

        # 操作按钮组
        ops_group = QGroupBox("服务操作")
        ops_layout = QGridLayout(ops_group)

        self._btn_svc_start = QPushButton("启动服务")
        self._btn_svc_start.setMinimumHeight(32)
        self._btn_svc_start.clicked.connect(self._on_svc_start)

        self._btn_svc_stop = QPushButton("停止服务")
        self._btn_svc_stop.setMinimumHeight(32)
        self._btn_svc_stop.clicked.connect(self._on_svc_stop)

        self._btn_svc_restart = QPushButton("重启服务")
        self._btn_svc_restart.setMinimumHeight(32)
        self._btn_svc_restart.clicked.connect(self._on_svc_restart)

        ops_layout.addWidget(self._btn_svc_start, 0, 0)
        ops_layout.addWidget(self._btn_svc_stop, 0, 1)
        ops_layout.addWidget(self._btn_svc_restart, 0, 2)

        # 服务安装/卸载
        install_group = QGroupBox("服务管理")
        install_layout = QHBoxLayout(install_group)

        self._btn_svc_install = QPushButton("注册为 Windows 服务")
        self._btn_svc_install.setMinimumHeight(32)
        self._btn_svc_install.clicked.connect(self._on_svc_install)

        self._btn_svc_remove = QPushButton("卸载服务")
        self._btn_svc_remove.setMinimumHeight(32)
        self._btn_svc_remove.clicked.connect(self._on_svc_remove)

        self._btn_svc_set_failure = QPushButton("配置崩溃重启")
        self._btn_svc_set_failure.setMinimumHeight(32)
        self._btn_svc_set_failure.setToolTip("设置崩溃后 5s/10s/30s 自动重启，24h 重置计数")
        self._btn_svc_set_failure.clicked.connect(self._on_svc_set_failure)

        install_layout.addWidget(self._btn_svc_install)
        install_layout.addWidget(self._btn_svc_remove)
        install_layout.addWidget(self._btn_svc_set_failure)

        # 说明
        hint_service = QLabel(
            "说明：注册为 Windows 服务后，MCP Server 将开机自启、崩溃自动重启。"
            "侧边栏的「启动 MCP」按钮也会自动切换为服务模式操作。\n"
            "安装/卸载/配置崩溃重启需要管理员权限（会弹出 UAC 确认框）。"
        )
        hint_service.setObjectName("hintLabel")
        hint_service.setWordWrap(True)

        service_layout.addWidget(status_group)
        service_layout.addWidget(ops_group)
        service_layout.addWidget(install_group)
        service_layout.addWidget(hint_service)
        service_layout.addStretch()
        tabs.addTab(service_tab, "服务")

        # 首次加载服务状态
        self._refresh_svc_status()

        # ---- 按钮 ----
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_save = QPushButton("保存")
        btn_save.setObjectName("primaryBtn")
        set_named_icon(btn_save, "save", "on_accent", 14)
        btn_save.clicked.connect(self._save)
        btn_cancel = QPushButton("取消")
        set_named_icon(btn_cancel, "close", "text_dim", 13)
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_save)
        btn_row.addWidget(btn_cancel)
        layout.addLayout(btn_row)

    def _add_connection_test_row(self, form: QFormLayout, text: str, callback):
        """Add a non-blocking connection-test control to a settings form."""
        container = QWidget()
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        button = QPushButton(text)
        button.clicked.connect(callback)
        status = QLabel("未测试")
        status.setObjectName("hintLabel")
        status.setWordWrap(True)
        row.addWidget(button)
        row.addWidget(status, 1)
        form.addRow("连接测试：", container)
        return button, status

    @staticmethod
    def _field_value(field: QLineEdit) -> str:
        return field.text().strip()

    def _start_connection_test(self, *, request, label: str, buttons, statuses) -> None:
        for button in buttons:
            button.setEnabled(False)
        for status in statuses:
            status.setText("测试中…")

        worker = ConnectionTestWorker(request, label, self)
        self._connection_test_worker = worker

        def finish(ok: bool, message: str) -> None:
            for button in buttons:
                button.setEnabled(True)
            for status in statuses:
                status.setText(message)
                status.setProperty("connectionStatus", "success" if ok else "error")
                status.style().polish(status)
            self._connection_test_worker = None

        worker.completed.connect(finish)
        worker.start()

    def _test_llm_connection(self) -> None:
        from src.services.provider_runtime import ProviderRequest

        base_url = self._field_value(self.llm_base_url)
        model = self._field_value(self.llm_model)
        api_key = self._field_value(self.llm_api_key)
        if not base_url or not model or not api_key:
            self.llm_test_status.setText("请填写 API 地址、模型和 API Key 后再测试。")
            return
        self._start_connection_test(
            request=ProviderRequest(
                provider_type="openai_compatible_chat",
                base_url=base_url,
                model=model,
                payload={"messages": [{"role": "user", "content": "Reply with OK."}], "max_tokens": 8, "temperature": 0},
                timeout_seconds=15,
                secret_env_key="SHINEHE_LLM_API_KEY",
                credential=api_key,
            ),
            label="LLM ",
            buttons=(self.llm_test_button,),
            statuses=(self.llm_test_status,),
        )

    def _embedding_connection_fields(self) -> tuple[str, str, str]:
        reuse_llm = self.emb_reuse_llm.isChecked()
        return (
            self._field_value(self.emb_base_url) or (self._field_value(self.llm_base_url) if reuse_llm else ""),
            self._field_value(self.emb_model),
            self._field_value(self.emb_api_key) or (self._field_value(self.llm_api_key) if reuse_llm else ""),
        )

    def _test_embedding_connection(self) -> None:
        from src.services.provider_runtime import ProviderRequest

        base_url, model, api_key = self._embedding_connection_fields()
        if not base_url or not model or not api_key:
            message = "请填写 Embedding 的 API 地址、模型和 API Key 后再测试。"
            self.embedding_test_status.setText(message)
            self.rag_test_status.setText(message)
            return
        self._start_connection_test(
            request=ProviderRequest(
                provider_type="openai_compatible_embedding",
                base_url=base_url,
                model=model,
                payload={"input": ["连接测试"]},
                timeout_seconds=15,
                secret_env_key="SHINEHE_EMBEDDING_API_KEY",
                credential=api_key,
            ),
            label="Embedding / RAG ",
            buttons=(self.embedding_test_button, self.rag_test_button),
            statuses=(self.embedding_test_status, self.rag_test_status),
        )

    def _test_rerank_connection(self) -> None:
        from src.services.provider_runtime import ProviderRequest

        reuse_llm = self.rerank_reuse_llm.isChecked()
        base_url = self._field_value(self.rerank_base_url) or (self._field_value(self.llm_base_url) if reuse_llm else "")
        model = self._field_value(self.rerank_model)
        api_key = self._field_value(self.rerank_api_key) or (self._field_value(self.llm_api_key) if reuse_llm else "")
        if not base_url or not model or not api_key:
            self.rerank_test_status.setText("请填写重排序的 API 地址、模型和 API Key 后再测试。")
            return
        self._start_connection_test(
            request=ProviderRequest(
                provider_type="reranker_api",
                base_url=base_url,
                model=model,
                payload={"query": "连接测试", "documents": ["用于验证重排序服务的测试文本"], "top_n": 1},
                timeout_seconds=15,
                secret_env_key="SHINEHE_RERANKER_API_KEY",
                credential=api_key,
            ),
            label="重排序 ",
            buttons=(self.rerank_test_button,),
            statuses=(self.rerank_test_status,),
        )

    def _load_values(self):
        self.llm_provider.setText(Config.get("llm.provider", ""))
        self.llm_api_key.setText(Config.get("llm.api_key", ""))
        self.llm_base_url.setText(Config.get("llm.base_url", ""))
        self.llm_model.setText(Config.get("llm.model", ""))
        self.llm_temperature.setValue(int(Config.get("llm.temperature", 0.7) * 100))
        self.llm_max_tokens.setValue(Config.get("llm.max_tokens", 2048))

        self.emb_reuse_llm.setChecked(Config.get("embedding.reuse_llm", True))
        self.emb_provider.setText(Config.get("embedding.provider", ""))
        self.emb_api_key.setText(Config.get("embedding.api_key", ""))
        self.emb_base_url.setText(Config.get("embedding.base_url", ""))
        self.emb_model.setText(Config.get("embedding.model", ""))

        # Reranker 配置
        self.rerank_enabled.setChecked(Config.get("reranker.enabled", True))
        self.rerank_use_llm_fallback.setChecked(Config.get("reranker.use_llm_fallback", True))
        self.rerank_reuse_llm.setChecked(Config.get("reranker.reuse_llm", False))
        self.rerank_provider.setText(Config.get("reranker.provider", ""))
        self.rerank_api_key.setText(Config.get("reranker.api_key", ""))
        self.rerank_base_url.setText(Config.get("reranker.base_url", ""))
        self.rerank_model.setText(Config.get("reranker.model", ""))

        self.rag_top_k.setValue(Config.get("rag.top_k", 5))
        self.rag_chunk_size.setValue(Config.get("rag.chunk_size", 500))
        self.rag_chunk_overlap.setValue(Config.get("rag.chunk_overlap", 50))
        self.rag_score_threshold.setValue(int(Config.get("rag.score_threshold", 0.5) * 100))

        theme = Config.get("appearance.theme", "light")
        idx = self.theme_combo.findData(theme)
        if idx >= 0:
            self.theme_combo.setCurrentIndex(idx)
        self.font_size.setValue(Config.get("appearance.font_size", 14))

        # MCP 工具配置档
        mcp_profile = Config.get("mcp.tool_profile", "extended") or "extended"
        if mcp_profile not in {"core", "extended", "admin", "full", "legacy"}:
            mcp_profile = "extended"
        midx = self.mcp_profile_combo.findData(mcp_profile)
        if midx >= 0:
            self.mcp_profile_combo.setCurrentIndex(midx)
        # 触发详情区刷新(防止首次加载时未联动)
        self._on_mcp_profile_changed()
        self.mcp_enable_aliases.setChecked(
            bool(Config.get("mcp.enable_legacy_aliases", mcp_profile == "legacy"))
        )
        self.mcp_enable_experimental.setChecked(
            bool(Config.get("mcp.experimental_tools_enabled", False))
        )
        self.mcp_enable_wiki.setChecked(
            bool(Config.get("wiki.enabled", False))
        )

    def _save(self):
        if not self.llm_provider.text().strip() or not self.llm_base_url.text().strip():
            QMessageBox.warning(self, "提示", "请至少填写 LLM 的供应商名称和 API 地址。")
            return

        # ---- 快照旧值，用于检测 API Key 变更 ----
        old_llm_key = Config.get("llm.api_key", "")
        old_emb_key = Config.get("embedding.api_key", "")
        old_rerank_key = Config.get("reranker.api_key", "")

        Config.set("llm.provider", self.llm_provider.text().strip())
        new_llm_key = self.llm_api_key.text().strip()
        Config.set("llm.api_key", new_llm_key)
        Config.set("llm.base_url", self.llm_base_url.text().strip())
        Config.set("llm.model", self.llm_model.text().strip())
        Config.set("llm.temperature", self.llm_temperature.value() / 100)
        Config.set("llm.max_tokens", self.llm_max_tokens.value())

        reuse = self.emb_reuse_llm.isChecked()
        Config.set("embedding.reuse_llm", reuse)
        if reuse:
            new_emb_key = self.emb_api_key.text().strip() or new_llm_key
            Config.set("embedding.provider", self.emb_provider.text().strip() or self.llm_provider.text().strip())
            Config.set("embedding.api_key", new_emb_key)
            Config.set("embedding.base_url", self.emb_base_url.text().strip() or self.llm_base_url.text().strip())
        else:
            new_emb_key = self.emb_api_key.text().strip()
            Config.set("embedding.provider", self.emb_provider.text().strip())
            Config.set("embedding.api_key", new_emb_key)
            Config.set("embedding.base_url", self.emb_base_url.text().strip())
        Config.set("embedding.model", self.emb_model.text().strip())

        # 保存 Reranker 配置
        Config.set("reranker.enabled", self.rerank_enabled.isChecked())
        Config.set("reranker.use_llm_fallback", self.rerank_use_llm_fallback.isChecked())
        rerank_reuse = self.rerank_reuse_llm.isChecked()
        Config.set("reranker.reuse_llm", rerank_reuse)
        if rerank_reuse:
            new_rerank_key = self.rerank_api_key.text().strip() or new_llm_key
            Config.set("reranker.provider", self.rerank_provider.text().strip() or self.llm_provider.text().strip())
            Config.set("reranker.api_key", new_rerank_key)
            Config.set("reranker.base_url", self.rerank_base_url.text().strip() or self.llm_base_url.text().strip())
        else:
            new_rerank_key = self.rerank_api_key.text().strip()
            Config.set("reranker.provider", self.rerank_provider.text().strip())
            Config.set("reranker.api_key", new_rerank_key)
            Config.set("reranker.base_url", self.rerank_base_url.text().strip())
        Config.set("reranker.model", self.rerank_model.text().strip())

        Config.set("rag.top_k", self.rag_top_k.value())
        Config.set("rag.chunk_size", self.rag_chunk_size.value())
        Config.set("rag.chunk_overlap", self.rag_chunk_overlap.value())
        Config.set("rag.score_threshold", self.rag_score_threshold.value() / 100)

        Config.set("appearance.theme", self.theme_combo.currentData())
        Config.set("appearance.font_size", self.font_size.value())

        # 图谱后端固定为 SQLite
        Config.set("graph_backend.provider", "sqlite")

        # 保存 MCP 配置档(先取旧值再 set,便于判断是否需要重启提示)
        new_profile = self.mcp_profile_combo.currentData() or "extended"
        new_aliases = self.mcp_enable_aliases.isChecked()
        new_experimental = self.mcp_enable_experimental.isChecked()
        old_profile = Config.get("mcp.tool_profile", "extended")
        old_aliases = bool(Config.get("mcp.enable_legacy_aliases", False))
        old_experimental = bool(Config.get("mcp.experimental_tools_enabled", False))
        mcp_changed = (
            new_profile != old_profile
            or new_aliases != old_aliases
            or new_experimental != old_experimental
        )
        Config.set("mcp.tool_profile", new_profile)
        Config.set("mcp.enable_legacy_aliases", new_aliases)
        Config.set("mcp.experimental_tools_enabled", new_experimental)
        Config.set("wiki.enabled", self.mcp_enable_wiki.isChecked())

        Config.save()

        # 立即应用主题
        from PySide6.QtWidgets import QApplication

        from src.gui.theme import apply
        apply(QApplication.instance())

        # ---- 检测 API Key 是否发生变更 ----
        api_key_changed = (
            new_llm_key != old_llm_key
            or new_emb_key != old_emb_key
            or new_rerank_key != old_rerank_key
        )

        if api_key_changed and mcp_changed:
            # API Key + MCP 配置同时变更
            reply = QMessageBox.question(
                self, "已保存",
                "设置已保存。\n\n"
                "检测到以下变更：\n"
                "• API Key（LLM / Embedding / Reranker）\n"
                "• MCP 配置档\n\n"
                "Windows MCP 服务需要重启才能加载新 Key 并生效。\n\n"
                "是否立即重启服务？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self._on_svc_restart()
        elif api_key_changed:
            # 仅 API Key 变更 — 检查是否以 Windows 服务模式运行
            from src.services.mcp_launcher import get_service_status, is_service_installed
            if is_service_installed() and get_service_status() == "running":
                reply = QMessageBox.question(
                    self, "API Key 已更新",
                    "API Key 已保存（通过 DPAPI 加密存储，跨账户安全共享）。\n\n"
                    "当前 MCP 服务正在运行，需要重启才能加载新 Key。\n\n"
                    "是否立即重启 Windows 服务？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.Yes,
                )
                if reply == QMessageBox.StandardButton.Yes:
                    self._on_svc_restart()
                else:
                    QMessageBox.information(
                        self, "提示",
                        "可稍后手动重启：设置页 → 服务管理 →「重启服务」按钮。",
                    )
            else:
                # 服务未安装或未运行，Key 已持久化，下次启动自动加载
                QMessageBox.information(
                    self, "API Key 已保存",
                    "API Key 已保存，下次启动 MCP 服务时自动加载。",
                )
        elif mcp_changed:
            QMessageBox.information(
                self, "已保存",
                "设置已保存。\n\n"
                "MCP 配置档已变更,需重启 MCP server 才能生效("
                "关闭并重新启动侧边栏 MCP 进程,或重启 Windows 服务)。"
            )
        else:
            QMessageBox.information(self, "已保存", "设置已保存并生效。")

    # ---- 服务管理 ----

    def _refresh_svc_status(self):
        """刷新服务状态显示"""
        from src.services.mcp_launcher import (
            get_service_failure_config,
            get_service_status,
            is_service_installed,
        )
        installed = is_service_installed()
        if installed:
            status = get_service_status()
            status_text = {
                "running": "运行中",
                "stopped": "已停止",
                "unknown": "未知",
            }.get(status, status)
            self._svc_status_label.setText(status_text)
            self._svc_status_label.setProperty("status", status)
            self._svc_mode_label.setText("Windows 服务（开机自启）")

            # 崩溃重启策略
            fc = get_service_failure_config()
            if fc.get("configured"):
                actions_desc = " / ".join(
                    f"{a['delay_ms']//1000}s" for a in fc.get("actions", [])
                )
                self._svc_failure_label.setText(f"已配置（{actions_desc} 自动重启）")
            else:
                self._svc_failure_label.setText("未配置")

            # 按钮状态
            running = status == "running"
            self._btn_svc_start.setEnabled(not running)
            self._btn_svc_stop.setEnabled(running)
            self._btn_svc_restart.setEnabled(running)
            self._btn_svc_install.setEnabled(False)
            self._btn_svc_remove.setEnabled(True)
            self._btn_svc_set_failure.setEnabled(True)
        else:
            self._svc_status_label.setText("未注册")
            self._svc_mode_label.setText("子进程模式（关闭 GUI 后继续运行）")
            self._svc_failure_label.setText("—")
            self._btn_svc_start.setEnabled(False)
            self._btn_svc_stop.setEnabled(False)
            self._btn_svc_restart.setEnabled(False)
            self._btn_svc_install.setEnabled(True)
            self._btn_svc_remove.setEnabled(False)
            self._btn_svc_set_failure.setEnabled(False)

        # 刷新按钮样式
        for lbl in [self._svc_status_label]:
            lbl.style().polish(lbl)

    def _on_svc_start(self):
        from src.services.mcp_launcher import service_start
        msg = service_start()
        if "UAC" in msg:
            # UAC 异步:用户先确认 UAC 弹窗,再点此处「确定」触发后台轮询
            QMessageBox.information(self, "服务操作", msg + "\n\n请先在 UAC 弹窗中确认,再点击此处「确定」开始跟踪启动结果...")
            # 启动类操作:期望最终变 running,超时未达则提示排查
            self._poll_svc_after_uac(expect_running=True)
        else:
            # 非 UAC 分支:端口冲突/已在运行/提权失败等,显示后只刷新一次
            QMessageBox.warning(self, "服务操作", msg)
            self._refresh_svc_status()

    def _on_svc_stop(self):
        from src.services.mcp_launcher import service_stop
        msg = service_stop()
        if "UAC" in msg:
            QMessageBox.information(self, "服务操作", msg + "\n\n请先在 UAC 弹窗中确认,再点击此处「确定」开始跟踪停止结果...")
        else:
            QMessageBox.information(self, "服务操作", msg)
        self._poll_svc_after_uac(expect_running=False)

    def _on_svc_restart(self):
        from src.services.mcp_launcher import service_restart
        msg = service_restart()
        if "UAC" in msg:
            QMessageBox.information(self, "服务操作", msg + "\n\n请先在 UAC 弹窗中确认,再点击此处「确定」开始跟踪重启结果...")
        else:
            QMessageBox.information(self, "服务操作", msg)
        self._poll_svc_after_uac(expect_running=True)

    def _on_svc_install(self):
        reply = QMessageBox.question(
            self, "注册 Windows 服务",
            "将 MCP Server 注册为 Windows 服务后：\n"
            "• 开机自动启动\n"
            "• 崩溃自动重启\n"
            "• 侧边栏 MCP 按钮自动切换为服务模式\n\n"
            "需要管理员权限（会弹出 UAC 确认框）。\n\n"
            "确定注册？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        from src.services.mcp_launcher import service_install
        msg = service_install()
        if "UAC" in msg:
            QMessageBox.information(self, "服务安装", msg + "\n\n请先在 UAC 弹窗中确认,再点击此处「确定」开始跟踪注册结果...")
        else:
            QMessageBox.information(self, "服务安装", msg + "\n\n点击确定后刷新状态...")
        # 注册后状态语义不同(可能 stopped),仅刷新不判定成败
        self._poll_svc_after_uac(expect_running=None)

    def _on_svc_remove(self):
        reply = QMessageBox.question(
            self, "卸载 Windows 服务",
            "卸载后 MCP Server 将恢复为子进程模式（关闭 GUI 后继续运行）。\n"
            "需要管理员权限（会弹出 UAC 确认框）。\n\n"
            "确定卸载？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        from src.services.mcp_launcher import service_remove
        msg = service_remove()
        if "UAC" in msg:
            QMessageBox.information(self, "服务卸载", msg + "\n\n请先在 UAC 弹窗中确认,再点击此处「确定」开始跟踪卸载结果...")
        else:
            QMessageBox.information(self, "服务卸载", msg + "\n\n点击确定后刷新状态...")
        self._poll_svc_after_uac(expect_running=None)

    def _on_svc_set_failure(self):
        from src.services.mcp_launcher import service_configure_failure
        msg = service_configure_failure()
        if "UAC" in msg:
            QMessageBox.information(self, "崩溃重启策略", msg + "\n\n请先在 UAC 弹窗中确认,再点击此处「确定」开始刷新配置...")
        else:
            QMessageBox.information(self, "崩溃重启策略", msg)
        self._poll_svc_after_uac(expect_running=None)

    # ---- 服务操作异步轮询 ----

    def _poll_svc_after_uac(self, expect_running: bool | None):
        """UAC 异步操作后轮询服务状态,跟踪真实结果。

        expect_running:
          True  — 期望服务变 running(start/restart),超时未达提示「启动失败」
          False — 期望服务变 stopped(stop)
          None  — 仅周期刷新不判定成败(install/remove/configure)
        """
        self._svc_poll_expect_running = expect_running
        self._svc_poll_ticks = 8  # ~8 秒覆盖服务启动/停止窗口
        self._svc_poll.start()
        self._refresh_svc_status()

    def _on_svc_poll_tick(self):
        self._refresh_svc_status()
        self._svc_poll_ticks -= 1

        # 仅刷新模式:到时即停,不判定
        if self._svc_poll_expect_running is None:
            if self._svc_poll_ticks <= 0:
                self._svc_poll.stop()
            return

        from src.services.mcp_launcher import get_service_status, is_service_installed
        status = get_service_status() if is_service_installed() else "not_installed"
        reached = (
            status == "running"
            if self._svc_poll_expect_running
            else status in ("stopped", "not_installed")
        )
        if reached or self._svc_poll_ticks <= 0:
            self._svc_poll.stop()
            if not reached:
                self._prompt_svc_failure(self._svc_poll_expect_running)

    def _prompt_svc_failure(self, expect_running: bool):
        """操作超时未达预期时引导用户排查,而非默默显示旧状态。"""
        if expect_running:
            QMessageBox.warning(
                self, "服务启动未生效",
                "等待数秒后服务仍未进入「运行中」状态。\n\n"
                "常见原因:\n"
                "• 服务注册不完整 — 注册表缺少 PythonClass,pythonservice.exe 找不到服务类\n"
                "  (若服务曾由打包应用注册,需重新注册)\n"
                "• 服务进程启动后立即崩溃 — 查看: 事件查看器 → Windows 日志 → 系统\n"
                "• UAC 提权弹窗被取消\n\n"
                "建议:先点「卸载服务」,再点「注册为 Windows 服务」重新注册,然后再次启动。",
            )
        else:
            QMessageBox.warning(
                self, "服务停止未生效",
                "等待数秒后服务仍未停止。\n\n"
                "可稍后重试,或手动执行: sc stop ShineHeMCP",
            )

    # ---- MCP 配置档 ----

    def _on_mcp_profile_changed(self):
        """档位切换时刷新详情区"""
        key = self.mcp_profile_combo.currentData() or "extended"
        info = self._profile_info.get(key)
        if not info:
            return
        self._mcp_summary_label.setText(info["summary"])
        self._mcp_scope_label.setText(info["scope"])
        self._mcp_usecase_label.setText(info["use_case"])
        self._mcp_writes_label.setText(info["writes"])
