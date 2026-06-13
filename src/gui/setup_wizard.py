"""首次启动配置向导 — 4 步引导新用户完成 AI 服务配置

步骤:
1. 欢迎 — 项目简介、功能预览
2. AI 服务商 — 预设模板选择 + API Key 输入
3. 连通性测试 — 验证 Embedding API 可用性
4. 完成 — 配置摘要 + 可选导入示例知识包
"""
import logging

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QFont, QPixmap, QIcon
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QComboBox, QWidget, QStackedWidget,
    QProgressBar, QTextEdit, QMessageBox, QCheckBox,
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
                latency = resp.usage.total_tokens if resp.usage else 0
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

        title = QLabel("选择 AI 服务商")
        title.setObjectName("wizardPageTitle")
        lay.addWidget(title)

        desc = QLabel("选择你已有 API Key 的服务商，我们将自动填充配置。")
        desc.setWordWrap(True)
        desc.setObjectName("hintLabel")
        lay.addWidget(desc)

        lay.addSpacing(8)

        # 服务商选择
        form = QVBoxLayout()
        form.setSpacing(10)

        prov_row = QHBoxLayout()
        prov_label = QLabel("服务商：")
        prov_label.setFixedWidth(90)
        self._provider_combo = QComboBox()
        self._provider_combo.addItems(PROVIDER_PRESETS.keys())
        self._provider_combo.currentTextChanged.connect(self._on_provider_changed)
        prov_row.addWidget(prov_label)
        prov_row.addWidget(self._provider_combo, 1)
        form.addLayout(prov_row)

        # API Key
        key_row = QHBoxLayout()
        key_label = QLabel("API Key：")
        key_label.setFixedWidth(90)
        self._api_key_input = QLineEdit()
        self._api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_key_input.setPlaceholderText("sk-...")
        self._show_key_cb = QCheckBox("显示")
        self._show_key_cb.setFixedWidth(56)
        self._show_key_cb.toggled.connect(
            lambda on: self._api_key_input.setEchoMode(
                QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password
            )
        )
        key_row.addWidget(key_label)
        key_row.addWidget(self._api_key_input, 1)
        key_row.addWidget(self._show_key_cb)
        form.addLayout(key_row)

        # API Base URL（可编辑）
        url_row = QHBoxLayout()
        url_label = QLabel("API 地址：")
        url_label.setFixedWidth(90)
        self._base_url_input = QLineEdit()
        self._base_url_input.setPlaceholderText("https://api.example.com/v1")
        url_row.addWidget(url_label)
        url_row.addWidget(self._base_url_input, 1)
        form.addLayout(url_row)

        # Embedding 模型
        emb_row = QHBoxLayout()
        emb_label = QLabel("Embedding：")
        emb_label.setFixedWidth(90)
        self._emb_model_input = QLineEdit()
        self._emb_model_input.setPlaceholderText("用于文本向量化的模型")
        emb_row.addWidget(emb_label)
        emb_row.addWidget(self._emb_model_input, 1)
        form.addLayout(emb_row)

        # LLM 模型
        llm_row = QHBoxLayout()
        llm_label = QLabel("LLM 模型：")
        llm_label.setFixedWidth(90)
        self._llm_model_input = QLineEdit()
        self._llm_model_input.setPlaceholderText("用于问答和知识处理的模型")
        llm_row.addWidget(llm_label)
        llm_row.addWidget(self._llm_model_input, 1)
        form.addLayout(llm_row)

        lay.addLayout(form)

        lay.addSpacing(8)
        hint = QLabel(
            "💡 提示：如果你还没有 API Key，可以前往服务商官网免费注册。\n"
            "   硅基流动 (siliconflow.cn) 和智谱 (bigmodel.cn) 均提供免费额度。"
        )
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        lay.addStretch(1)
        return page

    # ---- Page 2: 连通性测试 ----

    def _build_test_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(40, 32, 40, 16)
        lay.setSpacing(14)

        title = QLabel("连通性测试")
        title.setObjectName("wizardPageTitle")
        lay.addWidget(title)

        desc = QLabel("验证你的 API Key 和模型配置是否正确可用。")
        desc.setWordWrap(True)
        desc.setObjectName("hintLabel")
        lay.addWidget(desc)

        lay.addSpacing(16)

        self._test_btn = QPushButton("🔍 开始测试")
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
            if not self._base_url_input.text().strip():
                QMessageBox.warning(self, "提示", "请填写 API 地址或选择服务商")
                return
            # 如果没有填写 embedding 模型，给出提示但允许继续
            if not self._emb_model_input.text().strip():
                reply = QMessageBox.question(
                    self, "提示",
                    "未填写 Embedding 模型，RAG 检索将不可用。\n是否继续？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if reply == QMessageBox.StandardButton.No:
                    return

        if idx < self._stack.count() - 1:
            self._go_to_page(idx + 1)

    # ---- 服务商选择 ----

    def _on_provider_changed(self, name: str):
        preset = PROVIDER_PRESETS.get(name, {})
        self._base_url_input.setText(preset.get("llm_base_url", ""))
        self._emb_model_input.setText(preset.get("embedding_model", ""))
        self._llm_model_input.setText(preset.get("llm_model", ""))

        # 更新 API Key 占位符
        placeholder = preset.get("api_key_placeholder", "sk-...")
        self._api_key_input.setPlaceholderText(placeholder)

    # ---- 连通性测试 ----

    def _run_connectivity_test(self):
        base_url = self._base_url_input.text().strip()
        api_key = self._api_key_input.text().strip()
        model = self._emb_model_input.text().strip()

        if not base_url:
            self._test_result.setText("❌ 请先填写 API 地址")
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

    def _build_summary(self):
        provider = self._provider_combo.currentText()
        api_key = self._api_key_input.text().strip()
        base_url = self._base_url_input.text().strip()
        emb_model = self._emb_model_input.text().strip()
        llm_model = self._llm_model_input.text().strip()

        key_display = f"{api_key[:8]}...{api_key[-4:]}" if len(api_key) > 12 else "(未设置)"

        self._summary.setHtml(f"""
        <table style="width:100%; font-size:13px;">
          <tr><td style="padding:4px; color:gray;">服务商</td>
              <td style="padding:4px;"><b>{provider}</b></td></tr>
          <tr><td style="padding:4px; color:gray;">API Key</td>
              <td style="padding:4px;">{key_display}</td></tr>
          <tr><td style="padding:4px; color:gray;">API 地址</td>
              <td style="padding:4px;">{base_url}</td></tr>
          <tr><td style="padding:4px; color:gray;">Embedding 模型</td>
              <td style="padding:4px;">{emb_model or '(未设置)'}</td></tr>
          <tr><td style="padding:4px; color:gray;">LLM 模型</td>
              <td style="padding:4px;">{llm_model or '(未设置)'}</td></tr>
        </table>
        """)

    def _on_finish(self):
        """保存配置并关闭向导"""
        try:
            api_key = self._api_key_input.text().strip()
            base_url = self._base_url_input.text().strip()
            emb_model = self._emb_model_input.text().strip()
            llm_model = self._llm_model_input.text().strip()

            # 获取当前 provider 对应的 preset
            provider_name = self._provider_combo.currentText()
            preset = PROVIDER_PRESETS.get(provider_name, {})

            # 设置 LLM 配置
            Config.set("llm.base_url", base_url)
            Config.set("llm.model", llm_model or preset.get("llm_model", ""))
            Config.set("llm.provider", provider_name)
            if api_key:
                Config.set("llm.api_key", api_key)

            # 设置 Embedding 配置
            emb_base_url = preset.get("embedding_base_url", base_url)
            Config.set("embedding.base_url", emb_base_url)
            Config.set("embedding.model", emb_model or preset.get("embedding_model", ""))
            Config.set("embedding.provider", provider_name)
            if api_key and emb_base_url == base_url:
                # 同服务商复用同一个 key
                Config.set("embedding.api_key", api_key)

            # 设置 Reranker 配置（如果 preset 有）
            reranker_url = preset.get("reranker_base_url")
            reranker_model = preset.get("reranker_model")
            if reranker_url:
                Config.set("reranker.base_url", reranker_url)
                Config.set("reranker.model", reranker_model)
                Config.set("reranker.enabled", True)
                if api_key and reranker_url == base_url:
                    Config.set("reranker.api_key", api_key)

            # Embedding reuse_llm 根据是否同服务商自动设置
            emb_url = Config.get("embedding.base_url", "")
            Config.set("embedding.reuse_llm", emb_url == base_url)

            # 持久化（API Key 通过 keyring 安全存储）
            Config.save()

            logger.info("Setup Wizard 配置保存成功")
            self.accept()

        except Exception as exc:
            logger.exception("Setup Wizard 保存配置失败")
            QMessageBox.critical(self, "保存失败", f"配置保存出错：\n{exc}")

    def get_import_samples(self) -> bool:
        """用户是否选择导入示例知识包"""
        return self._import_samples_cb.isChecked()

    # ---- 清理 ----

    def reject(self):
        """用户跳过或关闭向导"""
        if self._worker and self._worker.isRunning():
            self._worker.terminate()
            self._worker.wait(2000)
        super().reject()
