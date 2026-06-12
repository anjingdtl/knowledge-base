"""RAG 问答对话界面"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTextEdit,
    QPushButton, QListWidget, QListWidgetItem, QLabel,
    QSplitter, QMenu, QMessageBox, QStackedWidget, QFrame,
)
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QTextCursor
import html
import json

from src.services.db import Database
from src.services.rag import RAGService
from src.services.llm import _notify_status
from src.models.chat import Conversation, ChatMessage
from src.gui.icons import NAV, icon as make_icon, set_named_icon
from src.gui.theme import get_color
from src.utils.config import Config
from src.gui.empty_state import EmptyState


def _font_sm() -> int:
    return max(10, Config.get("appearance.font_size", 13) - 2)


class ChatWorker(QThread):
    chunk_received = Signal(str)
    phase_changed = Signal(str, str)
    finished = Signal(str, list, dict)
    error = Signal(str)

    def __init__(self, question: str, conversation_id: str, history: list):
        super().__init__()
        self.question = question
        self.conversation_id = conversation_id
        self.history = history
        self._rag = RAGService()

    def run(self):
        try:
            def on_phase(status, detail):
                self.phase_changed.emit(status, detail)
                _notify_status("running", detail)

            stream_result = self._rag.query_stream(
                self.question, self.history, phase_callback=on_phase,
            )
            if isinstance(stream_result, tuple) and len(stream_result) == 3:
                stream, sources, source_graph = stream_result
            else:
                stream, sources = stream_result
                source_graph = {"nodes": [], "edges": []}
            full_text = ""
            display_text = ""
            in_think = False

            for chunk in stream:
                full_text += chunk
                display_text += chunk

                while True:
                    if in_think:
                        # 查找 think 结束标签（多种格式）
                        for end_tag in ["</think>", "</thinking>"]:
                            end = display_text.find(end_tag)
                            if end != -1:
                                close = display_text.find(">", end)
                                if close != -1:
                                    display_text = display_text[close + 1:]
                                    in_think = False
                                    break
                        if in_think:
                            # think 未结束，清空显示缓冲区
                            display_text = ""
                            break
                    else:
                        # 查找 think 开始标签（多种格式）
                        for start_tag in ["<think>", "<thinking>"]:
                            start = display_text.find(start_tag)
                            if start != -1:
                                visible = display_text[:start]
                                if visible:
                                    self.chunk_received.emit(visible)
                                rest = display_text[start + len(start_tag):]
                                display_text = rest
                                in_think = True
                                break
                        if not in_think:
                            break

                if not in_think and display_text:
                    self.chunk_received.emit(display_text)
                    display_text = ""

            if not in_think and display_text:
                self.chunk_received.emit(display_text)

            self.finished.emit(full_text, sources, source_graph)
        except Exception as e:
            _notify_status("error", str(e)[:100])
            self.error.emit(str(e))
        finally:
            _notify_status("idle")


def _now_ts() -> str:
    from datetime import datetime
    return datetime.now().strftime("%H:%M")


def _user_bubble_html(content: str) -> str:
    text_color = get_color("text")
    dim = get_color("text_dim")
    bubble_bg = get_color("chat_user_bubble")
    ts = _now_ts()
    return (
        f'<div style="padding:10px 14px;margin:6px 40px 6px 0;'
        f'line-height:1.6;border-radius:8px;'
        f'background:{bubble_bg};border:1px solid {get_color("accent")}30;">'
        f'<span style="color:{dim};font-size:{_font_sm()}px;">你</span>'
        f'<span style="color:{dim};font-size:{_font_sm() - 2}px;float:right;">{ts}</span><br>'
        f'<span style="color:{text_color};">{content}</span></div>'
    )


def _source_graph_summary(source_graph: dict | None) -> str:
    if not source_graph:
        return ""
    nodes = source_graph.get("nodes") or []
    edges = source_graph.get("edges") or []
    if not nodes and not edges:
        return ""
    labels = {node.get("id"): (node.get("label") or node.get("id") or "") for node in nodes}
    edge_samples = []
    for edge in edges[:3]:
        source = labels.get(edge.get("source"), edge.get("source", ""))
        target = labels.get(edge.get("target"), edge.get("target", ""))
        rel = edge.get("type") or "link"
        if source and target:
            edge_samples.append(f"{source} -> {target} ({rel})")
    summary = f"来源图谱: {len(nodes)} 个节点 / {len(edges)} 条关系"
    if edge_samples:
        summary += " · " + "；".join(edge_samples)
    return summary


def _ai_bubble_html(content: str, sources: list = None, source_graph: dict | None = None) -> str:
    text_color = get_color("text")
    dim = get_color("text_dim")
    bubble_bg = get_color("chat_ai_bubble")
    accent = get_color("accent")
    ts = _now_ts()
    sources_text = ""
    if sources:
        cards = []
        for s in sources[:5]:
            title = s.get("title", "未知")[:30]
            score = s.get("score", 0)
            cards.append(
                f'<span style="display:inline-block;padding:3px 10px;margin:2px 4px 2px 0;'
                f'background:{bubble_bg};border:1px solid {get_color("accent")}25;border-radius:6px;'
                f'font-size:{max(10, _font_sm() - 1)}px;color:{accent};">'
                f'{title} <span style="color:{dim};font-size:{max(9, _font_sm() - 2)}px;">({score:.0%})</span>'
                f'</span>'
            )
        sources_text = (
            f'<div style="margin-top:8px;line-height:2;">'
            f'<span style="color:{dim};font-size:{_font_sm()}px;">参考来源</span><br>'
            f'{"".join(cards)}'
            f'</div>'
        )
    graph_summary = _source_graph_summary(source_graph)
    graph_text = ""
    if graph_summary:
        graph_text = (
            f'<div style="margin-top:6px;color:{dim};font-size:{_font_sm()}px;">'
            f'{html.escape(graph_summary)}'
            f'</div>'
        )
    return (
        f'<div style="padding:10px 14px;margin:6px 0;'
        f'line-height:1.6;border-radius:8px;'
        f'background:{bubble_bg};border:1px solid {get_color("border")};">'
        f'<span style="color:{accent};font-size:{_font_sm()}px;">AI 助手</span>'
        f'<span style="color:{dim};font-size:{_font_sm() - 2}px;float:right;">{ts}</span><br>'
        f'<span style="color:{text_color};">{content}</span>'
        f'{sources_text}{graph_text}</div>'
    )


class ChatView(QWidget):
    def __init__(self, llm_indicator=None):
        self._last_ai_question = ""
        self._last_ai_sources: list = []
        self._last_ai_source_graph: dict = {"nodes": [], "edges": []}
        super().__init__()
        self._llm_indicator = llm_indicator
        self._current_conv_id = None
        self._worker = None
        self._setup_ui()
        # 对话列表首次加载延后到 showEvent：默认页是知识库，聊天页可能很久才切到
        self._conversations_loaded = False

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        self.setObjectName("pageSurface")
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        header_card = QFrame()
        header_card.setObjectName("pageHeader")
        header_layout = QHBoxLayout(header_card)
        header_layout.setContentsMargins(16, 12, 16, 12)
        title_col = QVBoxLayout()
        title = QLabel("智能问答")
        title.setObjectName("pageTitle")
        subtitle = QLabel("基于本地知识库进行检索增强问答，并可沉淀为 Wiki 页面")
        subtitle.setObjectName("pageSubtitle")
        title_col.addWidget(title)
        title_col.addWidget(subtitle)
        header_layout.addLayout(title_col)
        header_layout.addStretch()
        layout.addWidget(header_card)

        splitter = QSplitter(Qt.Horizontal)

        # 左侧：对话历史
        left = QFrame()
        left.setObjectName("sidePanel")
        left.setMaximumWidth(240)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(12, 12, 12, 12)
        left_layout.setSpacing(10)

        header = QLabel("对话历史")
        header.setObjectName("sectionLabel")
        left_layout.addWidget(header)

        self.conv_list = QListWidget()
        self.conv_list.currentItemChanged.connect(self._on_conv_selected)
        self.conv_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.conv_list.customContextMenuRequested.connect(self._conv_context_menu)
        left_layout.addWidget(self.conv_list)

        btn_new = QPushButton("新对话")
        btn_new.setObjectName("primaryBtn")
        set_named_icon(btn_new, "new", "on_accent", 15)
        btn_new.clicked.connect(self._new_conversation)
        left_layout.addWidget(btn_new)

        splitter.addWidget(left)
        left.setMinimumWidth(120)

        # 右侧：对话区
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(12, 0, 0, 0)
        right_layout.setSpacing(10)

        # 用 QStackedWidget 包裹聊天区和欢迎页
        self.chat_stack = QStackedWidget()

        self.chat_output = QTextEdit()
        self.chat_output.setObjectName("chatOutput")
        self.chat_output.setReadOnly(True)

        # 欢迎页容器（无对话时显示）
        welcome_container = QWidget()
        welcome_layout = QVBoxLayout(welcome_container)
        welcome_layout.setAlignment(Qt.AlignCenter)
        welcome_layout.setContentsMargins(40, 40, 40, 40)

        self.welcome_page = EmptyState(
            title="开始一段智能问答",
            description="基于知识库内容回答你的问题，回答会引用相关来源",
            icon_key="chat",
        )
        welcome_layout.addWidget(self.welcome_page)

        # 示例问题按钮区
        example_row = QHBoxLayout()
        example_row.setAlignment(Qt.AlignCenter)
        example_row.setSpacing(10)
        accent = get_color("accent")
        for question_text in [
            "知识库里有哪些管理制度？",
            "帮我总结最新的营销政策",
            "什么是全渠道运营？",
        ]:
            btn = QPushButton(question_text)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedHeight(32)
            btn.setStyleSheet(
                f"QPushButton {{"
                f"  background: transparent; color: {accent};"
                f"  border: 1px solid {accent}; border-radius: 6px;"
                f"  font-size: {_font_sm()}px; padding: 0 14px;"
                f"}}"
                f"QPushButton:hover {{ background: {get_color('accent_surface')}; }}"
            )
            btn.clicked.connect(lambda checked, t=question_text: self._fill_example_question(t))
            example_row.addWidget(btn)
        welcome_layout.addLayout(example_row)

        self.chat_stack.addWidget(self.chat_output)        # page 0
        self.chat_stack.addWidget(welcome_container)       # page 1

        right_layout.addWidget(self.chat_stack, 1)

        self.sources_label = QLabel("")
        self.sources_label.setWordWrap(True)
        self.sources_label.setObjectName("hintLabel")
        right_layout.addWidget(self.sources_label)

        input_row = QHBoxLayout()
        input_row.setSpacing(8)
        self.chat_input = QTextEdit()
        self.chat_input.setObjectName("chatInput")
        self.chat_input.setMaximumHeight(100)
        self.chat_input.setPlaceholderText("输入问题，Enter 发送，Shift+Enter 换行")
        self.chat_input.keyPressEvent = self._input_key_press
        input_row.addWidget(self.chat_input, 1)

        self.btn_send = QPushButton("发送")
        self.btn_send.setObjectName("primaryBtn")
        set_named_icon(self.btn_send, "send", "on_accent", 15)
        self.btn_send.clicked.connect(self._send_message)
        input_row.addWidget(self.btn_send)

        self.btn_save_wiki = QPushButton("保存到 Wiki")
        set_named_icon(self.btn_save_wiki, "save", "text_dim", 15)
        self.btn_save_wiki.setEnabled(False)
        self.btn_save_wiki.setMinimumWidth(100)
        self.btn_save_wiki.clicked.connect(self._save_to_wiki)
        input_row.addWidget(self.btn_save_wiki)

        right_layout.addLayout(input_row)
        splitter.addWidget(right)
        right.setMinimumWidth(400)

        splitter.setSizes([200, 900])
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)

    def _load_conversations(self):
        self.conv_list.clear()
        convs = Database.list_conversations()
        for conv in convs:
            item = QListWidgetItem(conv["title"] or "新对话")
            item.setData(Qt.UserRole, conv)
            self.conv_list.addItem(item)
        if convs:
            self.conv_list.setCurrentRow(0)
        else:
            self._show_welcome()

    def _show_welcome(self):
        """显示欢迎页"""
        self.chat_stack.setCurrentIndex(1)

    def _show_chat(self):
        """显示聊天区"""
        self.chat_stack.setCurrentIndex(0)

    def _fill_example_question(self, text: str, auto_send: bool = True):
        """将示例问题填入输入框，可选自动发送"""
        # 如果没有当前对话，先创建一个
        if not self._current_conv_id:
            self._new_conversation()
        self.chat_input.setPlainText(text)
        self.chat_input.setFocus()
        if auto_send:
            self._send_message()

    def _on_conv_selected(self, current: QListWidgetItem, previous: QListWidgetItem):
        if not current:
            return
        conv = current.data(Qt.UserRole)
        self._current_conv_id = conv["id"]
        self._display_messages()

    def _display_messages(self):
        self.chat_output.clear()
        self.sources_label.clear()
        if not self._current_conv_id:
            self._show_welcome()
            return
        messages = Database.get_messages(self._current_conv_id)
        if not messages:
            self._show_welcome()
            return
        self._show_chat()
        for msg in messages:
            role = msg["role"]
            content = self._display_content(role, msg["content"])
            if role == "user":
                self.chat_output.append(_user_bubble_html(self._escape(content)))
            else:
                sources = json.loads(msg.get("sources", "[]")) if isinstance(msg.get("sources"), str) else msg.get("sources", [])
                source_graph = msg.get("source_graph", {"nodes": [], "edges": []})
                if isinstance(source_graph, str):
                    try:
                        source_graph = json.loads(source_graph)
                    except (json.JSONDecodeError, ValueError):
                        source_graph = {"nodes": [], "edges": []}
                self.chat_output.append(_ai_bubble_html(self._escape(content), sources, source_graph))
        self.chat_output.moveCursor(QTextCursor.End)

    def showEvent(self, event):
        super().showEvent(event)
        if not self._conversations_loaded:
            self._load_conversations()
            self._conversations_loaded = True
        if self._worker and self._worker.isRunning():
            return
        if self._current_conv_id:
            self._display_messages()
        elif self.conv_list.count() > 0:
            self.conv_list.setCurrentRow(0)

    def _new_conversation(self):
        conv = Conversation(title="新对话")
        Database.insert_conversation(conv.to_row())
        self._current_conv_id = conv.id
        item = QListWidgetItem("新对话")
        item.setData(Qt.UserRole, {"id": conv.id, "title": conv.title, "created_at": conv.created_at})
        self.conv_list.insertItem(0, item)
        self.conv_list.setCurrentItem(item)
        self.chat_output.clear()
        self.sources_label.clear()
        self._show_chat()
        self.chat_input.setFocus()

    def _conv_context_menu(self, pos):
        item = self.conv_list.itemAt(pos)
        if not item:
            return
        menu = QMenu(self)
        action_rename = menu.addAction(make_icon(NAV["rename"]), "重命名")
        action_delete = menu.addAction(make_icon(NAV["delete"], "danger"), "删除")
        action = menu.exec(self.conv_list.mapToGlobal(pos))
        if action == action_rename:
            conv = item.data(Qt.UserRole)
            from PySide6.QtWidgets import QInputDialog
            text, ok = QInputDialog.getText(self, "重命名", "对话标题：", text=conv["title"])
            if ok:
                Database.get_conn().execute("UPDATE conversations SET title = ? WHERE id = ?", (text, conv["id"]))
                Database.get_conn().commit()
                conv["title"] = text
                item.setText(text)
        elif action == action_delete:
            conv = item.data(Qt.UserRole)
            reply = QMessageBox.question(self, "确认删除", "确定删除此对话？", QMessageBox.Yes | QMessageBox.No)
            if reply == QMessageBox.Yes:
                Database.delete_conversation(conv["id"])
                self._load_conversations()
                self.chat_output.clear()

    def _input_key_press(self, event):
        if event.key() == Qt.Key_Return and not event.modifiers() & Qt.ShiftModifier:
            self._send_message()
        else:
            from PySide6.QtGui import QKeyEvent
            QTextEdit.keyPressEvent(self.chat_input, event)

    def _send_message(self):
        if self._worker and self._worker.isRunning():
            return
        question = self.chat_input.toPlainText().strip()
        if not question:
            return
        if not self._current_conv_id:
            self._new_conversation()

        self._last_user_question = question
        self.btn_save_wiki.setEnabled(False)

        history = self._get_history()

        user_msg = ChatMessage(
            conversation_id=self._current_conv_id,
            role="user",
            content=question,
        )
        Database.insert_message(user_msg.to_row())

        self.chat_output.append(_user_bubble_html(self._escape(question)))

        # 思考中动画
        self._thinking_dots = 0
        self._thinking_timer = self  # 存活标记
        from PySide6.QtCore import QTimer
        self._think_timer = QTimer(self)
        self._think_timer.timeout.connect(self._tick_thinking)
        self._think_timer.start(400)
        self._thinking_anchor = self.chat_output.toHtml().count("<")
        self.chat_output.append(
            '<div id="thinking" style="padding:10px 14px;margin:6px 0;'
            'line-height:1.6;border-radius:8px;'
            f'background:{get_color("chat_ai_bubble")};border:1px solid {get_color("border")};">'
            f'<span style="color:{get_color("accent")};font-size:{_font_sm()}px;">AI 助手</span><br>'
            f'<span style="color:{get_color("typing_dots")};">思考中 ···</span></div>'
        )

        self.chat_input.clear()
        self.btn_send.setEnabled(False)

        self._worker = ChatWorker(question, self._current_conv_id, history)
        self._worker.phase_changed.connect(self._on_phase)
        self._worker.chunk_received.connect(self._on_chunk)
        self._worker.finished.connect(self._on_response_finished)
        self._worker.error.connect(self._on_worker_error)
        self._worker.start()

        if self._current_conv_id:
            Database.get_conn().execute(
                "UPDATE conversations SET title = ? WHERE id = ? AND title = ?",
                (question[:30], self._current_conv_id, '新对话'),
            )
            Database.get_conn().commit()
            current_item = self.conv_list.currentItem()
            if current_item and current_item.data(Qt.UserRole).get("title") == "新对话":
                current_item.setText(question[:30])

    # 思考阶段中文映射
    _PHASE_MAP = {
        "searching": "检索知识库",
        "reranking": "重排结果",
        "generating": "生成回答",
        "running": "思考中",
    }

    def _on_phase(self, status, detail):
        if self._llm_indicator:
            self._llm_indicator.set_status("running", detail)
        # 更新思考气泡的阶段文字
        phase_text = self._PHASE_MAP.get(status, detail[:20] or "思考中")
        if hasattr(self, '_think_timer') and self._think_timer is not None:
            self._thinking_phase = phase_text

    def _tick_thinking(self):
        if not hasattr(self, '_think_timer') or self._think_timer is None:
            return
        self._thinking_dots = (self._thinking_dots + 1) % 4
        dots = "·" * (self._thinking_dots + 1) + " " * (3 - self._thinking_dots)
        phase = getattr(self, '_thinking_phase', '思考中')
        # 更新思考气泡中的文字
        cursor = self.chat_output.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.movePosition(QTextCursor.StartOfBlock, QTextCursor.KeepAnchor)
        cursor.removeSelectedText()
        cursor.insertText(f"{phase} {dots}")

    def _stop_thinking(self):
        if hasattr(self, '_think_timer') and self._think_timer is not None:
            self._think_timer.stop()
            self._think_timer = None

    def _get_history(self) -> list[dict]:
        messages = Database.get_messages(self._current_conv_id)
        history = []
        for msg in messages[-10:]:
            role = msg.get("role")
            content = (msg.get("content") or "").strip()
            if role not in {"user", "assistant"}:
                continue
            if content.lower() in {"question", "answer"}:
                continue
            history.append({"role": role, "content": content})
        return history

    def _display_content(self, role: str, content: str) -> str:
        text = content or ""
        if text.strip().lower() == "question" and role == "user":
            return "（问题内容异常，未能恢复原始文本）"
        if text.strip().lower() == "answer" and role == "assistant":
            return "（回答内容异常，未能恢复原始文本）"
        return text

    def _on_chunk(self, chunk: str):
        cursor = self.chat_output.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertText(chunk)
        self.chat_output.setTextCursor(cursor)
        self.chat_output.ensureCursorVisible()

    def _on_worker_error(self, error_msg: str):
        """Handle errors from ChatWorker without saving to database."""
        self._stop_thinking()
        self.btn_send.setEnabled(True)
        if self._llm_indicator:
            self._llm_indicator.set_status("idle")
        self._display_messages()
        QMessageBox.warning(self, "错误", f"生成回答时出错: {error_msg}")

    def _on_response_finished(self, full_text: str, sources: list, source_graph: dict | None = None):
        self._stop_thinking()
        self.btn_send.setEnabled(True)
        if self._llm_indicator:
            self._llm_indicator.set_status("idle")

        source_graph = source_graph or {"nodes": [], "edges": []}
        self._last_ai_question = self._last_user_question if hasattr(self, '_last_user_question') else ""
        self._last_ai_sources = sources
        self._last_ai_source_graph = source_graph
        self.btn_save_wiki.setEnabled(len(full_text) >= 100)

        ai_msg = ChatMessage(
            conversation_id=self._current_conv_id,
            role="assistant",
            content=full_text,
            sources=sources,
            source_graph=source_graph,
        )
        Database.insert_message(ai_msg.to_row())

        self._display_messages()

        if sources:
            src_strs = [f"[{s.get('title', '未知')}] (score: {s.get('score', 0):.2f})" for s in sources]
            self.sources_label.setText("参考来源: " + " | ".join(src_strs))

        graph_summary = _source_graph_summary(source_graph)
        if graph_summary:
            current = self.sources_label.text()
            prefix = current + " | " if current else ""
            self.sources_label.setText(prefix + graph_summary)

    def _save_to_wiki(self):
        from src.utils.config import Config
        if not Config.get("wiki.enabled", False):
            return
        from PySide6.QtWidgets import QMessageBox
        if not self._last_ai_question:
            return
        reply = QMessageBox.question(
            self, "保存到 Wiki", "将这条 AI 回答保存为 Wiki 页面？",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        from src.services.wiki_compiler import WikiCompiler
        source_ids = [s.get("knowledge_id", "") for s in self._last_ai_sources if s.get("knowledge_id")]
        try:
            compiler = WikiCompiler()
            # 获取最后一条 AI 消息内容
            messages = Database.get_messages(self._current_conv_id)
            last_ai = next((m for m in reversed(messages) if m["role"] == "assistant"), None)
            answer = last_ai["content"] if last_ai else ""
            page_id = compiler.save_answer(self._last_ai_question, answer, source_ids)
            if page_id:
                QMessageBox.information(self, "成功", f"已保存为 Wiki 页面")
                self.btn_save_wiki.setEnabled(False)
            else:
                QMessageBox.warning(self, "提示", "回答内容过短，未保存")
        except Exception as e:
            QMessageBox.warning(self, "保存失败", str(e)[:200])

    @staticmethod
    def _escape(text: str) -> str:
        # 1. 移除思维链标签
        import re
        text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
        text = re.sub(r'<thinking>.*?</thinking>', '', text, flags=re.DOTALL)
        # 2. 压缩所有连续换行为一个
        text = re.sub(r'\n+', '\n', text)
        # 3. HTML 转义
        text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        text = text.replace("\n", "<br>")
        return text
