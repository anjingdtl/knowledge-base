"""文件导入对话框"""
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QListWidget,
    QPushButton, QLabel, QLineEdit,
    QFileDialog, QMessageBox, QTextEdit, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView,
    QTabWidget, QWidget,
)
from PySide6.QtCore import Qt, QThread, Signal, QMimeData
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QPainter, QColor, QFont
import os
from datetime import datetime

from src.services.file_parser import parse_file
from src.services.db import Database
from src.services.block_store import BlockStore
from src.services.file_graph import FileGraphService
from src.services.llm import LLMService
from src.gui.icons import set_named_icon
from src.gui.theme import get_color
from src.utils.config import Config

TITLE_PROMPT = """你是一个标题生成助手。请根据文件名和文件内容判断文件名是否已能准确概括内容。

## 输出格式
严格输出 JSON：
```json
{{"needs_supplement": false}}
```
或
```json
{{"needs_supplement": true, "supplement": "补充说明（不超过20字）"}}
```

## 判断规则
1. 如果文件名已经能清晰表达内容主题（如"API接口文档"、"用户手册"），输出 needs_supplement=false
2. 如果文件名过于模糊（如"数据"、"报告"、"新建文档"）或文件名与内容核心主题差异大，输出 needs_supplement=true 并提供简短补充
3. 补充内容要精炼，突出内容的核心主题，不要重复文件名已有信息
4. 只输出 JSON，不要输出任何解释

## 文件名
{filename}

## 内容摘要
{content}"""


def assemble_title(filename: str, result: dict) -> str:
    """根据 LLM 判断结果拼装标题：文件名优先，必要时追加补充"""
    needs_supplement = result.get("needs_supplement", False)
    supplement = result.get("supplement", "").strip()
    if not needs_supplement or not supplement:
        return filename[:60]
    title = f"{filename}（{supplement}）"
    return title[:60]


def generate_title(content: str, filename: str = None) -> str:
    """用 LLM 判断文件名是否需要补充，返回以文件名为基础的标题"""
    snippet = content[:800] if content else ""
    if not snippet.strip() or not filename:
        return filename or ""
    try:
        llm = LLMService()
        prompt = TITLE_PROMPT.format(filename=filename, content=snippet)
        raw = llm.chat([{"role": "user", "content": prompt}], silent=True)
        text = _strip_think(raw).strip()
        import json, re
        json_match = re.search(r'\{[^{}]+\}', text)
        if json_match:
            result = json.loads(json_match.group())
            return assemble_title(filename, result)
        return filename[:60]
    except Exception:
        return filename[:60]


def _file_graph_service() -> FileGraphService:
    return FileGraphService(Config, Database, BlockStore(db=Database), embedding=None)


def _strip_think(text: str) -> str:
    """移除 LLM 返回中的 <?xml version="1.0" encoding="UTF-8"?>...<?xml version="1.0" encoding="UTF-8"?> 思维链"""
    from src.utils.llm_text import strip_think
    return strip_think(text)


