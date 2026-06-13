"""知识目录浏览视图 — 分类树 + 条目详情"""
from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, Qt, QThread, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.data.classification_schema import CLASSIFICATION_SCHEMA, UNCATEGORIZED
from src.gui.empty_state import EmptyState
from src.gui.icons import set_named_icon
from src.services.db import Database
from src.services.librarian import LibrarianService


def _schema_items() -> list[dict]:
    if isinstance(CLASSIFICATION_SCHEMA, dict):
        items = []
        for code, info in CLASSIFICATION_SCHEMA.items():
            children = info.get("children", []) if isinstance(info, dict) else []
            items.append({
                "code": code,
                "name": info.get("name", code) if isinstance(info, dict) else str(info),
                "description": info.get("description", "") if isinstance(info, dict) else "",
                "subcategories": [
                    {"code": child[0], "name": child[1], "description": ""}
                    if isinstance(child, (tuple, list)) and len(child) >= 2
                    else {"code": str(child), "name": str(child), "description": ""}
                    for child in children
                ],
            })
        return items
    return CLASSIFICATION_SCHEMA


def _format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"


class ClassifyWorker(QThread):
    progress = Signal(str, int, int)
    finished = Signal(list)
    error = Signal(str)

    def __init__(self, incremental=True):
        super().__init__()
        self._incremental = incremental

    def run(self):
        try:
            librarian = LibrarianService()
            categories = librarian.classify_all(
                progress_cb=self._progress,
                incremental=self._incremental,
            )
            self.finished.emit(categories)
        except Exception as e:
            self.error.emit(str(e))

    def _progress(self, phase, current, total):
        self.progress.emit(phase, current, total)


