"""Wiki 页面浏览器视图"""
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QListWidget, QListWidgetItem, QLabel,
    QPushButton, QLineEdit, QTextEdit, QComboBox,
    QMessageBox, QStackedWidget, QFrame,
    QFileDialog,
)
from PySide6.QtCore import Qt, QThread, Signal, QPropertyAnimation, QEasingCurve, QPoint
from PySide6.QtGui import QColor

from src.services.db import Database
from src.services.wiki_compiler import WikiCompiler
from src.services.wiki_lint import WikiLint
from src.gui.icons import set_named_icon
from src.gui.theme import get_color
from src.utils.config import Config
from src.gui.empty_state import EmptyState

import json
import re


def _safe_wiki_filename(title: str) -> str:
    """将 Wiki 标题转为安全的 Markdown 文件名"""
    base = re.sub(r'[<>:"/\\|?*]', "_", (title or "").strip())
    base = re.sub(r"_+", "_", base).strip(" ._")
    if not base:
        base = "untitled-wiki"
    return f"{base[:120]}.md"


def _wiki_to_markdown(page: dict) -> str:
    """将 Wiki 页面数据转为 Markdown 文本"""
    title = page.get("title") or "未命名 Wiki"
    tags = json.loads(page.get("tags", "[]")) if isinstance(page.get("tags"), str) else page.get("tags", [])
    source_ids = json.loads(page.get("source_ids", "[]")) if isinstance(page.get("source_ids"), str) else page.get("source_ids", [])
    status = page.get("status", "active")
    score = page.get("lint_score", 1.0)
    summary = page.get("concept_summary", "")
    content = page.get("content", "")

    lines = [
        f"# {title}",
        "",
        "## 元数据",
        "",
        f"- ID: {page.get('id', '')}",
        f"- 状态: {status}",
        f"- 健康分: {score:.0%}",
        f"- 标签: {', '.join(tags) if tags else '无'}",
        f"- 来源知识: {len(source_ids)} 条",
        f"- 更新时间: {page.get('updated_at', '')}",
    ]
    if summary:
        lines += ["", "## 摘要", "", summary]
    lines += ["", "## 正文", "", content, ""]
    return "\n".join(lines)


class WikiLintWorker(QThread):
    finished = Signal(dict)
    error = Signal(str)

    def run(self):
        try:
            linter = WikiLint()
            report = linter.run()
            self.finished.emit(report)
        except Exception as e:
            self.error.emit(str(e))


class WikiRepairWorker(QThread):
    """后台线程：调用 LLM 修复 Wiki 死链"""
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, max_pages: int = 50, parent=None):
        super().__init__(parent)
        self._max_pages = max_pages

    def run(self):
        try:
            compiler = WikiCompiler()
            result = compiler.repair_dead_references(max_pages=self._max_pages)
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))