class ImportWorker(QThread):
    progress = Signal(int, str)
    file_done = Signal(str, str, str)  # (filename, status, detail)
    import_finished = Signal(int, int, int, list)  # success, skipped, failed, errors

    def __init__(self, file_paths: list[str], tags: list[str]):
        super().__init__()
        self.file_paths = file_paths
        self.tags = tags

    def run(self):
        import hashlib
        import threading
        from concurrent.futures import ThreadPoolExecutor, as_completed

        total = len(self.file_paths)
        lock = threading.Lock()
        counters = {"success": 0, "skipped": 0, "failed": 0, "done": 0}
        errors = []
        tags = self.tags
        inserted_ids = []

        def process(path):
            basename = os.path.basename(path)
            counted = False
            try:
                parsed_list = parse_file(path)

                # 读取文件创建时间戳
                file_created_at = ""
                try:
                    file_created_at = datetime.fromtimestamp(
                        os.path.getctime(path)
                    ).isoformat()
                except OSError:
                    pass

                # 读取文件修改时间戳
                file_modified_at = ""
                try:
                    file_modified_at = datetime.fromtimestamp(
                        os.path.getmtime(path)
                    ).isoformat()
                except OSError:
                    pass

                for idx, parsed in enumerate(parsed_list):
                    content_hash = hashlib.sha256(parsed.content.encode("utf-8")).hexdigest()

                    # 快速去重检查（减少无谓的 LLM 调用）
                    with lock:
                        existing = Database.get_knowledge_by_hash(content_hash)
                        if existing:
                            counters["skipped"] += 1
                            counters["done"] += 1
                            counted = True
                            pct = int(counters["done"] / (total * max(len(parsed_list), 1)) * 100)
                            sheet_info = f" ({parsed.metadata.get('sheet_name', '')})" if len(parsed_list) > 1 else ""
                            self.file_done.emit(f"{basename}{sheet_info}", "skipped", f"内容已存在：《{existing.get('title', '')}》")
                            self.progress.emit(min(pct, 99), f"已处理 {counters['done']}")
                            continue

                    # LLM 标题生成 — 使用文件名优先逻辑
                    filename_stem = os.path.splitext(basename)[0]
                    title = generate_title(parsed.content, filename=filename_stem)

                    file_size = 0
                    try:
                        file_size = os.path.getsize(path)
                    except OSError:
                        pass

                    # 写入本地 Markdown graph + 重建 DB/index cache（二次去重：防止并发导入同内容）
                    with lock:
                        existing2 = Database.get_knowledge_by_hash(content_hash)
                        if existing2:
                            counters["skipped"] += 1
                            counters["done"] += 1
                            counted = True
                            sheet_info = f" ({parsed.metadata.get('sheet_name', '')})" if len(parsed_list) > 1 else ""
                            self.file_done.emit(f"{basename}{sheet_info}", "skipped", f"内容已存在：《{existing2.get('title', '')}》")
                        else:
                            blocks = parsed.structured if parsed.structured else parsed.content
                            item_id = _file_graph_service().create_page(
                                title,
                                blocks,
                                tags=tags,
                                metadata={
                                    "source_type": "file",
                                    "source_path": parsed.source_path,
                                    "file_type": parsed.file_type,
                                    "file_created_at": file_created_at,
                                    "file_modified_at": file_modified_at,
                                },
                            )
                            inserted_ids.append(item_id)
                            counters["success"] += 1
                            sheet_info = f" ({parsed.metadata.get('sheet_name', '')})" if len(parsed_list) > 1 else ""
                            self.file_done.emit(f"{basename}{sheet_info}", "success", "导入成功")

                return basename, "success", None
            except Exception as e:
                return basename, "failed", str(e)
            finally:
                if not counted:
                    with lock:
                        counters["done"] += 1
                        pct = int(counters["done"] / total * 100)
                        self.progress.emit(min(pct, 99), f"已处理 {counters['done']}/{total}")

        try:
            with ThreadPoolExecutor(max_workers=5) as pool:
                futures = {pool.submit(process, p): p for p in self.file_paths}
                for future in as_completed(futures):
                    basename, status, err = future.result()
                    if status == "failed":
                        errors.append(f"  {basename}: {err}")
                        self.file_done.emit(basename, "failed", err or "导入失败")

            # Wiki 编译（导入完成后统一触发）
            if inserted_ids:
                from src.utils.config import Config
                if Config.get("wiki.enabled", False) and Config.get("wiki.auto_compile", True):
                    try:
                        from src.services.wiki_compiler import WikiCompiler
                        compiler = WikiCompiler()
                        for kid in inserted_ids:
                            compiler.ingest(kid)
                    except Exception as e:
                        from src.services.llm import _notify_status
                        _notify_status("error", f"Wiki 编译失败: {str(e)[:80]}")

            self.progress.emit(100, "导入完成")
        except Exception as e:
            errors.append(f"导入过程发生异常: {e}")
        finally:
            # 确保信号始终触发，避免 UI 卡死
            self.import_finished.emit(counters["success"], counters["skipped"], counters["failed"], errors)


