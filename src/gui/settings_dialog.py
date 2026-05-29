"""设置对话框"""
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLineEdit, QSpinBox, QPushButton, QMessageBox,
    QTabWidget, QWidget, QLabel, QCheckBox, QComboBox,
)
from src.gui.icons import set_named_icon
from src.utils.config import Config


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setMinimumWidth(560)
        self.setMinimumHeight(520)
        self._setup_ui()
        self._load_values()

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

    def _save(self):
        if not self.llm_provider.text().strip() or not self.llm_base_url.text().strip():
            QMessageBox.warning(self, "提示", "请至少填写 LLM 的供应商名称和 API 地址。")
            return

        Config.set("llm.provider", self.llm_provider.text().strip())
        Config.set("llm.api_key", self.llm_api_key.text().strip())
        Config.set("llm.base_url", self.llm_base_url.text().strip())
        Config.set("llm.model", self.llm_model.text().strip())
        Config.set("llm.temperature", self.llm_temperature.value() / 100)
        Config.set("llm.max_tokens", self.llm_max_tokens.value())

        reuse = self.emb_reuse_llm.isChecked()
        Config.set("embedding.reuse_llm", reuse)
        if reuse:
            Config.set("embedding.provider", self.emb_provider.text().strip() or self.llm_provider.text().strip())
            Config.set("embedding.api_key", self.emb_api_key.text().strip() or self.llm_api_key.text().strip())
            Config.set("embedding.base_url", self.emb_base_url.text().strip() or self.llm_base_url.text().strip())
        else:
            Config.set("embedding.provider", self.emb_provider.text().strip())
            Config.set("embedding.api_key", self.emb_api_key.text().strip())
            Config.set("embedding.base_url", self.emb_base_url.text().strip())
        Config.set("embedding.model", self.emb_model.text().strip())

        # 保存 Reranker 配置
        Config.set("reranker.enabled", self.rerank_enabled.isChecked())
        Config.set("reranker.use_llm_fallback", self.rerank_use_llm_fallback.isChecked())
        rerank_reuse = self.rerank_reuse_llm.isChecked()
        Config.set("reranker.reuse_llm", rerank_reuse)
        if rerank_reuse:
            Config.set("reranker.provider", self.rerank_provider.text().strip() or self.llm_provider.text().strip())
            Config.set("reranker.api_key", self.rerank_api_key.text().strip() or self.llm_api_key.text().strip())
            Config.set("reranker.base_url", self.rerank_base_url.text().strip() or self.llm_base_url.text().strip())
        else:
            Config.set("reranker.provider", self.rerank_provider.text().strip())
            Config.set("reranker.api_key", self.rerank_api_key.text().strip())
            Config.set("reranker.base_url", self.rerank_base_url.text().strip())
        Config.set("reranker.model", self.rerank_model.text().strip())

        Config.set("rag.top_k", self.rag_top_k.value())
        Config.set("rag.chunk_size", self.rag_chunk_size.value())
        Config.set("rag.chunk_overlap", self.rag_chunk_overlap.value())
        Config.set("rag.score_threshold", self.rag_score_threshold.value() / 100)

        Config.set("appearance.theme", self.theme_combo.currentData())
        Config.set("appearance.font_size", self.font_size.value())

        Config.save()

        # 立即应用主题
        from PySide6.QtWidgets import QApplication
        from src.gui.theme import apply
        apply(QApplication.instance())

        QMessageBox.information(self, "已保存", "设置已保存并生效。")
        self.accept()
