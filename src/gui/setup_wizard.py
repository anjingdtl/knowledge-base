"""首次启动配置向导 — 4 步引导新用户完成独立的 AI 模型配置

步骤:
1. 欢迎 — 项目简介、功能预览
2. AI 模型配置 — 分别选择 LLM、RAG 向量与重排序服务商
3. 连通性测试 — 可选验证 RAG Embedding API 可用性
4. 完成 — 配置摘要 + 可选导入示例知识包
"""
import logging

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.core.provider_presets import PROVIDER_PRESETS as _core_presets
from src.utils.config import Config
from src.version import APP_NAME, VERSION

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 预设服务商模板（从 src.core.provider_presets 转换，GUI 兼容格式）
# ---------------------------------------------------------------------------
def _gui_presets() -> dict:
    """将核心 ProviderPreset 转换为 GUI 使用的扁平字典格式

    返回以显示名为键的字典，值包含 embedding/llm/reranker 配置，
    保持与原有 GUI 代码完全兼容的格式。
    """
    result = {}
    for p in _core_presets.values():
        entry: dict = {
            "embedding_base_url": p.embedding_base_url,
            "embedding_model": p.embedding_model,
            "llm_base_url": p.llm_base_url,
            "llm_model": p.llm_model,
        }
        if p.reranker_base_url:
            entry["reranker_base_url"] = p.reranker_base_url
        if p.reranker_model:
            entry["reranker_model"] = p.reranker_model
        if p.api_key_placeholder is not None:
            entry["api_key_placeholder"] = p.api_key_placeholder
        result[p.display_name] = entry
    return result


PROVIDER_PRESETS = _gui_presets()