class UrlImportWorker(QThread):
    progress = Signal(int, str)
    file_done = Signal(str, str, str)  # (url, status, detail)
    import_finished = Signal(int, int, int, list)

    def __init__(self, urls: list[str], tags: list[str]):
        super().__init__()
        self.urls = urls
        self.tags = tags

    def run(self):
        import hashlib
        from src.services.file_parser import parse_url
        from urllib.parse import urlparse

        total = len(self.urls)
        counters = {"success": 0, "skipped": 0, "failed": 0}
        errors = []
        inserted_ids = []

        try:
            for i, url in enumerate(self.urls):
                url = url.strip()
                try:
                    parsed = parse_url(url)
                    content_hash = hashlib.sha256(parsed.content.encode("utf-8")).hexdigest()

                    existing = Database.get_knowledge_by_hash(content_hash)
                    if existing:
                        counters["skipped"] += 1
                        self.file_done.emit(url, "skipped", f"内容已存在：《{existing.get('title', '')}》")
                    else:
                        url_title = parsed.title or urlparse(url).netloc
                        title = generate_title(parsed.content, filename=url_title)

                        blocks = parsed.structured if parsed.structured else parsed.content
                        item_id = _file_graph_service().create_page(
                            title,
                            blocks,
                            tags=self.tags,
                            metadata={"source_type": "web", "source_path": parsed.source_path, "file_type": parsed.file_type},
                        )
                        inserted_ids.append(item_id)
                        counters["success"] += 1
                        self.file_done.emit(url, "success", "导入成功")

                except Exception as e:
                    counters["failed"] += 1
                    errors.append(f"  {url}: {e}")
                    self.file_done.emit(url, "failed", str(e)[:100])

                pct = int((i + 1) / total * 100)
                self.progress.emit(pct, f"已处理 {i + 1}/{total}")

            if inserted_ids:
                from src.utils.config import Config
                if Config.get("wiki.enabled", False) and Config.get("wiki.auto_compile", True):
                    try:
                        from src.services.wiki_compiler import WikiCompiler
                        compiler = WikiCompiler()
                        for kid in inserted_ids:
                            compiler.ingest(kid)
                    except Exception:
                        pass

            self.progress.emit(100, "导入完成")
        except Exception as e:
            errors.append(f"URL 导入过程发生异常: {e}")
        finally:
            # 确保信号始终触发，避免 UI 卡死
            self.import_finished.emit(counters["success"], counters["skipped"], counters["failed"], errors)


SUPPORTED_EXT = {".pdf", ".pptx", ".ppt", ".docx", ".txt", ".md", ".html", ".py", ".js", ".ts", ".java", ".c", ".cpp", ".go", ".rs", ".json", ".yaml", ".yml", ".xlsx", ".xls", ".csv"}