class WikiView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._compiler = None  # 首次 showEvent 时再实例化（避免启动期重活）
        self._lint_worker = None
        self._repair_worker = None
        self._lint_findings = {}  # page_id → [findings]
        self._setup_ui()
        self._pages_loaded = False

    def showEvent(self, event):
        super().showEvent(event)
        if not self._pages_loaded:
            if self._compiler is None:
                self._compiler = WikiCompiler()
            self._load_pages()
            self._pages_loaded = True

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

        title = QLabel("知识 Wiki")
        title.setObjectName("pageTitle")
        title_col = QVBoxLayout()
        subtitle = QLabel("管理从问答沉淀出的结构化知识页面和发布状态")
        subtitle.setObjectName("pageSubtitle")
        title_col.addWidget(title)
        title_col.addWidget(subtitle)
        toolbar.addLayout(title_col)
        toolbar.addStretch()

        self.search_input = QLineEdit()
        self.search_input.setProperty("search", True)
        self.search_input.setPlaceholderText("搜索 Wiki...")
        self.search_input.setMaximumWidth(220)
        self.search_input.textChanged.connect(self._on_search)
        toolbar.addWidget(self.search_input)

        self.status_combo = QComboBox()
        self.status_combo.addItem("全部状态", "")
        self.status_combo.addItem("草稿", "draft")
        self.status_combo.addItem("审核中", "review")
        self.status_combo.addItem("已发布", "published")
        self.status_combo.addItem("已弃用", "deprecated")
        # 向后兼容旧状态
        self.status_combo.addItem("活跃(旧)", "active")
        self.status_combo.addItem("孤立(旧)", "orphan")
        self.status_combo.currentIndexChanged.connect(self._load_pages)
        toolbar.addWidget(self.status_combo)

        self.btn_lint = QPushButton("知识体检")
        set_named_icon(self.btn_lint, "lint", "text_dim", 15)
        self.btn_lint.clicked.connect(self._run_lint)
        toolbar.addWidget(self.btn_lint)

        self.btn_repair = QPushButton("修复死链")
        set_named_icon(self.btn_repair, "link", "text_dim", 15)
        self.btn_repair.setToolTip("使用 LLM 智能修复 Wiki 页面中的 [[死链]] 引用")
        self.btn_repair.clicked.connect(self._run_repair)
        toolbar.addWidget(self.btn_repair)

        self.stats_label = QLabel("")
        self.stats_label.setObjectName("hintLabel")
        toolbar.addWidget(self.stats_label)

        layout.addWidget(toolbar_card)

        # 批量操作栏（全选 / 清空 / 选择计数 / 导出 / 删除）
        bulk_card = QFrame()
        bulk_card.setObjectName("bulkBar")
        bulk_row = QHBoxLayout(bulk_card)
        bulk_row.setContentsMargins(16, 6, 16, 6)
        bulk_row.setSpacing(8)

        self.selection_label = QLabel("已选择 0 条")
        self.selection_label.setObjectName("hintLabel")
        bulk_row.addWidget(self.selection_label)

        btn_select_all = QPushButton("全选")
        btn_select_all.setFixedHeight(28)
        btn_select_all.clicked.connect(self._select_all)
        bulk_row.addWidget(btn_select_all)

        btn_clear_sel = QPushButton("清空选择")
        btn_clear_sel.setFixedHeight(28)
        btn_clear_sel.clicked.connect(self._clear_selection)
        bulk_row.addWidget(btn_clear_sel)

        bulk_row.addStretch()

        self.btn_export_md = QPushButton("导出 MD")
        set_named_icon(self.btn_export_md, "export", "text_dim", 14)
        self.btn_export_md.setFixedHeight(28)
        self.btn_export_md.clicked.connect(self._export_selected_md)
        self.btn_export_md.setEnabled(False)
        bulk_row.addWidget(self.btn_export_md)

        self.btn_batch_delete = QPushButton("批量删除")
        set_named_icon(self.btn_batch_delete, "delete", "danger", 14)
        self.btn_batch_delete.setFixedHeight(28)
        self.btn_batch_delete.clicked.connect(self._batch_delete)
        self.btn_batch_delete.setEnabled(False)
        bulk_row.addWidget(self.btn_batch_delete)

        self._bulk_actions = [self.btn_export_md, self.btn_batch_delete]
        layout.addWidget(bulk_card)

        # 左侧页面列表（用 QStackedWidget 包裹，支持空状态切换）
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(12, 8, 8, 8)

        self.page_stack = QStackedWidget()

        self.page_list = QListWidget()
        self.page_list.setSelectionMode(QListWidget.SelectionMode.MultiSelection)
        self.page_list.currentItemChanged.connect(self._on_page_selected)
        self.page_list.itemDoubleClicked.connect(self._on_page_double_clicked)
        self.page_list.itemSelectionChanged.connect(self._on_selection_changed)

        self.empty_state = EmptyState(
            title="还没有 Wiki 页面",
            description="在智能问答中保存好的回答，或让 AI 自动沉淀知识",
            icon_key="wiki",
        )

        self.page_stack.addWidget(self.page_list)     # page 0
        self.page_stack.addWidget(self.empty_state)    # page 1

        left_layout.addWidget(self.page_stack)
        layout.addWidget(left, 1)

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
        detail_layout.setContentsMargins(12, 12, 12, 12)

        # 顶部：关闭按钮 + 标题
        detail_header = QHBoxLayout()
        self.detail_title = QLabel("双击页面查看详情")
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

        self.detail_summary = QLabel("")
        self.detail_summary.setObjectName("hintLabel")
        self.detail_summary.setWordWrap(True)
        detail_layout.addWidget(self.detail_summary)

        self.detail_meta = QLabel("")
        self.detail_meta.setObjectName("hintLabel")
        self.detail_meta.setWordWrap(True)
        detail_layout.addWidget(self.detail_meta)

        self.detail_content = QTextEdit()
        self.detail_content.setReadOnly(True)
        detail_layout.addWidget(self.detail_content, 1)

        self.detail_links = QLabel("")
        self.detail_links.setWordWrap(True)
        self.detail_links.setObjectName("hintLabel")
        detail_layout.addWidget(self.detail_links)

        self.detail_backlinks = QLabel("")
        self.detail_backlinks.setWordWrap(True)
        self.detail_backlinks.setObjectName("hintLabel")
        detail_layout.addWidget(self.detail_backlinks)

        btn_row = QHBoxLayout()
        self.btn_delete_page = QPushButton("删除页面")
        self.btn_delete_page.setObjectName("dangerBtn")
        set_named_icon(self.btn_delete_page, "delete", "danger", 14)
        self.btn_delete_page.setEnabled(False)
        self.btn_delete_page.clicked.connect(self._delete_page)
        btn_row.addStretch()
        btn_row.addWidget(self.btn_delete_page)
        detail_layout.addLayout(btn_row)

        # Workflow 控制按钮
        workflow_row = QHBoxLayout()
        self.btn_submit_review = QPushButton("提交审核")
        set_named_icon(self.btn_submit_review, "upload", "text_dim", 14)
        self.btn_submit_review.setEnabled(False)
        self.btn_submit_review.clicked.connect(self._submit_review)
        self.btn_approve = QPushButton("批准发布")
        set_named_icon(self.btn_approve, "approve", "text_dim", 14)
        self.btn_approve.setEnabled(False)
        self.btn_approve.clicked.connect(self._approve_page)
        self.btn_reject = QPushButton("驳回")
        set_named_icon(self.btn_reject, "reject", "text_dim", 14)
        self.btn_reject.setEnabled(False)
        self.btn_reject.clicked.connect(self._reject_page)
        self.btn_deprecate = QPushButton("弃用")
        set_named_icon(self.btn_deprecate, "delete", "danger", 14)
        self.btn_deprecate.setEnabled(False)
        self.btn_deprecate.clicked.connect(self._deprecate_page)
        workflow_row.addWidget(self.btn_submit_review)
        workflow_row.addWidget(self.btn_approve)
        workflow_row.addWidget(self.btn_reject)
        workflow_row.addWidget(self.btn_deprecate)
        detail_layout.addLayout(workflow_row)

    def _load_pages(self):
        self.page_list.clear()
        self._on_selection_changed()
        status = self.status_combo.currentData() or None
        pages = Database.list_wiki_pages(status=status, sort_by="updated_at", limit=200)
        for p in pages:
            title = p['title']
            s = p.get("status", "active")
            score = p.get("lint_score", 1.0)
            badge_map = {
                "active": "活跃",
                "draft": "草稿",
                "review": "审核",
                "published": "发布",
                "orphan": "孤立",
                "deprecated": "弃用",
            }
            badge = badge_map.get(s, s)
            if score >= 0.8:
                score_label = "优"
            elif score >= 0.5:
                score_label = "良"
            else:
                score_label = "待修复"

            # 问题摘要
            findings = self._lint_findings.get(p["id"], [])
            issue_tag = ""
            if findings:
                cats = [f["category"] for f in findings]
                cat_labels = {
                    "orphan": "孤立", "stale": "过时", "empty": "空洞",
                    "duplicate": "重复", "broken_link": "坏链", "contradiction": "矛盾",
                }
                unique_cats = list(dict.fromkeys(cats))
                issue_tag = " ⚠" + "/".join(cat_labels.get(c, c) for c in unique_cats)

            label = f"[{badge}] {title}{issue_tag}  {score_label} {int(score * 100)}"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, p)

            # 问题条目标红
            if findings:
                has_error = any(f["severity"] == "error" for f in findings)
                color = QColor(get_color("danger")) if has_error else QColor(get_color("indicator_running"))
                item.setForeground(color)
                # tooltip 显示问题详情
                tips = [f"[{f['severity']}] {f['message']}" for f in findings]
                item.setToolTip("\n".join(tips))

            self.page_list.addItem(item)
        self.stats_label.setText(f"共 {len(pages)} 个 Wiki 页面")

        # 空状态切换
        if len(pages) == 0:
            self.page_stack.setCurrentIndex(1)
        else:
            self.page_stack.setCurrentIndex(0)

    def _on_search(self):
        search = self.search_input.text().strip()
        self.page_list.clear()
        status = self.status_combo.currentData() or None
        if search:
            pages = Database.list_wiki_pages(status=status, search=search, limit=100)
        else:
            pages = Database.list_wiki_pages(status=status, limit=100)
        for p in pages:
            tags = json.loads(p.get("tags", "[]"))
            label = f"{p['title']}"
            if tags:
                label += f"  [{', '.join(tags[:3])}]"
            findings = self._lint_findings.get(p["id"], [])
            if findings:
                label += " ⚠"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, p)
            if findings:
                has_error = any(f["severity"] == "error" for f in findings)
                color = QColor(get_color("danger")) if has_error else QColor(get_color("indicator_running"))
                item.setForeground(color)
                tips = [f"[{f['severity']}] {f['message']}" for f in findings]
                item.setToolTip("\n".join(tips))
            self.page_list.addItem(item)

        # 空状态切换
        if len(pages) == 0:
            self.page_stack.setCurrentIndex(1)
        else:
            self.page_stack.setCurrentIndex(0)

    def _on_page_selected(self, current: QListWidgetItem, previous: QListWidgetItem):
        """单击：只填充详情内容（不弹面板）"""
        if not current:
            return
        page = current.data(Qt.UserRole)
        self._fill_page_detail(page)

    def _on_page_double_clicked(self, item: QListWidgetItem):
        """双击：弹出右侧详情面板"""
        page = item.data(Qt.UserRole)
        if not page:
            return
        self._fill_page_detail(page)
        self._show_detail_panel()

    def _show_detail_panel(self):
        """弹出右侧详情面板"""
        if self._detail_anim is not None:
            self._detail_anim.stop()
            self._detail_anim = None

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

    def _fill_page_detail(self, page: dict):
        if not page:
            return
        self.detail_title.setText(page.get("title", ""))
        self.detail_summary.setText(page.get("concept_summary", ""))

        source_ids = json.loads(page.get("source_ids", "[]"))
        tags = json.loads(page.get("tags", "[]"))
        score = page.get("lint_score", 1.0)
        status = page.get("status", "active")
        meta_parts = [
            f"状态: {status}",
            f"健康分: {score:.0%}",
            f"标签: {', '.join(tags) if tags else '无'}",
            f"来源: {len(source_ids)} 条",
        ]
        self.detail_meta.setText("  |  ".join(meta_parts))

        self.detail_content.setPlainText(page.get("content", "")[:2000])

        # 链接
        links = Database.get_links_for_page(page["id"])
        if links:
            link_strs = [f"{l['target_title']}({l['link_type']})" for l in links[:10]]
            self.detail_links.setText("引用 → " + "、".join(link_strs))
        else:
            self.detail_links.setText("引用 → 无")

        # 反向链接
        backlinks = Database.get_backlinks(page["id"])
        if backlinks:
            bl_strs = [b["source_title"] for b in backlinks[:10]]
            self.detail_backlinks.setText("被引用 ← " + "、".join(bl_strs))
        else:
            self.detail_backlinks.setText("被引用 ← 无")

        self.btn_delete_page.setEnabled(True)
        self.btn_delete_page._page_id = page["id"]

        # 更新 workflow 按钮状态
        self._update_workflow_buttons(status, page["id"])

    def _update_workflow_buttons(self, status: str, page_id: str):
        """根据页面状态更新 workflow 按钮可用性"""
        # 保存当前页面 ID
        self.btn_submit_review._page_id = page_id
        self.btn_approve._page_id = page_id
        self.btn_reject._page_id = page_id
        self.btn_deprecate._page_id = page_id

        # 根据状态启用/禁用按钮
        if status == "draft":
            self.btn_submit_review.setEnabled(True)
            self.btn_approve.setEnabled(False)
            self.btn_reject.setEnabled(False)
            self.btn_deprecate.setEnabled(False)
        elif status == "review":
            self.btn_submit_review.setEnabled(False)
            self.btn_approve.setEnabled(True)
            self.btn_reject.setEnabled(True)
            self.btn_deprecate.setEnabled(False)
        elif status == "published":
            self.btn_submit_review.setEnabled(False)
            self.btn_approve.setEnabled(False)
            self.btn_reject.setEnabled(False)
            self.btn_deprecate.setEnabled(True)
        elif status == "deprecated":
            self.btn_submit_review.setEnabled(False)
            self.btn_approve.setEnabled(True)
            self.btn_reject.setEnabled(False)
            self.btn_deprecate.setEnabled(False)
        else:
            # 向后兼容旧状态
            if status == "active":
                self.btn_submit_review.setEnabled(False)
                self.btn_approve.setEnabled(False)
                self.btn_reject.setEnabled(False)
                self.btn_deprecate.setEnabled(True)
            else:
                # orphan 等其他状态
                self.btn_submit_review.setEnabled(False)
                self.btn_approve.setEnabled(False)
                self.btn_reject.setEnabled(False)
                self.btn_deprecate.setEnabled(False)

    def _submit_review(self):
        """提交审核"""
        page_id = getattr(self.btn_submit_review, "_page_id", None)
        if not page_id:
            return
        from src.services.wiki_workflow import WikiWorkflow
        result = WikiWorkflow.submit_for_review(page_id, operator="gui_user")
        if result.success:
            QMessageBox.information(self, "成功", "已提交审核")
            self._load_pages()
        else:
            QMessageBox.warning(self, "失败", result.message)

    def _approve_page(self):
        """批准发布"""
        page_id = getattr(self.btn_approve, "_page_id", None)
        if not page_id:
            return
        from src.services.wiki_workflow import WikiWorkflow
        result = WikiWorkflow.approve(page_id, operator="gui_user")
        if result.success:
            QMessageBox.information(self, "成功", "已批准发布")
            self._load_pages()
        else:
            QMessageBox.warning(self, "失败", result.message)

    def _reject_page(self):
        """驳回"""
        page_id = getattr(self.btn_reject, "_page_id", None)
        if not page_id:
            return
        from src.services.wiki_workflow import WikiWorkflow
        result = WikiWorkflow.reject(page_id, operator="gui_user")
        if result.success:
            QMessageBox.information(self, "成功", "已驳回")
            self._load_pages()
        else:
            QMessageBox.warning(self, "失败", result.message)

    def _deprecate_page(self):
        """弃用页面"""
        page_id = getattr(self.btn_deprecate, "_page_id", None)
        if not page_id:
            return
        reply = QMessageBox.question(
            self, "确认弃用", "确定弃用此 Wiki 页面？弃用后用户仍可查看但不会在搜索中优先显示。",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            from src.services.wiki_workflow import WikiWorkflow
            result = WikiWorkflow.deprecate(page_id, operator="gui_user")
            if result.success:
                QMessageBox.information(self, "成功", "已弃用")
                self._load_pages()
            else:
                QMessageBox.warning(self, "失败", result.message)

    def _delete_page(self):
        page_id = getattr(self.btn_delete_page, "_page_id", None)
        if not page_id:
            return
        reply = QMessageBox.question(
            self, "确认删除", "确定删除此 Wiki 页面？",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            Database.delete_wiki_page(page_id)
            self._hide_detail_panel()
            self._load_pages()

    def _on_selection_changed(self):
        """多选状态变化时更新批量操作按钮状态"""
        count = len(self.page_list.selectedItems())
        self.selection_label.setText(f"已选择 {count} 条")
        for btn in self._bulk_actions:
            btn.setEnabled(count > 0)

    def _select_all(self):
        """全选当前列表中所有 Wiki 页面"""
        self.page_list.blockSignals(True)
        for i in range(self.page_list.count()):
            self.page_list.item(i).setSelected(True)
        self.page_list.blockSignals(False)
        self._on_selection_changed()

    def _clear_selection(self):
        """清空所有选择"""
        self.page_list.blockSignals(True)
        self.page_list.clearSelection()
        self.page_list.blockSignals(False)
        self._on_selection_changed()

    def _export_selected_md(self):
        """将选中的 Wiki 页面批量导出为 Markdown 文件"""
        selected = self.page_list.selectedItems()
        if not selected:
            return
        folder = QFileDialog.getExistingDirectory(self, "选择导出目录")
        if not folder:
            return
        out_dir = Path(folder)
        exported = 0
        failed = []
        used_names = set()
        for item in selected:
            page = item.data(Qt.UserRole)
            if not page:
                continue
            title = page.get("title", "untitled")
            # 安全文件名
            filename = _safe_wiki_filename(title)
            if filename in used_names or (out_dir / filename).exists():
                stem = Path(filename).stem
                filename = f"{stem}-{page['id'][:8]}.md"
            used_names.add(filename)
            try:
                md = _wiki_to_markdown(page)
                (out_dir / filename).write_text(md, encoding="utf-8")
                exported += 1
            except Exception as exc:
                failed.append(f"{title}: {exc}")
        msg = f"成功导出 {exported} 个 Wiki 页面到:\n{folder}"
        if failed:
            msg += f"\n\n失败 {len(failed)} 个:\n" + "\n".join(failed[:5])
        QMessageBox.information(self, "导出完成", msg)

    def _batch_delete(self):
        """批量删除选中的 Wiki 页面"""
        selected = self.page_list.selectedItems()
        if not selected:
            return
        ids = []
        titles = []
        for item in selected:
            page = item.data(Qt.UserRole)
            if page:
                ids.append(page["id"])
                titles.append(page.get("title", ""))
        if not ids:
            return
        preview = "\n".join(f"  · {t}" for t in titles[:10])
        if len(titles) > 10:
            preview += f"\n  ... 等 {len(titles)} 个"
        reply = QMessageBox.question(
            self, "确认批量删除",
            f"确定删除以下 {len(ids)} 个 Wiki 页面？\n{preview}",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        deleted = 0
        for pid in ids:
            try:
                Database.delete_wiki_page(pid)
                deleted += 1
            except Exception:
                pass
        self._hide_detail_panel()
        self._lint_findings = {}
        self._load_pages()
        if deleted > 0:
            QMessageBox.information(self, "完成", f"已删除 {deleted} 个 Wiki 页面")

    def _run_lint(self):
        if self._lint_worker and self._lint_worker.isRunning():
            return
        if not Config.get("wiki.enabled", False):
            QMessageBox.warning(self, "提示", "Wiki 功能未启用")
            return
        self.btn_lint.setEnabled(False)
        self.stats_label.setText("正在体检...")
        self._lint_worker = WikiLintWorker()
        self._lint_worker.finished.connect(self._on_lint_done)
        self._lint_worker.error.connect(lambda e: (
            self.stats_label.setText(f"体检失败: {e[:50]}"),
            self.btn_lint.setEnabled(True),
        ))
        self._lint_worker.start()

    def _on_lint_done(self, report: dict):
        self.btn_lint.setEnabled(True)
        total = report.get("total_pages", 0)
        healthy = report.get("healthy_pages", 0)
        score = report.get("score", 1.0)
        findings = report.get("findings", [])
        self.stats_label.setText(f"体检完成: {healthy}/{total} 健康 · 得分 {score:.0%}")

        # 存储问题映射（page_id → findings）
        self._lint_findings = {}
        for f in findings:
            self._lint_findings.setdefault(f["page_id"], []).append(f)

        if findings:
            lines = [f"共发现 {len(findings)} 个问题：\n"]
            for f in findings[:20]:
                severity = "警告" if f["severity"] == "warning" else "错误" if f["severity"] == "error" else "信息"
                lines.append(f"[{severity}] [{f['category']}] {f['page_title']}: {f['message']}")

            # 显示批量删除按钮
            self.btn_batch_delete.setVisible(True)

            QMessageBox.information(self, "知识体检报告", "\n".join(lines))
        else:
            self.btn_batch_delete.setVisible(False)
            QMessageBox.information(self, "知识体检报告", "所有 Wiki 页面健康，未发现问题！")

        self._load_pages()

    def _run_repair(self):
        """启动 LLM 死链修复（后台线程）"""
        if self._repair_worker and self._repair_worker.isRunning():
            return
        if not Config.get("wiki.enabled", False):
            QMessageBox.warning(self, "提示", "Wiki 功能未启用")
            return

        # 先快速扫描死链数量，让用户确认
        from src.services.wiki_compiler import _WIKI_LINK_RE
        pages = Database.list_wiki_pages(limit=500)
        if not pages:
            QMessageBox.information(self, "修复死链", "当前没有 Wiki 页面，无需修复。")
            return

        all_titles = {p["title"] for p in pages}
        dead_count = 0
        for page in pages:
            content = page.get("content", "") or ""
            for m in _WIKI_LINK_RE.finditer(content):
                if m.group(1).strip() not in all_titles:
                    dead_count += 1

        if dead_count == 0:
            QMessageBox.information(self, "修复死链",
                                    f"扫描了 {len(pages)} 个 Wiki 页面，未发现死链，所有引用均有效！")
            return

        reply = QMessageBox.question(
            self, "确认修复",
            f"扫描到 {len(pages)} 个页面中共有 {dead_count} 个死链。\n\n"
            f"将调用 LLM 智能分析并修复（重定向、创建占位页或移除标记）。\n"
            f"确定开始修复？",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        self.btn_repair.setEnabled(False)
        self.stats_label.setText(f"正在修复 {dead_count} 个死链...")
        self._repair_worker = WikiRepairWorker(max_pages=50)
        self._repair_worker.finished.connect(self._on_repair_done)
        self._repair_worker.error.connect(self._on_repair_error)
        self._repair_worker.start()

    def _on_repair_done(self, result: dict):
        self.btn_repair.setEnabled(True)
        status = result.get("status", "")
        scanned = result.get("scanned", 0)

        if status == "clean":
            self.stats_label.setText("修复完成: 无死链")
            QMessageBox.information(self, "修复死链",
                                    f"扫描了 {scanned} 个页面，未发现死链。")
        elif status == "empty":
            self.stats_label.setText("修复完成: 无页面")
            QMessageBox.information(self, "修复死链", "当前没有 Wiki 页面。")
        else:
            redirects = result.get("redirects", 0)
            stubs = result.get("stubs", 0)
            removes = result.get("removes", 0)
            errors = result.get("errors", 0)
            fixed = result.get("fixed", 0)
            self.stats_label.setText(f"修复完成: {fixed} 处死链已修复")

            lines = [
                f"修复完成！共处理 {fixed} 处死链：\n",
                f"  · 重定向到已有页面: {redirects}",
                f"  · 创建占位页面: {stubs}",
                f"  · 移除引用标记: {removes}",
            ]
            if errors:
                lines.append(f"\n  ⚠ 失败: {errors} 处（可稍后重试）")
            QMessageBox.information(self, "修复死链", "\n".join(lines))

        # 清空旧的 lint 结果并重新加载页面
        self._lint_findings = {}
        self._load_pages()

    def _on_repair_error(self, error_msg: str):
        self.btn_repair.setEnabled(True)
        self.stats_label.setText(f"修复失败: {error_msg[:50]}")
        QMessageBox.warning(self, "修复失败", f"LLM 修复过程出错：\n\n{error_msg}")
