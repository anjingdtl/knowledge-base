"""回收站管理界面"""
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QFrame, QVBoxLayout, QHBoxLayout, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QPushButton, QMenu, QStackedWidget, QMessageBox,
    QTextEdit, QDialog, QDialogButtonBox,
)
from PySide6.QtCore import Qt

from src.gui.icons import NAV, icon as make_icon, set_named_icon
from src.gui.empty_state import EmptyState


# 表格列定义
COL_SELECT = 0
COL_TITLE = 1
COL_SIZE = 2
COL_DELETED = 3
TABLE_HEADERS = ["选择", "标题", "大小", "删除时间"]


def _file_graph_service():
    from src.services.db import Database
    from src.services.block_store import BlockStore
    from src.services.file_graph import FileGraphService
    from src.utils.config import Config
    return FileGraphService(Config, Database, BlockStore(db=Database), embedding=None)


def _format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"


class _PreviewDialog(QDialog):
    """MD 文件预览对话框"""

    def __init__(self, title: str, content: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"预览: {title}")
        self.resize(600, 450)
        layout = QVBoxLayout(self)

        text_edit = QTextEdit()
        text_edit.setReadOnly(True)
        text_edit.setPlainText(content)
        layout.addWidget(text_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)


class TrashView(QWidget):
    def __init__(self):
        super().__init__()
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        self.setObjectName("pageSurface")
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        # ---- 顶部标题区 ----
        header_card = QFrame()
        header_card.setObjectName("toolbarCard")
        header_layout = QVBoxLayout(header_card)
        header_layout.setContentsMargins(12, 8, 12, 8)
        header_layout.setSpacing(6)

        top_row = QHBoxLayout()
        top_row.setSpacing(8)

        title_col = QVBoxLayout()
        title_col.setSpacing(0)
        title = QLabel("回收站")
        title.setObjectName("pageTitle")
        title.setMinimumWidth(92)
        self.subtitle_label = QLabel("加载中...")
        self.subtitle_label.setObjectName("pageSubtitle")
        title_col.addWidget(title)
        title_col.addWidget(self.subtitle_label)
        top_row.addLayout(title_col)

        top_row.addStretch()

        self.btn_refresh = QPushButton("刷新")
        self.btn_refresh.setMinimumWidth(70)
        set_named_icon(self.btn_refresh, "refresh", "text_dim", 14)
        self.btn_refresh.clicked.connect(self.refresh)
        top_row.addWidget(self.btn_refresh)

        self.btn_empty = QPushButton("清空回收站")
        self.btn_empty.setObjectName("dangerBtn")
        self.btn_empty.setMinimumWidth(100)
        set_named_icon(self.btn_empty, "delete", "danger", 14)
        self.btn_empty.clicked.connect(self._empty_trash)
        top_row.addWidget(self.btn_empty)

        header_layout.addLayout(top_row)

        # ---- 批量操作栏 ----
        bulk = QHBoxLayout()
        bulk.setContentsMargins(0, 0, 0, 0)
        bulk.setSpacing(6)

        self.selection_label = QLabel("已选择 0 个")
        self.selection_label.setObjectName("hintLabel")
        self.selection_label.setMinimumWidth(82)
        bulk.addWidget(self.selection_label)

        bulk.addStretch()

        btn_select_all = QPushButton("全选")
        btn_select_all.setMinimumWidth(60)
        set_named_icon(btn_select_all, "approve", "text_dim", 14)
        btn_select_all.clicked.connect(self._select_all)
        bulk.addWidget(btn_select_all)

        btn_clear_sel = QPushButton("取消全选")
        btn_clear_sel.setMinimumWidth(76)
        set_named_icon(btn_clear_sel, "close", "text_dim", 13)
        btn_clear_sel.clicked.connect(self._clear_selection)
        bulk.addWidget(btn_clear_sel)

        self.btn_restore = QPushButton("恢复选中")
        self.btn_restore.setMinimumWidth(82)
        set_named_icon(self.btn_restore, "restore", "text_dim", 14)
        self.btn_restore.clicked.connect(self._restore_selected)
        bulk.addWidget(self.btn_restore)

        self.btn_purge = QPushButton("永久删除选中")
        self.btn_purge.setObjectName("dangerBtn")
        self.btn_purge.setMinimumWidth(104)
        set_named_icon(self.btn_purge, "delete", "danger", 14)
        self.btn_purge.clicked.connect(self._purge_selected)
        bulk.addWidget(self.btn_purge)

        header_layout.addLayout(bulk)
        layout.addWidget(header_card)

        # ---- 主内容区（表格 + 空状态） ----
        self.list_stack = QStackedWidget()

        self.table = QTableWidget(0, len(TABLE_HEADERS))
        self.table.setHorizontalHeaderLabels(TABLE_HEADERS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSortingEnabled(True)
        self.table.setAlternatingRowColors(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        self.table.cellDoubleClicked.connect(self._on_double_click)

        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(COL_SELECT, QHeaderView.Fixed)
        self.table.setColumnWidth(COL_SELECT, 48)
        hdr.setSectionResizeMode(COL_TITLE, QHeaderView.Stretch)
        hdr.setSectionResizeMode(COL_SIZE, QHeaderView.Fixed)
        self.table.setColumnWidth(COL_SIZE, 80)
        hdr.setSectionResizeMode(COL_DELETED, QHeaderView.Fixed)
        self.table.setColumnWidth(COL_DELETED, 140)

        self.list_stack.addWidget(self.table)

        self.empty_state = EmptyState(
            title="回收站为空",
            description="删除的知识条目会移入回收站，您可以在此恢复或永久清理",
            icon_key="trash",
        )
        self.list_stack.addWidget(self.empty_state)

        layout.addWidget(self.list_stack, 1)

        # 数据缓存
        self._items: list[dict] = []

    # ---- 公共方法 ----

    def refresh(self):
        """刷新回收站列表"""
        service = _file_graph_service()
        self._items = service.list_trash()
        self._fill_table()
        self._update_subtitle()
        self._update_selection_label()

    # ---- 内部方法 ----

    def _fill_table(self):
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        if not self._items:
            self.list_stack.setCurrentIndex(1)
            self.btn_empty.setEnabled(False)
            return
        self.list_stack.setCurrentIndex(0)
        self.btn_empty.setEnabled(True)

        self.table.setRowCount(len(self._items))
        for row, item in enumerate(self._items):
            # 选择框
            cb = QTableWidgetItem()
            cb.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            cb.setCheckState(Qt.Unchecked)
            self.table.setItem(row, COL_SELECT, cb)
            # 标题
            self.table.setItem(row, COL_TITLE, QTableWidgetItem(item["title"]))
            # 大小
            self.table.setItem(row, COL_SIZE, QTableWidgetItem(_format_size(item["size"])))
            # 删除时间
            self.table.setItem(row, COL_DELETED, QTableWidgetItem(item["deleted_at"]))
        self.table.setSortingEnabled(True)

    def _update_subtitle(self):
        total = len(self._items)
        total_size = sum(item["size"] for item in self._items)
        self.subtitle_label.setText(f"共 {total} 个文件，占用 {_format_size(total_size)}")

    def _get_selected_filenames(self) -> list[str]:
        """返回当前勾选的行对应的 filename 列表"""
        selected = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, COL_SELECT)
            if item and item.checkState() == Qt.Checked:
                selected.append(self._items[row]["filename"])
        return selected

    def _update_selection_label(self):
        count = len(self._get_selected_filenames())
        self.selection_label.setText(f"已选择 {count} 个")

    def _select_all(self):
        self.table.blockSignals(True)
        for row in range(self.table.rowCount()):
            item = self.table.item(row, COL_SELECT)
            if item:
                item.setCheckState(Qt.Checked)
        self.table.blockSignals(False)
        self._update_selection_label()

    def _clear_selection(self):
        self.table.blockSignals(True)
        for row in range(self.table.rowCount()):
            self.table.item(row, COL_SELECT).setCheckState(Qt.Unchecked)
        self.table.blockSignals(False)
        self._update_selection_label()

    # ---- 操作 ----

    def _restore_selected(self):
        filenames = self._get_selected_filenames()
        if not filenames:
            return
        reply = QMessageBox.question(
            self, "确认恢复",
            f"确定恢复选中的 {len(filenames)} 个文件吗？\n恢复后将重新建立索引。",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        service = _file_graph_service()
        failed = []
        for fn in filenames:
            try:
                service.restore_page(fn)
            except Exception as exc:
                failed.append(f"{fn}: {exc}")
        self.refresh()
        if failed:
            QMessageBox.warning(self, "恢复完成", f"部分文件恢复失败：\n" + "\n".join(failed[:10]))
        else:
            QMessageBox.information(self, "恢复完成", f"已恢复 {len(filenames)} 个文件。")

    def _purge_selected(self):
        filenames = self._get_selected_filenames()
        if not filenames:
            return
        reply = QMessageBox.question(
            self, "确认永久删除",
            f"确定永久删除选中的 {len(filenames)} 个文件吗？\n此操作不可恢复。",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        service = _file_graph_service()
        for fn in filenames:
            service.purge_page(fn)
        self.refresh()
        QMessageBox.information(self, "删除完成", f"已永久删除 {len(filenames)} 个文件。")

    def _empty_trash(self):
        total = len(self._items)
        if total == 0:
            return
        reply = QMessageBox.question(
            self, "确认清空回收站",
            f"确定清空回收站中的 {total} 个文件吗？\n此操作不可恢复。",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        service = _file_graph_service()
        count = service.empty_trash()
        self.refresh()
        QMessageBox.information(self, "清空完成", f"已清空 {count} 个文件。")

    # ---- 右键菜单 ----

    def _show_context_menu(self, pos):
        row = self.table.rowAt(pos.y())
        if row < 0:
            return
        item = self._items[row]
        menu = QMenu(self)

        act_restore = menu.addAction(make_icon(NAV["restore"]), "恢复")
        act_restore.triggered.connect(lambda: self._restore_single(item["filename"], row))

        menu.addSeparator()

        act_purge = menu.addAction(make_icon(NAV["delete"]), "永久删除")
        act_purge.triggered.connect(lambda: self._purge_single(item["filename"], row))

        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _restore_single(self, filename: str, row: int):
        reply = QMessageBox.question(
            self, "确认恢复",
            f"确定恢复 \"{self._items[row]['title']}\" 吗？",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        service = _file_graph_service()
        try:
            service.restore_page(filename)
            self.refresh()
            QMessageBox.information(self, "恢复完成", "文件已恢复并重建索引。")
        except Exception as exc:
            QMessageBox.warning(self, "恢复失败", str(exc))

    def _purge_single(self, filename: str, row: int):
        reply = QMessageBox.question(
            self, "确认永久删除",
            f"确定永久删除 \"{self._items[row]['title']}\" 吗？\n此操作不可恢复。",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        service = _file_graph_service()
        service.purge_page(filename)
        self.refresh()

    # ---- 双击预览 ----

    def _on_double_click(self, row: int, _col: int):
        if row < 0 or row >= len(self._items):
            return
        item = self._items[row]
        service = _file_graph_service()
        trash_path = service.ensure_graph() / ".trash" / item["filename"]
        if not trash_path.exists():
            return
        try:
            content = trash_path.read_text(encoding="utf-8")
        except Exception:
            content = "(无法读取文件内容)"
        dlg = _PreviewDialog(item["title"], content, self)
        dlg.exec()
