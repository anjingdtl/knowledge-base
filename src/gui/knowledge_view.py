"""知识浏览/管理界面"""
import hashlib
import json
import os

from PySide6.QtWidgets import (
    QWidget, QFrame, QVBoxLayout, QHBoxLayout, QLineEdit,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QLabel, QTextEdit,
    QPushButton, QComboBox, QFileDialog, QMenu,
    QSplitter, QMessageBox, QInputDialog, QProgressDialog,
    QStackedWidget, QToolButton,
)
from PySide6.QtCore import Qt, Signal, QThread, QTimer, QPropertyAnimation, QEasingCurve, QPoint, QSettings
from PySide6.QtGui import QColor

from src.services.db import Database
from src.gui.icons import NAV, icon as make_icon, set_named_icon
from src.gui.theme import get_color
from src.utils.config import Config
from src.gui.empty_state import EmptyState


def _check_garbled(content: str) -> bool:
    """检测内容是否严重乱码或为空（小部分乱码不标记，不影响阅读）"""
    import re
    if not content or not content.strip():
        return True
    # U+FFFD 替换字符 — 阈值放宽到 20 个且密度 > 5%
    repl_count = content.count("�")
    if repl_count > 20 and repl_count / len(content) > 0.05:
        return True
    # 控制字符（非换行/制表符）— 阈值放宽到 100 个且密度 > 10%
    ctrl_chars = len(re.findall(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", content))
    if ctrl_chars > 100 and ctrl_chars / len(content) > 0.10:
        return True
    # 非常见 Unicode 区段字符密度 — 放宽到 100 个且密度 > 20%
    unusual = len(re.findall(
        r"[ࠀ-࿿က-῿ -⯿ꀀ-￿�]", content
    ))
    if unusual > 100 and unusual / len(content) > 0.20:
        return True
    # (cid:xxx) 模式 — 放宽到 50 次
    if content.count("(cid:") > 50:
        return True
    # 有效字符占比过低（< 5%）才判定为乱码
    cn_chars = len(re.findall(r"[一-鿿＀-￯]", content))
    alpha_chars = len(re.findall(r"[a-zA-Z0-9]", content))
    total_chars = len(content.strip())
    if total_chars > 200 and cn_chars + alpha_chars < total_chars * 0.05:
        return True
    return False


class DedupWorker(QThread):
    """后台去重线程"""
    progress = Signal(int, str)
    finished = Signal(int, int)  # groups_found, removed_count

    def run(self):
        from src.services.vectorstore import VectorStore
        groups = Database.find_duplicates()
        if not groups:
            self.finished.emit(0, 0)
            return

        removed = 0
        total = sum(len(g) - 1 for g in groups)
        for i, group in enumerate(groups):
            # 保留第一条（最新的），删除其余
            for item in group[1:]:
                VectorStore().delete_by_knowledge(item["id"])
                Database.delete_knowledge(item["id"])
                removed += 1
                self.progress.emit(
                    int(removed / max(total, 1) * 100),
                    f"去重中: {removed}/{total}"
                )

        self.finished.emit(len(groups), removed)


class QualityWorker(QThread):
    """后台质量审查 + 自动修复线程"""
    progress = Signal(int, str)
    finished = Signal(int, int, int)  # garbled_count, repaired_count, total

    def __init__(self, items=None):
        super().__init__()
        self._items = items

    def run(self):
        if self._items is not None:
            items = self._items
        else:
            items = Database.list_knowledge(limit=10000)
        garbled_items = []
        total = len(items)

        # Phase 1: 审查标记
        for i, item in enumerate(items):
            content = item.get("content", "")
            quality = "garbled" if _check_garbled(content) else "ok"
            Database.update_knowledge(item["id"], quality=quality)
            if quality == "garbled":
                garbled_items.append(item)
            if i % 20 == 0:
                self.progress.emit(int(i / total * 70), f"审查中: {i}/{total}")

        # Phase 2: 自动修复乱码条目
        repaired = 0
        for j, item in enumerate(garbled_items):
            self.progress.emit(
                70 + int(j / max(len(garbled_items), 1) * 30),
                f"修复中: {j}/{len(garbled_items)}",
            )
            if self._try_repair(item):
                repaired += 1

        self.finished.emit(len(garbled_items), repaired, total)

    def _try_repair(self, item: dict) -> bool:
        """尝试修复乱码条目：源文件重读 → LLM 修复"""
        from src.models.knowledge import KnowledgeItem
        from src.services.llm import LLMService
        from src.gui.import_dialog import _strip_think
        source_path = item.get("source_path", "")
        content = item.get("content", "")

        # 方案 1: 源文件还在，重新读取（charset-normalizer 可能修复编码类乱码）
        if source_path and os.path.isfile(source_path):
            try:
                from src.services.file_parser import parse_file
                parsed = parse_file(source_path)
                if parsed.content and not _check_garbled(parsed.content):
                    content_hash = hashlib.sha256(parsed.content.encode("utf-8")).hexdigest()
                    Database.update_knowledge(
                        item["id"],
                        content=parsed.content,
                        source_path=parsed.source_path,
                        file_type=parsed.file_type,
                        content_hash=content_hash,
                        quality="ok",
                    )
                    ki = KnowledgeItem(
                        id=item["id"],
                        title=item["title"],
                        content=parsed.content,
                        source_type="file",
                        source_path=parsed.source_path,
                        file_type=parsed.file_type,
                        content_hash=content_hash,
                    )
                    from src.services.indexer import reindex_knowledge_item
                    reindex_knowledge_item(item["id"], ki)
                    return True
            except Exception:
                import logging
                import traceback
                logging.getLogger(__name__).warning(
                    "源文件重读修复失败 [id=%s, path=%s]: %s",
                    item.get("id"), source_path, traceback.format_exc(),
                )

        # 方案 2: LLM 尝试修复（包括 PDF 字体映射乱码，这些文件仍有大量可读内容）
        if content and len(content) > 50:
            try:
                llm = LLMService()
                prompt = (
                    "以下文本因编码错误或PDF字体映射问题出现乱码，请根据上下文推断并修复为正确的文本。\n"
                    "规则：\n"
                    "1. 修复乱码字符，保留原文结构和格式\n"
                    "2. 无法推断的部分用[?]替代\n"
                    "3. 只输出修复后的文本，不要解释\n\n"
                    f"乱码文本：\n{content[:3000]}"
                )
                fixed = llm.chat([{"role": "user", "content": prompt}], silent=True)
                fixed = _strip_think(fixed).strip()
                if fixed and not _check_garbled(fixed) and len(fixed) > len(content[:3000]) * 0.3:
                    Database.update_knowledge(item["id"], content=fixed, quality="ok")
                    ki = KnowledgeItem(
                        id=item["id"],
                        title=item["title"],
                        content=fixed,
                        source_type=item.get("source_type", "file"),
                    )
                    from src.services.indexer import reindex_knowledge_item
                    reindex_knowledge_item(item["id"], ki)
                    return True
            except Exception:
                import logging
                import traceback
                logging.getLogger(__name__).warning(
                    "LLM 修复失败 [id=%s, title=%s]: %s",
                    item.get("id"), item.get("title"), traceback.format_exc(),
                )

        return False


class RenameWorker(QThread):
    """后台批量智能重命名线程 — 批量调用 LLM，每次处理最多 BATCH_SIZE 条"""
    progress = Signal(int, str)
    finished = Signal(int)

    BATCH_SIZE = 10

    def __init__(self, items=None):
        super().__init__()
        self._items = items

    def run(self):
        try:
            self._do_rename()
        except Exception:
            import traceback
            traceback.print_exc()
        finally:
            self.finished.emit(getattr(self, "_renamed", 0))

    def _do_rename(self):
        import re
        import json
        from src.services.llm import LLMService
        from src.gui.import_dialog import _strip_think, generate_title

        if self._items is not None:
            items = self._items
        else:
            items = Database.list_knowledge(limit=10000)
        total = len(items)
        self._renamed = 0

        for i, item in enumerate(items):
            content = item.get("content", "")
            source_path = item.get("source_path", "")
            filename = ""
            if source_path and os.path.isfile(source_path):
                filename = os.path.splitext(os.path.basename(source_path))[0]
            elif source_path:
                filename = os.path.splitext(os.path.basename(source_path))[0]
            if not filename:
                continue
            try:
                new_title = generate_title(content, filename=filename)
                old_title = item.get("title", "")
                if new_title and new_title != old_title:
                    Database.update_knowledge(item["id"], title=new_title)
                    self._renamed += 1
            except Exception:
                pass
            if i % 5 == 0:
                self.progress.emit(int((i + 1) / total * 100), f"已处理 {i + 1}/{total}")


# 表格列定义
COL_TITLE = 0
COL_FORMAT = 1
COL_IMPORTED = 2
COL_FILE_CREATED = 3
COL_TAGS = 4
TABLE_HEADERS = ["标题", "格式", "导入时间", "文件创建时间", "标签"]

FILE_TYPE_COLORS = {
    "pdf": "#e8eff5", "docx": "#ede8ef", "xlsx": "#e6efe7",
    "csv": "#f5efe6", "txt": "#f5f3f0", "md": "#f0e4e8",
    "html": "#e2eff0", "code": "#eef2ea", "image": "#f5f0e6",
}


class KnowledgeView(QWidget):
    def __init__(self):
        super().__init__()
        self._setup_ui()
        self._load_knowledge()

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
        title = QLabel("知识库")
        title.setObjectName("pageTitle")
        subtitle = QLabel("管理本地文档、网页内容和手动沉淀的知识条目")
        subtitle.setObjectName("pageSubtitle")
        title_col.addWidget(title)
        title_col.addWidget(subtitle)
        header_layout.addLayout(title_col)
        header_layout.addStretch()
        layout.addWidget(header_card)

        # 顶部工具栏：左半搜索/筛选，右半操作按钮
        toolbar_card = QFrame()
        toolbar_card.setObjectName("toolbarCard")
        toolbar = QHBoxLayout(toolbar_card)
        toolbar.setContentsMargins(12, 10, 12, 10)
        toolbar.setSpacing(8)

        self.search_input = QLineEdit()
        self.search_input.setProperty("search", True)
        self.search_input.setPlaceholderText("搜索标题、正文或标签...")
        self.search_input.textChanged.connect(self._on_search)
        toolbar.addWidget(self.search_input, 1)

        self.tag_filter = QComboBox()
        self.tag_filter.addItem("全部标签")
        self.tag_filter.currentTextChanged.connect(self._on_tag_filter)
        toolbar.addWidget(self.tag_filter)

        self.format_filter = QComboBox()
        self.format_filter.addItem("全部格式")
        self.format_filter.currentTextChanged.connect(self._on_format_filter)
        toolbar.addWidget(self.format_filter)

        # 分隔间距
        toolbar.addSpacing(8)

        # 高频操作：主要按钮
        btn_import = QPushButton("导入文件")
        btn_import.setObjectName("accentBtn")
        set_named_icon(btn_import, "import", "on_accent", 16)
        btn_import.clicked.connect(self._import_files)
        toolbar.addWidget(btn_import)

        btn_add = QPushButton("手动添加")
        set_named_icon(btn_add, "add", "text_dim", 15)
        btn_add.clicked.connect(self._add_manual)
        toolbar.addWidget(btn_add)

        # 低频操作：收入"更多"菜单
        self.btn_more = QToolButton()
        self.btn_more.setText("更多")
        set_named_icon(self.btn_more, "more", "text_dim", 15)
        self.btn_more.setPopupMode(QToolButton.InstantPopup)
        more_menu = QMenu(self.btn_more)

        act_refresh = more_menu.addAction(make_icon(NAV["refresh"]), "刷新列表")
        act_refresh.triggered.connect(self._load_knowledge)

        more_menu.addSeparator()

        self.act_rename = more_menu.addAction(make_icon(NAV["rename"]), "智能重命名")
        self.act_rename.triggered.connect(self._smart_rename)

        self.act_quality = more_menu.addAction(make_icon(NAV["quality"]), "质量审查")
        self.act_quality.triggered.connect(self._quality_check)

        self.act_dedup = more_menu.addAction(make_icon(NAV["dedup"]), "知识去重")
        self.act_dedup.triggered.connect(self._deduplicate)

        self.btn_more.setMenu(more_menu)
        toolbar.addWidget(self.btn_more)

        layout.addWidget(toolbar_card)

        # 主内容区（表格 + 空状态）
        self.list_stack = QStackedWidget()

        self.table_widget = QTableWidget(0, len(TABLE_HEADERS))
        self.table_widget.setHorizontalHeaderLabels(TABLE_HEADERS)
        self.table_widget.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table_widget.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table_widget.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table_widget.setSortingEnabled(True)
        self.table_widget.setAlternatingRowColors(False)
        self.table_widget.verticalHeader().setVisible(False)
        self.table_widget.verticalHeader().setDefaultSectionSize(42)
        self.table_widget.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table_widget.customContextMenuRequested.connect(self._show_context_menu)
        self.table_widget.currentCellChanged.connect(self._on_row_selected)
        self.table_widget.cellDoubleClicked.connect(self._on_row_double_clicked)

        # 列宽设置 — 全部可交互拖拽调整，从 QSettings 恢复上次的宽度
        header = self.table_widget.horizontalHeader()
        default_widths = {COL_TITLE: 300, COL_FORMAT: 70, COL_IMPORTED: 140,
                          COL_FILE_CREATED: 140, COL_TAGS: 150}
        for col, default_w in default_widths.items():
            header.setSectionResizeMode(col, QHeaderView.Interactive)
            self.table_widget.setColumnWidth(col, default_w)
        # 尝试恢复上次保存的 header 状态（列宽 + 排序）
        col_settings = QSettings("ShineHeKnowledge", "KnowledgeTable")
        saved_state = col_settings.value("header_state")
        if saved_state:
            header.restoreState(saved_state)

        # 空状态
        self.empty_state = EmptyState(
            title="还没有知识条目",
            description="导入文件、粘贴文本或从网页抓取，开始构建你的知识库",
            buttons=[
                {"text": "导入文件", "callback": self._import_files, "objectName": "primaryBtn"},
                {"text": "手动添加", "callback": self._add_manual},
            ],
            icon_key="knowledge",
        )
        self.empty_search = EmptyState(
            title="没有找到匹配的知识",
            description="换个关键词试试，或导入新内容",
            icon_key="quality",
        )

        self.list_stack.addWidget(self.table_widget)
        self.list_stack.addWidget(self.empty_state)
        self.list_stack.addWidget(self.empty_search)
        layout.addWidget(self.list_stack, 1)

        # 右侧弹出式详情面板（覆盖在主内容之上）
        self._detail_width = 450
        self._detail_open = False
        self._detail_anim = None

        self.detail_panel = QFrame(self)
        self.detail_panel.setObjectName("detailCard")
        self.detail_panel.setFixedWidth(self._detail_width)
        self.detail_panel.setVisible(False)

        from PySide6.QtWidgets import QGraphicsDropShadowEffect
        detail_shadow = QGraphicsDropShadowEffect(self.detail_panel)
        detail_shadow.setBlurRadius(30)
        detail_shadow.setOffset(-4, 0)
        detail_shadow.setColor(QColor(0, 0, 0, 40))
        self.detail_panel.setGraphicsEffect(detail_shadow)

        detail_layout = QVBoxLayout(self.detail_panel)
        detail_layout.setContentsMargins(16, 12, 16, 12)

        # 顶部：关闭按钮 + 标题
        detail_header = QHBoxLayout()
        self.detail_title = QLabel("")
        self.detail_title.setObjectName("sectionLabel")
        self.detail_title.setWordWrap(True)
        detail_header.addWidget(self.detail_title, 1)
        btn_close = QPushButton("✕")
        btn_close.setFixedSize(28, 28)
        btn_close.setObjectName("closeDetailBtn")
        set_named_icon(btn_close, "close", "text_dim", 12)
        btn_close.clicked.connect(self._hide_detail_panel)
        detail_header.addWidget(btn_close)
        detail_layout.addLayout(detail_header)

        self.detail_meta = QLabel("")
        self.detail_meta.setObjectName("hintLabel")
        self.detail_meta.setWordWrap(True)
        detail_layout.addWidget(self.detail_meta)

        self.detail_tags = QLabel("")
        self.detail_tags.setWordWrap(True)
        detail_layout.addWidget(self.detail_tags)

        self.detail_content = QTextEdit()
        self.detail_content.setReadOnly(True)
        detail_layout.addWidget(self.detail_content, 1)

        self.detail_chars = QLabel("")
        self.detail_chars.setWordWrap(True)
        detail_layout.addWidget(self.detail_chars)

    # 当前是否处于搜索模式
    _search_mode: bool = False
    _search_timer: QTimer = None

    # ---- 详情面板弹出/收回 ----

    def _show_detail_panel(self, item: dict):
        """弹出右侧详情面板并显示内容"""
        # 停止旧动画
        if self._detail_anim is not None:
            self._detail_anim.stop()
            self._detail_anim = None

        self._show_detail(item)
        self.detail_panel.setFixedHeight(self.height())
        self.detail_panel.setVisible(True)
        self.detail_panel.raise_()

        target_x = self.width() - self._detail_width
        self.detail_panel.move(self.width(), 0)

        anim = QPropertyAnimation(self.detail_panel, b"pos")
        anim.setDuration(220)
        anim.setStartValue(QPoint(self.width(), 0))
        anim.setEndValue(QPoint(target_x, 0))
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.start()
        self._detail_anim = anim
        self._detail_open = True

    def _hide_detail_panel(self):
        """收回右侧详情面板"""
        if not self._detail_open:
            return
        self._detail_open = False

        # 停止旧动画
        if self._detail_anim is not None:
            self._detail_anim.stop()

        anim = QPropertyAnimation(self.detail_panel, b"pos")
        anim.setDuration(180)
        anim.setStartValue(self.detail_panel.pos())
        anim.setEndValue(QPoint(self.width(), 0))
        anim.setEasingCurve(QEasingCurve.Type.InCubic)
        anim.finished.connect(self._safe_hide_panel)
        anim.start()
        self._detail_anim = anim

    def _safe_hide_panel(self):
        """安全隐藏面板（动画完成后调用）"""
        try:
            if self.detail_panel is not None:
                self.detail_panel.hide()
        except RuntimeError:
            pass

    def resizeEvent(self, event):
        """窗口大小变化时更新详情面板位置"""
        super().resizeEvent(event)
        if self._detail_open:
            try:
                self.detail_panel.setFixedHeight(self.height())
                self.detail_panel.move(self.width() - self._detail_width, 0)
            except RuntimeError:
                pass

    def hideEvent(self, event):
        """视图被隐藏时停止动画、收回面板、保存列宽"""
        super().hideEvent(event)
        if self._detail_anim is not None:
            self._detail_anim.stop()
            self._detail_anim = None
        try:
            self.detail_panel.setVisible(False)
        except RuntimeError:
            pass
        self._detail_open = False
        # 保存表格列宽到 QSettings
        self._save_column_widths()

    def _save_column_widths(self):
        """将当前表格列宽持久化"""
        try:
            settings = QSettings("ShineHeKnowledge", "KnowledgeTable")
            settings.setValue("header_state",
                              self.table_widget.horizontalHeader().saveState())
        except RuntimeError:
            pass

    def _do_search(self, text: str):
        """实际执行搜索"""
        if not text.strip():
            self._search_mode = False
            self._populate_table(Database.list_knowledge(limit=200))
        else:
            self._search_mode = True
            self._populate_table(Database.search_knowledge(text))

    def _populate_table(self, items: list[dict]):
        """通用表格填充方法"""
        self.table_widget.setSortingEnabled(False)
        self.table_widget.setRowCount(0)

        for item in items:
            row = self.table_widget.rowCount()
            self.table_widget.insertRow(row)

            # 标题
            quality = item.get("quality", "")
            title = item.get("title", "")
            if quality == "garbled":
                title_item = QTableWidgetItem(f"[乱码] {title}")
                title_item.setForeground(QColor(get_color("garbled_fg")))
            else:
                title_item = QTableWidgetItem(title)
            title_item.setData(Qt.UserRole, item)
            self.table_widget.setItem(row, COL_TITLE, title_item)

            # 格式（彩色徽章）
            file_type = item.get("file_type", "")
            format_item = QTableWidgetItem(file_type)
            bg_color = FILE_TYPE_COLORS.get(file_type, "#F5F1EB")
            format_item.setBackground(QColor(bg_color))
            format_item.setForeground(QColor("#1A1A1A"))
            format_item.setTextAlignment(Qt.AlignCenter)
            self.table_widget.setItem(row, COL_FORMAT, format_item)

            # 导入时间
            imported = item.get("created_at", "")[:16].replace("T", " ")
            self.table_widget.setItem(row, COL_IMPORTED, QTableWidgetItem(imported))

            # 文件创建时间
            file_created = item.get("file_created_at", "")
            if file_created:
                file_created = file_created[:16].replace("T", " ")
            self.table_widget.setItem(row, COL_FILE_CREATED, QTableWidgetItem(file_created))

            # 标签
            tags = json.loads(item.get("tags", "[]")) if isinstance(item.get("tags"), str) else item.get("tags", [])
            self.table_widget.setItem(row, COL_TAGS, QTableWidgetItem(" · ".join(tags)))

        self.table_widget.setSortingEnabled(True)

        # 空状态切换
        if len(items) == 0:
            if self._search_mode:
                self.list_stack.setCurrentIndex(2)
            else:
                self.list_stack.setCurrentIndex(1)
        else:
            self.list_stack.setCurrentIndex(0)

    def _load_knowledge(self):
        self._populate_table(Database.list_knowledge(limit=200))
        self._load_tags()
        self._load_formats()

    def _flash_success(self, widget, duration=800):
        """操作成功反馈：短暂绿色边框闪烁"""
        accent = get_color("accent")
        orig_style = widget.styleSheet()
        widget.setStyleSheet(
            f"{orig_style}; border: 2px solid {accent}; background: {get_color('accent_surface')};"
        )
        def _restore():
            try:
                widget.setStyleSheet(orig_style)
            except RuntimeError:
                pass
        QTimer.singleShot(duration, _restore)

    def _load_tags(self):
        current = self.tag_filter.currentText()
        self.tag_filter.blockSignals(True)
        self.tag_filter.clear()
        self.tag_filter.addItem("全部标签")
        for tag in Database.get_all_tags():
            self.tag_filter.addItem(tag)
        idx = self.tag_filter.findText(current)
        if idx >= 0:
            self.tag_filter.setCurrentIndex(idx)
        self.tag_filter.blockSignals(False)

    def _load_formats(self):
        current = self.format_filter.currentText()
        self.format_filter.blockSignals(True)
        self.format_filter.clear()
        self.format_filter.addItem("全部格式")
        for ft in Database.get_all_file_types():
            self.format_filter.addItem(ft)
        idx = self.format_filter.findText(current)
        if idx >= 0:
            self.format_filter.setCurrentIndex(idx)
        self.format_filter.blockSignals(False)

    def _on_search(self, text: str):
        """搜索防抖：复用单个 QTimer"""
        if self._search_timer is not None:
            self._search_timer.stop()
            try:
                self._search_timer.timeout.disconnect()
            except RuntimeError:
                pass
        else:
            self._search_timer = QTimer(self)
            self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(lambda: self._do_search(text))
        self._search_timer.start(300)

    def _apply_combined_filter(self):
        """组合格式+标签筛选"""
        tag = self.tag_filter.currentText()
        file_type = self.format_filter.currentText()
        tag = None if tag == "全部标签" else tag
        file_type = None if file_type == "全部格式" else file_type
        self._search_mode = False
        self._populate_table(Database.list_knowledge(tag=tag, file_type=file_type, limit=200))

    def _on_tag_filter(self, tag: str):
        self._apply_combined_filter()

    def _on_format_filter(self, file_type: str):
        self._apply_combined_filter()

    def _get_selected_item(self) -> dict | None:
        """获取当前选中行的知识条目数据"""
        row = self.table_widget.currentRow()
        if row < 0:
            return None
        title_item = self.table_widget.item(row, COL_TITLE)
        if not title_item:
            return None
        return title_item.data(Qt.UserRole)

    def _on_row_selected(self, row: int, col: int, prev_row: int, prev_col: int):
        """单击行：不做任何操作（详情面板通过双击弹出）"""
        pass

    def _on_row_double_clicked(self, row: int, col: int):
        """双击行：弹出右侧详情面板"""
        item = self._get_selected_item()
        if not item:
            return
        self._show_detail_panel(item)

    def _show_detail(self, item: dict):
        self.detail_title.setText(item["title"])

        # 结构化元信息：基本信息行
        dim = get_color("text_dim")
        accent = get_color("accent")
        file_type = item.get("file_type", "未知")
        source = item.get("source_path", "手动创建")
        created = item.get("created_at", "")[:16].replace("T", " ")
        quality = item.get("quality", "")
        quality_text = "正常" if quality == "ok" else "乱码" if quality == "garbled" else "未审查"

        self.detail_meta.setText(
            f'<span style="color:{dim};font-size:{max(10, Config.get("appearance.font_size", 13) - 2)}px;">'
            f'格式 {file_type} &nbsp;|&nbsp; 来源 {source} &nbsp;|&nbsp; 导入 {created}'
            f'</span>'
        )

        # 标签 flex 布局
        tags = json.loads(item.get("tags", "[]")) if isinstance(item.get("tags"), str) else item.get("tags", [])
        tag_bg = get_color("tag_bg")
        tag_text_color = get_color("tag_text")
        tag_sm = max(10, Config.get("appearance.font_size", 13) - 2)
        tag_html = (
            f'<div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:4px;">'
            + "".join(
                f'<span style="background:{tag_bg};color:{tag_text_color};padding:3px 12px;'
                f'border-radius:8px;font-size:{tag_sm}px;'
                f'border:1px solid {get_color("accent")}30;">{t}</span>'
                for t in tags
            )
            + f'<span style="color:{dim};font-size:{tag_sm}px;margin-left:6px;">质量：{quality_text}</span>'
            + '</div>'
        )
        self.detail_tags.setText(tag_html)

        content = item.get("content", "")
        self.detail_content.setPlainText(content[:10000] if len(content) > 10000 else content)

        # 底部字数统计
        total_chars = len(content)
        self.detail_chars.setText(
            f'<span style="color:{dim};font-size:{max(9, Config.get("appearance.font_size", 13) - 4)}px;">'
            f'共 {total_chars} 字'
            + (f'（已截断显示前 10000 字）' if total_chars > 10000 else '')
            + '</span>'
        )

    def _show_context_menu(self, pos):
        row = self.table_widget.rowAt(pos.y())
        if row < 0:
            return
        title_item = self.table_widget.item(row, COL_TITLE)
        if not title_item:
            return
        data = title_item.data(Qt.UserRole)
        if not data:
            return
        menu = QMenu(self)
        action_reimport = None
        quality = data.get("quality", "")
        if quality == "garbled":
            action_reimport = menu.addAction(make_icon(NAV["import"]), "重新导入")
            menu.addSeparator()
        action_edit_tags = menu.addAction(make_icon(NAV["rename"]), "编辑标签")
        action_delete = menu.addAction(make_icon(NAV["delete"], "danger"), "删除")
        action = menu.exec(self.table_widget.viewport().mapToGlobal(pos))
        if action_reimport and action == action_reimport:
            self._reimport_item(data)
        elif action == action_edit_tags:
            self._edit_tags_for_item(data)
        elif action == action_delete:
            self._delete_item(data)

    def _reimport_item(self, data: dict):
        """重新选择文件替换乱码条目"""
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择替换文件", "",
            "支持的文件 (*.pdf *.pptx *.ppt *.docx *.txt *.md *.html *.xlsx *.xls *.csv);;所有文件 (*)",
        )
        if not files:
            return
        from src.services.file_parser import parse_file
        from src.models.knowledge import KnowledgeItem
        path = files[0]
        try:
            parsed_list = parse_file(path)
            parsed = parsed_list[0]
            content_hash = hashlib.sha256(parsed.content.encode("utf-8")).hexdigest()
            title = data.get("title", "")
            from src.gui.import_dialog import generate_title
            filename_stem = os.path.splitext(os.path.basename(path))[0]
            new_title = generate_title(parsed.content, filename=filename_stem)
            if not new_title:
                new_title = title
            file_size = 0
            try:
                file_size = os.path.getsize(path)
            except OSError:
                pass
            Database.update_knowledge(
                data["id"],
                title=new_title,
                content=parsed.content,
                source_path=parsed.source_path,
                file_type=parsed.file_type,
                file_size=file_size,
                content_hash=content_hash,
                quality="ok",
            )
            ki = KnowledgeItem(
                id=data["id"],
                title=new_title,
                content=parsed.content,
                source_type="file",
                source_path=parsed.source_path,
                file_type=parsed.file_type,
                file_size=file_size,
                content_hash=content_hash,
            )
            from src.services.indexer import reindex_knowledge_item
            reindex_knowledge_item(data["id"], ki)
            self._load_knowledge()
            QMessageBox.information(self, "成功", f"已替换: {new_title}")
        except Exception as e:
            QMessageBox.warning(self, "替换失败", str(e))

    def _edit_tags_for_item(self, data: dict):
        current_tags = json.loads(data.get("tags", "[]")) if isinstance(data.get("tags"), str) else data.get("tags", [])
        text, ok = QInputDialog.getText(
            self, "编辑标签", "输入标签（逗号分隔）：", text=", ".join(current_tags),
        )
        if ok:
            new_tags = [t.strip() for t in text.split(",") if t.strip()]
            Database.update_knowledge(data["id"], tags=json.dumps(new_tags, ensure_ascii=False))
            self._load_knowledge()

    def _delete_item(self, data: dict):
        reply = QMessageBox.question(
            self, "确认删除", f"确定删除 \"{data['title']}\" 吗？",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            from src.services.vectorstore import VectorStore
            VectorStore().delete_by_knowledge(data["id"])
            Database.delete_knowledge(data["id"])
            self._load_knowledge()

    def _import_files(self):
        from src.gui.import_dialog import ImportDialog
        dialog = ImportDialog(self)
        if dialog.exec() == ImportDialog.Accepted:
            self._load_knowledge()
            self._flash_success(self.list_stack)

    def _add_manual(self):
        from PySide6.QtWidgets import QDialog, QFormLayout
        dialog = QDialog(self)
        dialog.setWindowTitle("手动添加知识")
        dialog.setMinimumWidth(500)
        form = QFormLayout(dialog)

        title_input = QLineEdit()
        content_edit = QTextEdit()
        content_edit.setMaximumHeight(200)
        tags_input = QLineEdit()
        tags_input.setPlaceholderText("标签1, 标签2")

        form.addRow("标题：", title_input)
        form.addRow("内容：", content_edit)
        form.addRow("标签：", tags_input)

        btns = QHBoxLayout()
        btn_ok = QPushButton("确定")
        btn_ok.setObjectName("primaryBtn")
        set_named_icon(btn_ok, "approve", "on_accent", 14)
        btn_cancel = QPushButton("取消")
        set_named_icon(btn_cancel, "close", "text_dim", 13)
        btn_cancel.clicked.connect(dialog.reject)
        btn_ok.clicked.connect(dialog.accept)
        btns.addWidget(btn_ok)
        btns.addWidget(btn_cancel)
        form.addRow(btns)

        if dialog.exec() == QDialog.Accepted:
            tags = [t.strip() for t in tags_input.text().split(",") if t.strip()]
            item = KnowledgeItem(
                title=title_input.text(),
                content=content_edit.toPlainText(),
                source_type="manual",
                tags=tags,
            )
            Database.insert_knowledge(item.to_row())
            from src.services.indexer import index_knowledge_item
            index_knowledge_item(item)
            self._load_knowledge()

    def _quality_check(self):
        # 增量：只取未审查的条目（quality 为空字符串）
        items = Database.list_knowledge(quality="", limit=10000)
        if not items:
            total = len(Database.list_knowledge(limit=10000))
            reply = QMessageBox.question(
                self, "全部审查完毕",
                f"所有 {total} 条知识已审查完毕。\n是否强制重新全量审查？",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return
            items = Database.list_knowledge(limit=10000)

        self.act_quality.setEnabled(False)
        progress = QProgressDialog(f"正在审查 {len(items)} 条知识...", None, 0, 100, self)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setCancelButton(None)

        total = len(items)
        self._quality_worker = QualityWorker(items=items)
        self._quality_worker.progress.connect(
            lambda v, msg: (progress.setValue(int(v / total * 100)) if total > 0 else None, progress.setLabelText(msg))
        )
        self._quality_worker.finished.connect(
            lambda garbled, repaired, t: (progress.close(), self._on_quality_finished(garbled, repaired, t))
        )
        self._quality_worker.start()

    def _on_quality_finished(self, garbled: int, repaired: int, total: int):
        self.act_quality.setEnabled(True)
        self._load_knowledge()
        if garbled == 0:
            QMessageBox.information(self, "审查完成", f"共 {total} 个条目，全部正常。")
        else:
            still_garbled = garbled - repaired
            msg = f"共 {total} 个条目，发现 {garbled} 个异常条目。\n已自动修复 {repaired} 个"
            if still_garbled > 0:
                msg += (f"\n\n剩余 {still_garbled} 个无法自动修复：\n"
                        f"- 可右键\"重新导入\"选择正确文件替换")
            else:
                msg += "，全部修复成功。"
            if still_garbled > 0:
                QMessageBox.warning(self, "审查完成", msg)
            else:
                QMessageBox.information(self, "审查完成", msg)

    def _smart_rename(self):
        # 增量：只处理标题未标准化的条目
        all_items = Database.list_knowledge(limit=10000)

        items = []
        for it in all_items:
            title = it.get("title", "")
            source_path = it.get("source_path", "")

            # 标题含书名号《》，视为已固定
            if title.startswith("《") and "》" in title and len(title) > 2:
                continue

            # 无源文件路径的条目无法基于文件名重命名，跳过
            if not source_path:
                continue

            # 提取文件名主干
            filename = os.path.splitext(os.path.basename(source_path))[0]

            # 标题已是文件名 或 文件名+补充格式，视为已完成重命名
            if filename and title and (
                title == filename[:60]
                or title.startswith(filename + "（")
                or title.startswith(filename + "(")
            ):
                continue

            items.append(it)

        if not items:
            reply = QMessageBox.question(
                self, "全部标准化完毕",
                f"所有 {len(all_items)} 个条目标题已标准化。\n是否强制重新全量重命名？",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return
            items = all_items
        else:
            reply = QMessageBox.question(
                self, "智能重命名",
                f"共 {len(items)} 个条目需要重命名（已跳过 {len(all_items) - len(items)} 个标准化标题），是否继续？",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return

        self.act_rename.setEnabled(False)
        self._rename_progress = QProgressDialog(f"正在重命名 {len(items)} 条...", None, 0, 100, self)
        self._rename_progress.setWindowModality(Qt.WindowModal)
        self._rename_progress.setMinimumDuration(0)
        self._rename_progress.setCancelButton(None)

        total = len(items)
        self._rename_worker = RenameWorker(items=items)
        self._rename_worker.progress.connect(self._on_rename_progress)
        self._rename_worker.finished.connect(self._on_rename_finished)
        self._rename_worker.start()

    def _on_rename_progress(self, v, msg):
        if self._rename_progress:
            self._rename_progress.setValue(v)
            self._rename_progress.setLabelText(msg)

    def _on_rename_finished(self, count: int):
        if self._rename_progress:
            self._rename_progress.close()
            self._rename_progress = None
        self.act_rename.setEnabled(True)
        self._load_knowledge()
        QMessageBox.information(self, "完成", f"智能重命名完成，共更新 {count} 个条目标题。")

    def _deduplicate(self):
        """扫描并去除重复的知识条目"""
        groups = Database.find_duplicates()
        if not groups:
            QMessageBox.information(self, "知识去重", "没有发现重复的知识条目。")
            return

        total_dupes = sum(len(g) - 1 for g in groups)

        # 用自定义对话框代替 QMessageBox，限制高度可滚动
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QTextEdit, QDialogButtonBox
        dialog = QDialog(self)
        dialog.setWindowTitle("确认去重")
        dialog.setMinimumWidth(420)
        dialog.setMaximumHeight(500)

        layout = QVBoxLayout(dialog)

        summary = QLabel(f"发现 {len(groups)} 组重复条目，共 {total_dupes} 个重复项。\n"
                         f"将删除旧条目，保留每组最新的一个。")
        summary.setObjectName("hintLabel")
        summary.setWordWrap(True)
        layout.addWidget(summary)

        detail = QTextEdit()
        detail.setReadOnly(True)
        detail.setMaximumHeight(300)
        lines = []
        for i, g in enumerate(groups[:50]):
            keep = g[0]
            lines.append(f"【{keep['title']}】(保留)")
            for dup in g[1:]:
                t = dup.get("created_at", "")[:16].replace("T", " ")
                lines.append(f"  × {t}")
        if len(groups) > 50:
            lines.append(f"\n... 还有 {len(groups) - 50} 组未显示")
        detail.setPlainText("\n".join(lines))
        layout.addWidget(detail)

        btns = QDialogButtonBox(QDialogButtonBox.Yes | QDialogButtonBox.No)
        btns.button(QDialogButtonBox.Yes).setText("确认去重")
        btns.button(QDialogButtonBox.No).setText("取消")
        layout.addWidget(btns)

        btns.accepted.connect(dialog.accept)
        btns.rejected.connect(dialog.reject)

        if dialog.exec() != QDialog.Accepted:
            return

        self.act_dedup.setEnabled(False)
        progress = QProgressDialog("正在去重...", None, 0, 100, self)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setCancelButton(None)

        self._dedup_worker = DedupWorker()
        self._dedup_worker.progress.connect(
            lambda v, msg: (progress.setValue(v), progress.setLabelText(msg))
        )
        self._dedup_worker.finished.connect(
            lambda groups_found, removed: (
                progress.close(),
                self._on_dedup_finished(groups_found, removed),
            )
        )
        self._dedup_worker.start()

    def _on_dedup_finished(self, groups_found: int, removed: int):
        self.act_dedup.setEnabled(True)
        self._load_knowledge()
        if removed > 0:
            QMessageBox.information(
                self, "去重完成",
                f"共处理 {groups_found} 组重复，删除了 {removed} 个重复条目。"
            )
        else:
            QMessageBox.information(self, "去重完成", "没有发现重复的知识条目。")