# ---------------------------------------------------------------------------
# 连通性测试线程
# ---------------------------------------------------------------------------
class _ConnectivityWorker(QThread):
    """后台测试 Embedding API 连通性"""
    finished = Signal(bool, str)  # (success, message)

    def __init__(self, base_url: str, api_key: str, model: str):
        super().__init__()
        self._base_url = base_url
        self._api_key = api_key
        self._model = model

    def run(self):
        try:
            import openai
            client = openai.OpenAI(
                base_url=self._base_url,
                api_key=self._api_key or "sk-placeholder",
                timeout=8.0,
            )
            resp = client.embeddings.create(
                model=self._model,
                input=["ShineHeKnowledge 连通性测试"],
            )
            if resp.data and len(resp.data) > 0:
                self.finished.emit(True, f"连接成功！模型返回 {len(resp.data)} 个向量")
            else:
                self.finished.emit(False, "API 返回数据为空")
        except openai.APIConnectionError as e:
            self.finished.emit(False, f"连接失败：{e.__cause__ or e}")
        except openai.AuthenticationError:
            self.finished.emit(False, "认证失败：API Key 无效或已过期")
        except openai.NotFoundError:
            self.finished.emit(False, f"模型 '{self._model}' 不存在或无权访问")
        except Exception as e:
            self.finished.emit(False, f"测试出错：{type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# SetupWizard 主对话框
# ---------------------------------------------------------------------------
class SetupWizard(QDialog):
    """首次启动配置向导"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker = None
        self.setWindowTitle(f"{APP_NAME} — 初始配置")
        self.setMinimumSize(640, 520)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)
        self._build_ui()
        self._go_to_page(0)

    # ---- UI 构建 ----

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 页面堆栈
        self._stack = QStackedWidget()
        layout.addWidget(self._stack, 1)

        # 底部导航栏
        nav = QHBoxLayout()
        nav.setContentsMargins(24, 12, 24, 18)
        nav.setSpacing(12)

        self._btn_prev = QPushButton("← 上一步")
        self._btn_prev.setObjectName("wizardNavBtn")
        self._btn_prev.clicked.connect(self._prev_page)

        self._btn_next = QPushButton("下一步 →")
        self._btn_next.setObjectName("wizardNavBtn")
        self._btn_next.clicked.connect(self._next_page)

        self._btn_finish = QPushButton("✓ 完成配置")
        self._btn_finish.setObjectName("wizardFinishBtn")
        self._btn_finish.clicked.connect(self._on_finish)

        self._btn_skip = QPushButton("跳过，稍后配置")
        self._btn_skip.setObjectName("wizardSkipBtn")
        self._btn_skip.setFlat(True)
        self._btn_skip.clicked.connect(self.reject)

        nav.addWidget(self._btn_skip)
        nav.addStretch(1)
        nav.addWidget(self._btn_prev)
        nav.addWidget(self._btn_next)
        nav.addWidget(self._btn_finish)
        layout.addLayout(nav)

        # 构建各页面
        self._stack.addWidget(self._build_welcome_page())
        self._stack.addWidget(self._build_provider_page())
        self._stack.addWidget(self._build_test_page())
        self._stack.addWidget(self._build_done_page())

    def _make_page(self) -> QVBoxLayout:
        """创建带统一内边距的页面布局"""
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(40, 32, 40, 16)
        lay.setSpacing(14)
        self._stack.addWidget(page)
        return lay

    # ---- Page 0: 欢迎 ----

    def _build_welcome_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(40, 40, 40, 16)
        lay.setSpacing(16)

        # 标题
        title = QLabel(f"欢迎使用 {APP_NAME}")
        title.setObjectName("wizardTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(title)

        ver = QLabel(f"v{VERSION}")
        ver.setObjectName("wizardSubtitle")
        ver.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(ver)

        lay.addSpacing(16)

        # 功能亮点
        features = [
            ("📚", "本地优先知识库", "完全私有化部署，数据不离开你的电脑"),
            ("🔍", "RAG 智能问答", "结合向量搜索与 LLM，精准回答你的问题"),
            ("🔗", "知识图谱", "自动构建文档关系网络，可视化知识关联"),
            ("🤖", "MCP 协议集成", "Claude / Cursor / Cline 等工具一键连接"),
            ("🌐", "多模态文档支持", "PDF、Word、Excel、Markdown 一键导入"),
        ]
        for emoji, name, desc in features:
            row = QHBoxLayout()
            row.setSpacing(10)
            icon_label = QLabel(emoji)
            icon_label.setFixedSize(32, 32)
            icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            icon_label.setObjectName("wizardFeatureIcon")
            name_label = QLabel(f"<b>{name}</b><br><span style='color: gray; font-size: 12px'>{desc}</span>")
            name_label.setWordWrap(True)
            row.addWidget(icon_label)
            row.addWidget(name_label, 1)
            lay.addLayout(row)

        lay.addStretch(1)

        hint = QLabel("接下来将引导你完成 AI 服务配置，仅需 1 分钟。")
        hint.setObjectName("hintLabel")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setWordWrap(True)
        lay.addWidget(hint)

        return page

    # ---- Page 1: AI 服务商 ----

    def _build_provider_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(40, 32, 40, 16)
        lay.setSpacing(14)

        title = QLabel("配置 AI 模型服务")
        title.setObjectName("wizardPageTitle")
        lay.addWidget(title)

        desc = QLabel(
            "LLM、RAG 向量模型和重排序模型分别配置。它们可以使用不同的服务商；"
            "RAG 与重排序均可暂不配置。"
        )
        desc.setWordWrap(True)
        desc.setObjectName("hintLabel")
        lay.addWidget(desc)

        lay.addSpacing(8)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        form = QVBoxLayout(content)
        form.setContentsMargins(0, 0, 8, 0)
        form.setSpacing(10)

        self._add_service_config(
            form,
            role="llm",
            title="LLM（问答与知识处理）",
            model_label="LLM 模型：",
            model_placeholder="例如：gpt-4o-mini、deepseek-chat",
            optional=False,
        )
        self._add_service_config(
            form,
            role="rag",
            title="RAG 向量模型（可选）",
            model_label="Embedding 模型：",
            model_placeholder="例如：text-embedding-3-small、BAAI/bge-m3",
            optional=True,
        )
        self._add_service_config(
            form,
            role="reranker",
            title="重排序模型（可选）",
            model_label="重排序模型：",
            model_placeholder="例如：BAAI/bge-reranker-v2-m3",
            optional=True,
            with_enabled_switch=True,
        )
        form.addStretch(1)
        scroll.setWidget(content)
        lay.addWidget(scroll, 1)

        lay.addSpacing(8)
        hint = QLabel(
            "💡 提示：如果你还没有 API Key，可以前往服务商官网免费注册。\n"
            "   硅基流动 (siliconflow.cn) 和智谱 (bigmodel.cn) 均提供免费额度。"
        )
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        return page

    def _add_service_config(
        self,
        layout: QVBoxLayout,
        *,
        role: str,
        title: str,
        model_label: str,
        model_placeholder: str,
        optional: bool,
        with_enabled_switch: bool = False,
    ) -> None:
        """添加一组独立的模型服务商配置控件。"""
        group = QGroupBox(title)
        form = QFormLayout(group)
        form.setSpacing(8)

        if with_enabled_switch:
            enabled = QCheckBox("启用专用重排序模型")
            enabled.setChecked(False)
            enabled.toggled.connect(
                lambda checked, service_role=role: self._set_service_fields_enabled(
                    service_role, checked
                )
            )
            setattr(self, f"_{role}_enabled_cb", enabled)
            form.addRow(enabled)

        provider = QComboBox()
        provider.addItem("暂不配置（可稍后设置）" if optional else "请选择服务商")
        provider.addItems(list(PROVIDER_PRESETS))
        provider.currentTextChanged.connect(
            lambda name, service_role=role: self._on_service_provider_changed(
                service_role, name
            )
        )

        api_key = QLineEdit()
        api_key.setEchoMode(QLineEdit.EchoMode.Password)
        api_key.setPlaceholderText("sk-...（此服务商专用）")
        show_key = QCheckBox("显示")
        show_key.toggled.connect(
            lambda on, field=api_key: field.setEchoMode(
                QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password
            )
        )
        key_row = QHBoxLayout()
        key_row.addWidget(api_key, 1)
        key_row.addWidget(show_key)

        base_url = QLineEdit()
        base_url.setPlaceholderText("https://api.example.com/v1")
        model = QLineEdit()
        model.setPlaceholderText(model_placeholder)

        for field_name, field in {
            "provider_combo": provider,
            "api_key_input": api_key,
            "show_key_cb": show_key,
            "base_url_input": base_url,
            "model_input": model,
        }.items():
            setattr(self, f"_{role}_{field_name}", field)

        form.addRow("服务商：", provider)
        form.addRow("API Key：", key_row)
        form.addRow("API 地址：", base_url)
        form.addRow(model_label, model)
        layout.addWidget(group)

        if with_enabled_switch:
            self._set_service_fields_enabled(role, False)

    def _set_service_fields_enabled(self, role: str, enabled: bool) -> None:
        """禁用可选服务的输入，避免未启用时误以为会保存。"""
        for suffix in (
            "provider_combo",
            "api_key_input",
            "show_key_cb",
            "base_url_input",
            "model_input",
        ):
            getattr(self, f"_{role}_{suffix}").setEnabled(enabled)

    # ---- Page 2: 连通性测试 ----

    def _build_test_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(40, 32, 40, 16)
        lay.setSpacing(14)

        title = QLabel("RAG 向量模型连通性测试（可选）")
        title.setObjectName("wizardPageTitle")
        lay.addWidget(title)

        desc = QLabel("验证 RAG 向量模型的 API Key、地址和模型是否可用。未配置 RAG 时可直接跳过。")
        desc.setWordWrap(True)
        desc.setObjectName("hintLabel")
        lay.addWidget(desc)

        lay.addSpacing(16)

        self._test_btn = QPushButton("🔍 测试 RAG 向量模型")
        self._test_btn.setObjectName("wizardTestBtn")
        self._test_btn.setFixedHeight(42)
        self._test_btn.clicked.connect(self._run_connectivity_test)
        lay.addWidget(self._test_btn)

        self._test_progress = QProgressBar()
        self._test_progress.setRange(0, 0)  # 不确定进度
        self._test_progress.setVisible(False)
        lay.addWidget(self._test_progress)

        self._test_result = QLabel("")
        self._test_result.setWordWrap(True)
        self._test_result.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._test_result.setMinimumHeight(60)
        lay.addWidget(self._test_result)

        lay.addStretch(1)

        skip_hint = QLabel("测试失败不影响继续，你可以在设置中稍后修改。")
        skip_hint.setObjectName("hintLabel")
        skip_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(skip_hint)

        return page

    # ---- Page 3: 完成 ----

    def _build_done_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(40, 32, 40, 16)
        lay.setSpacing(14)

        title = QLabel("🎉 配置完成！")
        title.setObjectName("wizardPageTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(title)

        desc = QLabel("以下是你的配置摘要，点击「完成配置」开始使用。")
        desc.setWordWrap(True)
        desc.setObjectName("hintLabel")
        desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(desc)

        lay.addSpacing(12)

        self._summary = QTextEdit()
        self._summary.setReadOnly(True)
        self._summary.setObjectName("wizardSummary")
        self._summary.setMaximumHeight(180)
        lay.addWidget(self._summary)

        lay.addSpacing(8)

        self._import_samples_cb = QCheckBox("导入示例知识包（5 篇入门文档）")
        self._import_samples_cb.setChecked(True)
        lay.addWidget(self._import_samples_cb)

        lay.addStretch(1)
        return page

    # ---- 导航逻辑 ----

    def _go_to_page(self, index: int):
        self._stack.setCurrentIndex(index)
        total = self._stack.count()
        self._btn_prev.setVisible(index > 0)
        self._btn_next.setVisible(index < total - 1)
        self._btn_finish.setVisible(index == total - 1)

        # 进入测试页时自动触发
        if index == 2:
            self._test_result.setText("")
            self._test_progress.setVisible(False)

        # 进入完成页时生成摘要
        if index == 3:
            self._build_summary()

    def _prev_page(self):
        idx = self._stack.currentIndex()
        if idx > 0:
            self._go_to_page(idx - 1)

    def _next_page(self):
        idx = self._stack.currentIndex()

        # 从 provider 页面进入时，校验必填
        if idx == 1:
            if not self._llm_base_url_input.text().strip() or not self._llm_model_input.text().strip():
                QMessageBox.warning(self, "提示", "请为 LLM 选择服务商，并填写 API 地址和模型。")
                return
            if self._rag_provider_combo.currentIndex() > 0 and (
                not self._rag_base_url_input.text().strip()
                or not self._rag_model_input.text().strip()
            ):
                QMessageBox.warning(self, "提示", "已选择 RAG 服务商，请填写 API 地址和 Embedding 模型。")
                return
            if self._reranker_enabled_cb.isChecked() and (
                self._reranker_provider_combo.currentIndex() == 0
                or not self._reranker_base_url_input.text().strip()
                or not self._reranker_model_input.text().strip()
            ):
                QMessageBox.warning(self, "提示", "已启用重排序，请选择服务商并填写 API 地址和模型。")
                return

        if idx < self._stack.count() - 1:
            self._go_to_page(idx + 1)

    # ---- 服务商选择 ----

    def _on_service_provider_changed(self, role: str, name: str):
        """只填充当前服务的预设，绝不修改其他模型配置。"""
        preset = PROVIDER_PRESETS.get(name, {})
        if role == "llm":
            base_url = preset.get("llm_base_url", "")
            model = preset.get("llm_model", "")
        elif role == "rag":
            base_url = preset.get("embedding_base_url", "")
            model = preset.get("embedding_model", "")
        else:
            base_url = preset.get("reranker_base_url", "")
            model = preset.get("reranker_model", "")

        getattr(self, f"_{role}_base_url_input").setText(base_url)
        getattr(self, f"_{role}_model_input").setText(model)
        placeholder = preset.get("api_key_placeholder", "sk-...")
        getattr(self, f"_{role}_api_key_input").setPlaceholderText(
            f"{placeholder}（此服务商专用）"
        )

    # ---- 连通性测试 ----

    def _run_connectivity_test(self):
        base_url = self._rag_base_url_input.text().strip()
        api_key = self._rag_api_key_input.text().strip()
        model = self._rag_model_input.text().strip()

        if not base_url:
            self._test_result.setText("⚠️ 未配置 RAG 向量模型，跳过测试")
            return
        if not model:
            self._test_result.setText("⚠️ 未配置 Embedding 模型，跳过测试")
            return

        self._test_btn.setEnabled(False)
        self._test_progress.setVisible(True)
        self._test_result.setText("正在连接...")

        self._worker = _ConnectivityWorker(base_url, api_key, model)
        self._worker.finished.connect(self._on_test_done)
        self._worker.start()

    def _on_test_done(self, success: bool, message: str):
        self._test_btn.setEnabled(True)
        self._test_progress.setVisible(False)
        if success:
            self._test_result.setText(f"✅ {message}")
        else:
            self._test_result.setText(f"❌ {message}")

    # ---- 完成并保存配置 ----

    def _service_values(self, role: str) -> dict[str, str]:
        """读取一组服务配置，保持三类模型完全隔离。"""
        return {
            "provider": getattr(self, f"_{role}_provider_combo").currentText(),
            "api_key": getattr(self, f"_{role}_api_key_input").text().strip(),
            "base_url": getattr(self, f"_{role}_base_url_input").text().strip(),
            "model": getattr(self, f"_{role}_model_input").text().strip(),
        }

    @staticmethod
    def _key_display(api_key: str) -> str:
        return f"{api_key[:8]}...{api_key[-4:]}" if len(api_key) > 12 else "(未设置)"

    @staticmethod
    def _value_or_unconfigured(value: str, *, is_configured: bool = True) -> str:
        return value if is_configured and value else "(未配置)"

    def _build_summary(self):
        llm = self._service_values("llm")
        rag = self._service_values("rag")
        reranker = self._service_values("reranker")
        rag_configured = self._rag_provider_combo.currentIndex() > 0
        reranker_configured = self._reranker_enabled_cb.isChecked()

        self._summary.setHtml(f"""
        <table style="width:100%; font-size:13px;">
          <tr><td style="padding:4px; color:gray;">LLM 服务商</td>
              <td style="padding:4px;"><b>{llm['provider']}</b></td></tr>
          <tr><td style="padding:4px; color:gray;">LLM 模型 / Key</td>
              <td style="padding:4px;">{llm['model']} / {self._key_display(llm['api_key'])}</td></tr>
          <tr><td style="padding:4px; color:gray;">RAG 服务商</td>
              <td style="padding:4px;">{self._value_or_unconfigured(rag['provider'], is_configured=rag_configured)}</td></tr>
          <tr><td style="padding:4px; color:gray;">Embedding 模型 / Key</td>
              <td style="padding:4px;">{self._value_or_unconfigured(rag['model'], is_configured=rag_configured)} / {self._key_display(rag['api_key'])}</td></tr>
          <tr><td style="padding:4px; color:gray;">重排序服务商</td>
              <td style="padding:4px;">{self._value_or_unconfigured(reranker['provider'], is_configured=reranker_configured)}</td></tr>
          <tr><td style="padding:4px; color:gray;">重排序模型 / Key</td>
              <td style="padding:4px;">{self._value_or_unconfigured(reranker['model'], is_configured=reranker_configured)} / {self._key_display(reranker['api_key'])}</td></tr>
        </table>
        """)

    def _on_finish(self):
        """保存配置并关闭向导"""
        try:
            llm = self._service_values("llm")
            rag = self._service_values("rag")
            reranker = self._service_values("reranker")
            rag_configured = self._rag_provider_combo.currentIndex() > 0
            reranker_enabled = self._reranker_enabled_cb.isChecked()

            # 每个模型组独立保存，绝不从 LLM 自动继承供应商、地址或 API Key。
            Config.set("llm.provider", llm["provider"])
            Config.set("llm.base_url", llm["base_url"])
            Config.set("llm.model", llm["model"])
            Config.set("llm.api_key", llm["api_key"])

            Config.set("embedding.reuse_llm", False)
            Config.set("embedding.provider", rag["provider"] if rag_configured else "")
            Config.set("embedding.base_url", rag["base_url"] if rag_configured else "")
            Config.set("embedding.model", rag["model"] if rag_configured else "")
            Config.set("embedding.api_key", rag["api_key"] if rag_configured else "")

            Config.set("reranker.enabled", reranker_enabled)
            Config.set("reranker.reuse_llm", False)
            # 工厂以 provider=api 识别兼容 API；显示名称仅用于本向导的选择与摘要。
            Config.set("reranker.provider", "api" if reranker_enabled else "disabled")
            Config.set("reranker.base_url", reranker["base_url"] if reranker_enabled else "")
            Config.set("reranker.model", reranker["model"] if reranker_enabled else "")
            Config.set("reranker.api_key", reranker["api_key"] if reranker_enabled else "")

            # 持久化（API Key 通过 keyring + DPAPI 安全存储）
            Config.save()

            logger.info("Setup Wizard 配置保存成功")

            # 如果 Windows 服务已注册且正在运行，提示重启以加载新 Key
            try:
                from src.services.mcp_launcher import get_service_status, is_service_installed
                if is_service_installed() and get_service_status() == "running":
                    reply = QMessageBox.question(
                        self, "初始配置已完成",
                        "初始配置已保存！\n\n"
                        "检测到 Windows MCP 服务正在运行。\n"
                        "需要重启服务才能加载新配置的 API Key。\n\n"
                        "是否立即重启服务？",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                        QMessageBox.StandardButton.Yes,
                    )
                    if reply == QMessageBox.StandardButton.Yes:
                        # 延迟关闭向导，先让用户看到重启操作
                        from src.services.mcp_launcher import service_restart
                        msg = service_restart()
                        if "UAC" in msg:
                            QMessageBox.information(
                                self, "服务重启",
                                msg + "\n\n请先在 UAC 弹窗中确认...",
                            )
                        else:
                            QMessageBox.information(self, "服务重启", msg)
            except Exception:
                pass  # 服务检测失败不阻断向导流程

            self.accept()

        except Exception as exc:
            logger.exception("Setup Wizard 保存配置失败")
            QMessageBox.critical(self, "保存失败", f"配置保存出错：\n{exc}")

    def get_import_samples(self) -> bool:
        """用户是否选择导入示例知识包"""
        return bool(self._import_samples_cb.isChecked())

    # ---- 清理 ----

    def reject(self):
        """用户跳过或关闭向导"""
        if self._worker and self._worker.isRunning():
            self._worker.terminate()
            self._worker.wait(2000)
        super().reject()