class DropArea(QLabel):
    """可拖放文件/文件夹的区域（QSS 属性驱动）"""
    files_dropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("dropArea")
        self.setAcceptDrops(True)
        self.setMinimumHeight(90)
        self.setAlignment(Qt.AlignCenter)
        self.setProperty("hover", "false")
        self.setText("拖放文件或文件夹到此处")

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.setProperty("hover", "true")
            self.style().unpolish(self)
            self.style().polish(self)

    def dragLeaveEvent(self, event):
        self.setProperty("hover", "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def dropEvent(self, event: QDropEvent):
        self.setProperty("hover", "false")
        self.style().unpolish(self)
        self.style().polish(self)
        paths = []
        for url in event.mimeData().urls():
            local = url.toLocalFile()
            if not local:
                continue
            if os.path.isfile(local):
                _, ext = os.path.splitext(local)
                if ext.lower() in SUPPORTED_EXT:
                    paths.append(local)
            elif os.path.isdir(local):
                for root, dirs, files in os.walk(local):
                    for fname in files:
                        _, ext = os.path.splitext(fname)
                        if ext.lower() in SUPPORTED_EXT:
                            paths.append(os.path.join(root, fname))
        if paths:
            self.files_dropped.emit(paths)


class ImportDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("导入文件")
        self.setMinimumSize(680, 520)
        self._selected_files = []
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        # ---- 文件导入选项卡 ----
        file_tab = QWidget()
        file_layout = QVBoxLayout(file_tab)
        file_layout.setSpacing(10)

        file_layout.addWidget(QLabel("选择要导入的文件："))

        self.drop_area = DropArea()
        self.drop_area.files_dropped.connect(self._add_files)
        file_layout.addWidget(self.drop_area)

        self.file_list = QListWidget()
        file_layout.addWidget(self.file_list, 1)

        btn_row = QHBoxLayout()
        btn_select = QPushButton("选择文件")
        set_named_icon(btn_select, "import", "text_dim", 15)
        btn_select.clicked.connect(self._select_files)
        btn_folder = QPushButton("选择文件夹")
        set_named_icon(btn_folder, "folder", "text_dim", 15)
        btn_folder.clicked.connect(self._select_folder)
        btn_remove = QPushButton("移除选中")
        set_named_icon(btn_remove, "remove", "text_dim", 15)
        btn_remove.clicked.connect(self._remove_selected)
        btn_row.addWidget(btn_select)
        btn_row.addWidget(btn_folder)
        btn_row.addWidget(btn_remove)
        file_layout.addLayout(btn_row)

        tag_row = QHBoxLayout()
        tag_row.addWidget(QLabel("标签："))
        self.tag_input = QLineEdit()
        self.tag_input.setPlaceholderText("输入标签（逗号分隔）")
        tag_row.addWidget(self.tag_input, 1)
        file_layout.addLayout(tag_row)

        action_row = QHBoxLayout()
        action_row.addStretch()
        btn_import = QPushButton("开始导入")
        btn_import.setObjectName("primaryBtn")
        set_named_icon(btn_import, "upload", "on_accent", 15)
        btn_import.clicked.connect(self._start_import)
        btn_cancel = QPushButton("取消")
        set_named_icon(btn_cancel, "close", "text_dim", 13)
        btn_cancel.clicked.connect(self.reject)
        action_row.addWidget(btn_import)
        action_row.addWidget(btn_cancel)
        file_layout.addLayout(action_row)

        self.tabs.addTab(file_tab, "文件导入")

        # ---- URL 导入选项卡 ----
        url_tab = QWidget()
        url_layout = QVBoxLayout(url_tab)
        url_layout.setSpacing(10)

        url_layout.addWidget(QLabel("输入网页 URL（每行一个）："))

        self.url_input = QTextEdit()
        self.url_input.setPlaceholderText("https://example.com/article\nhttps://example.com/another-page")
        self.url_input.setMaximumHeight(150)
        url_layout.addWidget(self.url_input)

        url_tag_row = QHBoxLayout()
        url_tag_row.addWidget(QLabel("标签："))
        self.url_tag_input = QLineEdit()
        self.url_tag_input.setPlaceholderText("输入标签（逗号分隔）")
        url_tag_row.addWidget(self.url_tag_input, 1)
        url_layout.addLayout(url_tag_row)

        url_layout.addStretch()

        url_action_row = QHBoxLayout()
        url_action_row.addStretch()
        btn_url_import = QPushButton("开始导入")
        btn_url_import.setObjectName("primaryBtn")
        set_named_icon(btn_url_import, "link", "on_accent", 15)
        btn_url_import.clicked.connect(self._start_url_import)
        btn_url_cancel = QPushButton("取消")
        set_named_icon(btn_url_cancel, "close", "text_dim", 13)
        btn_url_cancel.clicked.connect(self.reject)
        url_action_row.addWidget(btn_url_import)
        url_action_row.addWidget(btn_url_cancel)
        url_layout.addLayout(url_action_row)

        self.tabs.addTab(url_tab, "URL 导入")

        # ---- 粘贴导入选项卡 ----
        paste_tab = QWidget()
        paste_layout = QVBoxLayout(paste_tab)
        paste_layout.setSpacing(10)

        paste_layout.addWidget(QLabel("粘贴网页或文档内容（在浏览器中 Ctrl+A 全选 → Ctrl+C 复制）："))

        paste_title_row = QHBoxLayout()
        paste_title_row.addWidget(QLabel("标题："))
        self.paste_title_input = QLineEdit()
        self.paste_title_input.setPlaceholderText("输入知识标题")
        paste_title_row.addWidget(self.paste_title_input, 1)
        paste_layout.addLayout(paste_title_row)

        self.paste_content_input = QTextEdit()
        self.paste_content_input.setPlaceholderText("在此粘贴内容...\n\n提示：在浏览器中打开目标页面，按 Ctrl+A 全选，再按 Ctrl+C 复制，然后在此处 Ctrl+V 粘贴。")
        paste_layout.addWidget(self.paste_content_input, 1)

        paste_source_row = QHBoxLayout()
        paste_source_row.addWidget(QLabel("来源 URL："))
        self.paste_source_input = QLineEdit()
        self.paste_source_input.setPlaceholderText("可选，填写原始网页地址")
        paste_source_row.addWidget(self.paste_source_input, 1)
        paste_layout.addLayout(paste_source_row)

        paste_tag_row = QHBoxLayout()
        paste_tag_row.addWidget(QLabel("标签："))
        self.paste_tag_input = QLineEdit()
        self.paste_tag_input.setPlaceholderText("输入标签（逗号分隔）")
        paste_tag_row.addWidget(self.paste_tag_input, 1)
        paste_layout.addLayout(paste_tag_row)

        paste_action_row = QHBoxLayout()
        paste_action_row.addStretch()
        btn_paste_import = QPushButton("开始导入")
        btn_paste_import.setObjectName("primaryBtn")
        set_named_icon(btn_paste_import, "paste", "on_accent", 15)
        btn_paste_import.clicked.connect(self._start_paste_import)
        btn_paste_cancel = QPushButton("取消")
        set_named_icon(btn_paste_cancel, "close", "text_dim", 13)
        btn_paste_cancel.clicked.connect(self.reject)
        paste_action_row.addWidget(btn_paste_import)
        paste_action_row.addWidget(btn_paste_cancel)
        paste_layout.addLayout(paste_action_row)

        self.tabs.addTab(paste_tab, "粘贴导入")

    def _add_files(self, paths: list[str]):
        """将文件路径列表添加到待导入列表（去重）"""
        for f in paths:
            if f not in self._selected_files:
                self._selected_files.append(f)
                self.file_list.addItem(f)

    def _select_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择文件", "",
            "支持的文件 (*.pdf *.pptx *.ppt *.docx *.txt *.md *.html *.py *.js *.ts *.java *.c *.cpp *.go *.rs *.json *.yaml *.yml *.xlsx *.xls *.csv);;所有文件 (*)",
        )
        self._add_files(files)

    def _select_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "选择文件夹")
        if not folder:
            return
        paths = []
        for root, dirs, files in os.walk(folder):
            for fname in files:
                _, ext = os.path.splitext(fname)
                if ext.lower() in SUPPORTED_EXT:
                    paths.append(os.path.join(root, fname))
        self._add_files(paths)

    def _remove_selected(self):
        rows = sorted([self.file_list.row(item) for item in self.file_list.selectedItems()], reverse=True)
        for row in rows:
            self.file_list.takeItem(row)
            if row < len(self._selected_files):
                self._selected_files.pop(row)

    def _start_url_import(self):
        raw_text = self.url_input.toPlainText().strip()
        if not raw_text:
            QMessageBox.warning(self, "提示", "请输入至少一个网页 URL")
            return
        urls = [line.strip() for line in raw_text.splitlines() if line.strip()]
        tags = [t.strip() for t in self.url_tag_input.text().split(",") if t.strip()]

        self._result_dlg = QDialog(self)
        self._result_dlg.setWindowTitle("URL 导入进度")
        self._result_dlg.setMinimumSize(600, 420)
        self._result_dlg.setWindowModality(Qt.WindowModal)
        rlayout = QVBoxLayout(self._result_dlg)

        self._result_header = QLabel("正在导入网页...")
        self._result_header.setObjectName("sectionLabel")
        rlayout.addWidget(self._result_header)

        from PySide6.QtWidgets import QProgressBar
        self._result_progress = QProgressBar()
        self._result_progress.setRange(0, len(urls))
        self._result_progress.setValue(0)
        self._result_progress.setTextVisible(True)
        self._result_progress.setFormat("%v/%m URL")
        self._result_progress.setMinimumHeight(24)
        rlayout.addWidget(self._result_progress)

        self._result_table = QTableWidget(0, 3)
        self._result_table.setHorizontalHeaderLabels(["URL", "状态", "说明"])
        self._result_table.horizontalHeader().setStretchLastSection(True)
        self._result_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._result_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self._result_table.setColumnWidth(1, 80)
        self._result_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._result_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        rlayout.addWidget(self._result_table, 1)

        self._result_summary = QLabel("")
        self._result_summary.setObjectName("hintLabel")
        rlayout.addWidget(self._result_summary)

        self._result_btn = QPushButton("关闭")
        set_named_icon(self._result_btn, "close", "text_dim", 13)
        self._result_btn.setEnabled(False)
        self._result_btn.clicked.connect(self._result_dlg.accept)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(self._result_btn)
        rlayout.addLayout(btn_row)

        self._result_counts = {"success": 0, "skipped": 0, "failed": 0}
        self._import_completed_successfully = False

        self._url_worker = UrlImportWorker(urls, tags)
        self._url_worker.file_done.connect(self._on_file_done)
        self._url_worker.progress.connect(self._on_import_progress)
        self._url_worker.import_finished.connect(self._on_import_finished)
        self._url_worker.start()

        self._result_dlg.exec()
        if self._import_completed_successfully:
            self.accept()

    def _start_paste_import(self):
        content = self.paste_content_input.toPlainText().strip()
        if not content:
            QMessageBox.warning(self, "提示", "请粘贴要导入的内容")
            return

        title = self.paste_title_input.text().strip()
        if not title:
            title = generate_title(content, filename="粘贴内容")
        if not title:
            title = "粘贴内容"

        source_url = self.paste_source_input.text().strip()
        tags = [t.strip() for t in self.paste_tag_input.text().split(",") if t.strip()]

        import hashlib
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        existing = Database.get_knowledge_by_hash(content_hash)
        if existing:
            QMessageBox.information(self, "提示", f"内容已存在：《{existing.get('title', '')}》")
            return

        item_id = _file_graph_service().create_page(
            title,
            content,
            tags=tags,
            metadata={"source_type": "web" if source_url else "manual", "source_path": source_url, "file_type": "txt"},
        )

        from src.utils.config import Config
        if Config.get("wiki.enabled", False) and Config.get("wiki.auto_compile", True):
            try:
                from src.services.wiki_compiler import WikiCompiler
                WikiCompiler().ingest(item_id)
            except Exception:
                pass

        QMessageBox.information(self, "成功", f"已导入：{title}")
        self.accept()

    def _start_import(self):
        if not self._selected_files:
            QMessageBox.warning(self, "提示", "请先选择要导入的文件")
            return
        tags = [t.strip() for t in self.tag_input.text().split(",") if t.strip()]

        # 自定义导入结果面板
        self._result_dlg = QDialog(self)
        self._result_dlg.setWindowTitle("导入进度")
        self._result_dlg.setMinimumSize(600, 480)
        self._result_dlg.setWindowModality(Qt.WindowModal)
        rlayout = QVBoxLayout(self._result_dlg)

        self._result_header = QLabel("正在导入...")
        self._result_header.setObjectName("sectionLabel")
        rlayout.addWidget(self._result_header)

        from PySide6.QtWidgets import QProgressBar
        self._result_progress = QProgressBar()
        self._result_progress.setRange(0, 100)
        self._result_progress.setValue(0)
        self._result_progress.setTextVisible(True)
        self._result_progress.setFormat("%v/%m 文件")
        self._result_progress.setMinimumHeight(24)
        rlayout.addWidget(self._result_progress)

        self._result_table = QTableWidget(0, 3)
        self._result_table.setHorizontalHeaderLabels(["文件名", "状态", "说明"])
        self._result_table.horizontalHeader().setStretchLastSection(True)
        self._result_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._result_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self._result_table.setColumnWidth(1, 80)
        self._result_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._result_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        rlayout.addWidget(self._result_table, 1)

        self._result_summary = QLabel("")
        self._result_summary.setObjectName("hintLabel")
        rlayout.addWidget(self._result_summary)

        self._result_btn = QPushButton("关闭")
        set_named_icon(self._result_btn, "close", "text_dim", 13)
        self._result_btn.setEnabled(False)
        self._result_btn.clicked.connect(self._result_dlg.accept)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(self._result_btn)
        rlayout.addLayout(btn_row)

        # 计数器
        total_files = len(self._selected_files)
        self._result_counts = {"success": 0, "skipped": 0, "failed": 0}
        self._import_completed_successfully = False
        self._result_progress.setRange(0, total_files)
        self._result_progress.setValue(0)

        self._worker = ImportWorker(self._selected_files, tags)
        self._worker.file_done.connect(self._on_file_done)
        self._worker.progress.connect(self._on_import_progress)
        self._worker.import_finished.connect(self._on_import_finished)
        self._worker.start()

        self._result_dlg.exec()
        if self._import_completed_successfully:
            self.accept()

    def _on_file_done(self, filename: str, status: str, detail: str):
        self._result_counts[status] = self._result_counts.get(status, 0) + 1
        row = self._result_table.rowCount()
        self._result_table.insertRow(row)

        item_name = QTableWidgetItem(filename)

        status_icons = {"success": "成功", "skipped": "跳过", "failed": "失败"}
        status_colors = {"success": QColor(get_color("indicator_idle")), "skipped": QColor(get_color("indicator_running")), "failed": QColor(get_color("danger"))}
        item_status = QTableWidgetItem(status_icons.get(status, status))
        item_status.setForeground(status_colors.get(status, QColor()))
        item_status.setTextAlignment(Qt.AlignCenter)

        item_detail = QTableWidgetItem(detail)

        self._result_table.setItem(row, 0, item_name)
        self._result_table.setItem(row, 1, item_status)
        self._result_table.setItem(row, 2, item_detail)
        self._result_table.scrollToBottom()

        # 更新进度条
        done = sum(self._result_counts.values())
        self._result_progress.setValue(done)

    def _on_import_progress(self, value: int, msg: str):
        self._result_header.setText(msg)

    def _on_import_finished(self, success: int, skipped: int, failed: int, errors: list):
        parts = []
        if success > 0:
            parts.append(f"成功 {success}")
        if skipped > 0:
            parts.append(f"跳过 {skipped}")
        if failed > 0:
            parts.append(f"失败 {failed}")
        total = success + skipped + failed
        self._result_progress.setMaximum(max(total, 1))
        self._result_progress.setValue(total)
        self._result_header.setText(f"导入完成（共 {total} 个文件）")
        self._result_summary.setText("  |  ".join(parts))
        self._result_btn.setEnabled(True)
        self._import_completed_successfully = success > 0