class CatalogView(QWidget):
    def __init__(self, llm_indicator=None):
        super().__init__()
        self._llm_indicator = llm_indicator
        self._worker = None
        self._setup_ui()
        # 目录首次加载延后到 showEvent（避免启动期一次性跑全部 DB 查询）
        self._catalog_loaded = False

    def showEvent(self, event):
        super().showEvent(event)
        if not self._catalog_loaded:
            self._load_catalog()
            self._catalog_loaded = True

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        self.setObjectName("pageSurface")
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        toolbar_card = QFrame()
        toolbar_card.setObjectName("pageHeader")
        toolbar = QHBoxLayout(toolbar_card)
        toolbar.setContentsMargins(16, 12, 16, 12)
        toolbar.setSpacing(8)

        title = QLabel("知识目录")
        title.setObjectName("pageTitle")
        title_col = QVBoxLayout()
        subtitle = QLabel("按业务分类浏览知识，并让 AI 自动整理目录结构")
        subtitle.setObjectName("pageSubtitle")
        title_col.addWidget(title)
        title_col.addWidget(subtitle)
        toolbar.addLayout(title_col)
        toolbar.addStretch()

        self.btn_classify = QPushButton("自动整理")
        self.btn_classify.setObjectName("primaryBtn")
        set_named_icon(self.btn_classify, "classify", "on_accent", 15)
        self.btn_classify.clicked.connect(self._start_classify)
        toolbar.addWidget(self.btn_classify)

        self.btn_catalog = QPushButton("生成目录")
        set_named_icon(self.btn_catalog, "catalog_generate", "text_dim", 15)
        self.btn_catalog.clicked.connect(self._generate_catalog)
        toolbar.addWidget(self.btn_catalog)

        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximumHeight(18)
        self.progress_bar.setVisible(False)
        toolbar.addWidget(self.progress_bar)

        self.status_label = QLabel("")
        self.status_label.setObjectName("hintLabel")
        toolbar.addWidget(self.status_label)

        layout.addWidget(toolbar_card)

        # 左侧：统计 + 分类树
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(12, 8, 8, 8)
        left_layout.setSpacing(0)

        self._stats_widget = self._build_stats_bar()
        left_layout.addWidget(self._stats_widget)

        self.tree_stack = QStackedWidget()

        self.tree = QTreeWidget()
        self.tree.setHeaderLabel("分类目录")
        self.tree.itemClicked.connect(self._on_item_clicked)
        self.tree.itemDoubleClicked.connect(self._on_item_double_clicked)

        self.empty_state = EmptyState(
            title="知识库还是空的",
            description="先导入一些知识，然后点击「自动整理」生成分类目录",
            icon_key="catalog",
        )
        self.empty_unclassified = EmptyState(
            title="知识尚未分类",
            description="知识库有条目但尚未分类，点击「自动整理」生成目录",
            buttons=[
                {"text": "自动整理", "callback": self._start_classify, "objectName": "primaryBtn"},
            ],
            icon_key="classify",
        )

        self.tree_stack.addWidget(self.tree)
        self.tree_stack.addWidget(self.empty_state)
        self.tree_stack.addWidget(self.empty_unclassified)

        left_layout.addWidget(self.tree_stack, 1)
        layout.addWidget(left, 1)

        # 右侧弹出式详情面板
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
        detail_layout.setContentsMargins(12, 12, 12, 12)

        detail_header = QHBoxLayout()
        self.detail_title = QLabel("")
        self.detail_title.setObjectName("sectionLabel")
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
        detail_layout.addWidget(self.detail_meta)

        self.detail_content = QTextEdit()
        self.detail_content.setReadOnly(True)
        detail_layout.addWidget(self.detail_content, 1)

    def _build_stats_bar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("statsCard")
        row = QHBoxLayout(bar)
        row.setContentsMargins(12, 10, 12, 10)
        row.setSpacing(20)

        self.stat_files = QLabel("0 个文件")
        self.stat_files.setObjectName("statValue")
        row.addWidget(self.stat_files)

        self.stat_size = QLabel("0 B")
        self.stat_size.setObjectName("statValue")
        row.addWidget(self.stat_size)

        self.stat_cats = QLabel("0 / 68 分类")
        self.stat_cats.setObjectName("statValue")
        row.addWidget(self.stat_cats)

        row.addStretch()
        return bar

    def _refresh_stats(self):
        stats = Database.get_stats()
        self.stat_files.setText(f"{stats['total_files']} 个文件")
        self.stat_size.setText(_format_size(stats["total_size"]))
        total_cats = 1 + sum(
            1 + len(info.get("subcategories", []))
            for info in _schema_items()
        )
        self.stat_cats.setText(f"{stats['category_coverage']} / {total_cats} 分类")

    def _load_catalog(self):
        self._refresh_stats()
        # 整批构建目录树期间关闭更新，避免大量 QTreeWidgetItem 插入时反复重绘
        self.tree.setUpdatesEnabled(False)
        self.tree.blockSignals(True)
        self.tree.clear()
        categories = Database.get_all_categories()
        {c["id"]: c for c in categories}

        # 预设分类的 code 集合，用于识别动态分类
        schema_codes = set()
        for cat in _schema_items():
            schema_codes.add(cat["code"])
            for sub in cat.get("subcategories", []):
                schema_codes.add(sub["code"])
        schema_codes.add(UNCATEGORIZED["code"])

        # 已匹配到预设分类的 DB category ID 集合
        matched_db_ids = set()

        for schema_cat in _schema_items():
            code = schema_cat["code"]
            cat_db = None
            for c in categories:
                if not c.get("parent_id") and c["name"].startswith(code):
                    cat_db = c
                    matched_db_ids.add(c["id"])
                    break

            if cat_db:
                root_items = Database.get_knowledge_by_category(cat_db["id"])
                count = len(root_items)
                sub_cats = [c for c in categories if c.get("parent_id") == cat_db["id"]]
                for sc in sub_cats:
                    matched_db_ids.add(sc["id"])
                    count += len(Database.get_knowledge_by_category(sc["id"]))
                root_node = QTreeWidgetItem(
                    self.tree,
                    [f"{schema_cat['code']} {schema_cat['name']} ({count})"],
                )
                root_node.setData(0, Qt.UserRole, {"type": "category", "id": cat_db["id"], "data": cat_db})

                for item in root_items:
                    ki = QTreeWidgetItem(root_node, [f"  {item['title']}"])
                    ki.setData(0, Qt.UserRole, {"type": "knowledge", "id": item["id"], "data": item})

                # 先渲染预设子类
                for schema_sub in schema_cat.get("subcategories", []):
                    sub_code = schema_sub["code"]
                    sub_db = None
                    for sc in sub_cats:
                        if sc["name"].startswith(sub_code):
                            sub_db = sc
                            break
                    sub_items = Database.get_knowledge_by_category(sub_db["id"]) if sub_db else []
                    sub_node = QTreeWidgetItem(
                        root_node,
                        [f"  {schema_sub['code']} {schema_sub['name']} ({len(sub_items)})"],
                    )
                    if sub_db:
                        sub_node.setData(0, Qt.UserRole, {"type": "category", "id": sub_db["id"], "data": sub_db})
                    for item in sub_items:
                        ki = QTreeWidgetItem(sub_node, [f"    {item['title']}"])
                        ki.setData(0, Qt.UserRole, {"type": "knowledge", "id": item["id"], "data": item})

                # 再渲染动态子分类（不属于预设子类的）
                preset_sub_codes = {s["code"] for s in schema_cat.get("subcategories", [])}
                for sc in sub_cats:
                    parts = sc["name"].split(" ", 1)
                    sc_code = parts[0] if parts else ""
                    if sc_code not in preset_sub_codes:
                        dyn_items = Database.get_knowledge_by_category(sc["id"])
                        sub_node = QTreeWidgetItem(
                            root_node,
                            [f"  {sc['name']} ({len(dyn_items)})"],
                        )
                        sub_node.setData(0, Qt.UserRole, {"type": "category", "id": sc["id"], "data": sc})
                        for item in dyn_items:
                            ki = QTreeWidgetItem(sub_node, [f"    {item['title']}"])
                            ki.setData(0, Qt.UserRole, {"type": "knowledge", "id": item["id"], "data": item})
            else:
                root_node = QTreeWidgetItem(
                    self.tree,
                    [f"{schema_cat['code']} {schema_cat['name']} (0)"],
                )
                root_node.setData(0, Qt.UserRole, {
                    "type": "category", "id": "",
                    "data": {"name": f"{schema_cat['code']} {schema_cat['name']}",
                             "description": schema_cat["description"]},
                })
                for schema_sub in schema_cat.get("subcategories", []):
                    sub_node = QTreeWidgetItem(
                        root_node,
                        [f"  {schema_sub['code']} {schema_sub['name']} (0)"],
                    )

        # 未分类
        uncategorized_db = None
        for c in categories:
            if not c.get("parent_id") and c["name"].startswith(UNCATEGORIZED["code"]):
                uncategorized_db = c
                matched_db_ids.add(c["id"])
                break
        uncat_items = Database.get_knowledge_by_category(uncategorized_db["id"]) if uncategorized_db else []
        z_node = QTreeWidgetItem(
            self.tree,
            [f"{UNCATEGORIZED['code']} {UNCATEGORIZED['name']} ({len(uncat_items)})"],
        )
        if uncategorized_db:
            z_node.setData(0, Qt.UserRole, {"type": "category", "id": uncategorized_db["id"], "data": uncategorized_db})
        for item in uncat_items:
            ki = QTreeWidgetItem(z_node, [f"  {item['title']}"])
            ki.setData(0, Qt.UserRole, {"type": "knowledge", "id": item["id"], "data": item})

        # 动态分类（不属于预设分类的顶级分类）
        dynamic_roots = [
            c for c in categories
            if c["id"] not in matched_db_ids and not c.get("parent_id")
        ]
        if dynamic_roots:
            dyn_root = QTreeWidgetItem(self.tree, ["自定义分类"])
            dyn_root.setData(0, Qt.UserRole, {"type": "category", "id": "", "data": {"name": "自定义分类"}})
            for dc in dynamic_roots:
                dc_items = Database.get_knowledge_by_category(dc["id"])
                dc_children = [c for c in categories if c.get("parent_id") == dc["id"]]
                count = len(dc_items) + sum(len(Database.get_knowledge_by_category(ch["id"])) for ch in dc_children)
                dc_node = QTreeWidgetItem(dyn_root, [f"  {dc['name']} ({count})"])
                dc_node.setData(0, Qt.UserRole, {"type": "category", "id": dc["id"], "data": dc})
                for item in dc_items:
                    ki = QTreeWidgetItem(dc_node, [f"    {item['title']}"])
                    ki.setData(0, Qt.UserRole, {"type": "knowledge", "id": item["id"], "data": item})
                for ch in dc_children:
                    ch_items = Database.get_knowledge_by_category(ch["id"])
                    ch_node = QTreeWidgetItem(dc_node, [f"    {ch['name']} ({len(ch_items)})"])
                    ch_node.setData(0, Qt.UserRole, {"type": "category", "id": ch["id"], "data": ch})
                    for item in ch_items:
                        ki = QTreeWidgetItem(ch_node, [f"      {item['title']}"])
                        ki.setData(0, Qt.UserRole, {"type": "knowledge", "id": item["id"], "data": item})

        self.tree.expandAll()
        # 整批构建完成，恢复更新 + 信号（一次性重绘）
        self.tree.blockSignals(False)
        self.tree.setUpdatesEnabled(True)
        self.tree.viewport().update()

        # 空状态切换逻辑
        total_items = len(Database.list_knowledge(limit=10000))
        if total_items == 0:
            self.tree_stack.setCurrentIndex(1)  # 知识库为空
        else:
            # 检查是否有已分类的条目
            classified_ids = Database.get_all_classified_ids()
            uncat_ids = LibrarianService()._get_uncategorized_item_ids()
            classified_count = sum(
                1 for it in Database.list_knowledge(limit=10000)
                if it["id"] in classified_ids and it["id"] not in uncat_ids
            )
            if classified_count == 0:
                self.tree_stack.setCurrentIndex(2)  # 有知识但未分类
            else:
                self.tree_stack.setCurrentIndex(0)  # 正常显示树

    def _on_item_clicked(self, item: QTreeWidgetItem, column: int):
        data = item.data(0, Qt.UserRole)
        if not data:
            return

        if data["type"] == "category":
            cat = data["data"]
            items = Database.get_knowledge_by_category(cat["id"])
            self.detail_title.setText(cat["name"])
            self.detail_meta.setText(
                f"描述: {cat.get('description', '无')} | 条目数: {len(items)}"
            )
            content = "\n".join(f"- {it['title']}" for it in items) if items else "（无条目）"
            self.detail_content.setPlainText(content)

        elif data["type"] == "knowledge":
            ki = data["data"]
            self.detail_title.setText(ki["title"])
            cats = Database.get_categories_for_knowledge(ki["id"])
            cat_names = ", ".join(c["name"] for c in cats) if cats else "未分类"
            import_time = ki.get("created_at", "")[:16].replace("T", " ") if ki.get("created_at") else "未知"
            source = ki.get("source_path", "") or ki.get("source_type", "manual")
            self.detail_meta.setText(
                f"类型: {ki.get('file_type', 'txt')} | 来源: {source} | 导入时间: {import_time} | 分类: {cat_names}"
            )
            content = ki.get("content", "")
            self.detail_content.setPlainText(content[:10000] if len(content) > 10000 else content)

    def _on_item_double_clicked(self, item: QTreeWidgetItem, column: int):
        """双击：弹出右侧详情面板"""
        data = item.data(0, Qt.UserRole)
        if not data:
            return
        self._show_detail_panel(data)

    def _show_detail_panel(self, data: dict):
        """弹出右侧详情面板并显示内容"""
        # 停止旧动画
        if self._detail_anim is not None:
            self._detail_anim.stop()
            self._detail_anim = None

        if data["type"] == "category":
            cat = data["data"]
            items = Database.get_knowledge_by_category(cat["id"]) if cat.get("id") else []
            self.detail_title.setText(cat["name"])
            self.detail_meta.setText(
                f"描述: {cat.get('description', '无')} | 条目数: {len(items)}"
            )
            content = "\n".join(f"- {it['title']}" for it in items) if items else "（无条目）"
            self.detail_content.setPlainText(content)
        elif data["type"] == "knowledge":
            ki = data["data"]
            self.detail_title.setText(ki["title"])
            cats = Database.get_categories_for_knowledge(ki["id"])
            cat_names = ", ".join(c["name"] for c in cats) if cats else "未分类"
            import_time = ki.get("created_at", "")[:16].replace("T", " ") if ki.get("created_at") else "未知"
            source = ki.get("source_path", "") or ki.get("source_type", "manual")
            self.detail_meta.setText(
                f"类型: {ki.get('file_type', 'txt')} | 来源: {source} | 导入时间: {import_time} | 分类: {cat_names}"
            )
            content = ki.get("content", "")
            self.detail_content.setPlainText(content[:10000] if len(content) > 10000 else content)

        self.detail_panel.setFixedHeight(self.height())
        self.detail_panel.setVisible(True)
        self.detail_panel.raise_()

        target_x = self.width() - self._detail_width
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
        """视图被隐藏时停止动画、收回面板"""
        super().hideEvent(event)
        if self._detail_anim is not None:
            self._detail_anim.stop()
            self._detail_anim = None
        try:
            self.detail_panel.setVisible(False)
        except RuntimeError:
            pass
        self._detail_open = False

    def _start_classify(self):
        if self._worker and self._worker.isRunning():
            return

        # 增量检查：是否还有未分类条目（包括"Z 未分类"中的条目）
        from src.services.librarian import LibrarianService
        all_items = Database.list_knowledge(limit=10000)
        classified_ids = Database.get_all_classified_ids()
        uncat_ids = LibrarianService()._get_uncategorized_item_ids()
        unclassified_count = sum(
            1 for it in all_items
            if it["id"] not in classified_ids or it["id"] in uncat_ids
        )

        incremental = True
        if unclassified_count == 0:
            total = len(all_items)
            reply = QMessageBox.question(
                self, "全部分类完毕",
                f"所有 {total} 条知识已分类完毕。\n是否强制重新全量分类？",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return
            incremental = False
            unclassified_count = total

        self.btn_classify.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, unclassified_count)
        self.progress_bar.setValue(0)
        mode_text = "新增" if incremental else "全量"
        self.status_label.setText(f"正在分类 {unclassified_count} 条{mode_text}知识...")

        if self._llm_indicator:
            self._llm_indicator.set_status("running", "知识分类")

        self._worker = ClassifyWorker(incremental=incremental)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_classify_finished)
        self._worker.error.connect(self._on_classify_error)
        self._worker.start()

    def _on_progress(self, phase, current, total):
        self.status_label.setText(f"{phase} ({current}/{total})")
        if total > 0:
            self.progress_bar.setMaximum(total)
            self.progress_bar.setValue(min(current, total))

    def _on_classify_finished(self, categories):
        self.btn_classify.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.status_label.setText(f"整理完成，共 {len(categories)} 个类别")
        if self._llm_indicator:
            self._llm_indicator.set_status("idle")
        self._load_catalog()

    def _on_classify_error(self, error_msg):
        self.btn_classify.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.status_label.setText(f"错误: {error_msg}")
        if self._llm_indicator:
            self._llm_indicator.set_status("error", error_msg)
        QMessageBox.warning(self, "分类失败", f"知识库整理失败:\n{error_msg}")

    def _generate_catalog(self):
        try:
            librarian = LibrarianService()
            catalog = librarian.generate_catalog()
            self.detail_title.setText("知识库目录")
            self.detail_meta.setText("自动生成的图书馆式目录")
            self.detail_content.setPlainText(catalog)
            # 显示详情面板（带滑入动画）
            self.detail_panel.setFixedHeight(self.height())
            self.detail_panel.setVisible(True)
            self.detail_panel.raise_()
            target_x = self.width() - self._detail_width
            if self._detail_anim is not None:
                self._detail_anim.stop()
                self._detail_anim = None
            anim = QPropertyAnimation(self.detail_panel, b"pos")
            anim.setDuration(220)
            anim.setStartValue(QPoint(self.width(), 0))
            anim.setEndValue(QPoint(target_x, 0))
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            anim.start()
            self._detail_anim = anim
            self._detail_open = True
        except Exception as e:
            QMessageBox.warning(self, "生成目录失败", f"无法生成知识库目录:\n{str(e)}")
