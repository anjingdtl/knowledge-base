"""维护中心 — 版本冲突检测与清理(桌面 GUI)。

迁移自原 Web 客户端 client/src/views/MaintenanceView.tsx。GUI 直接调用
VersionConflictService,扫描与批量判断强制 run_synchronously=True(GUI 进程
不消费 async job,默认异步会永久卡 pending),并用 QThread 包装避免阻塞 UI。
"""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QSize, Qt, QThread, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from src.gui.empty_state import EmptyState
from src.gui.icons import set_named_icon
from src.services.version_conflict import VersionConflictService

RELATION_LABELS = {
    "supersedes": "A 替代 B",
    "superseded_by": "B 替代 A",
    "partial_overlap": "部分重叠",
    "unrelated": "无关",
}

# (显示文本, 传给 list_pairs 的 status 值;空串表示全部)
STATUS_FILTERS: list[tuple[str, str]] = [
    ("待处理", "pending"),
    ("已忽略", "ignored"),
    ("已删除", "deleted"),
    ("全部", ""),
]

# 关系筛选(None=全部, "deletable"=可删旧版, "unjudged"=未判断)
RELATION_FILTERS: list[tuple[str, str | None]] = [
    ("全部关系", None),
    ("可删旧版", "deletable"),
    ("部分重叠", "partial_overlap"),
    ("未判断", "unjudged"),
]

# LLM 判断结果标色:绿=可删旧版,橙=部分重叠,灰=无关
RELATION_COLORS = {
    "supersedes": "#5CB85C",
    "superseded_by": "#5CB85C",
    "partial_overlap": "#F0AD4E",
    "unrelated": "#888888",
}

_STATUS_TEXT = {
    "scanning": "扫描中...",
    "judging": "LLM 判断中...",
    "ready": "扫描完成",
    "error": "出错",
    "completed": "已完成",
}


def _service() -> VersionConflictService:
    return VersionConflictService()


def _can_delete(pair: dict) -> bool:
    return pair.get("relation_type") in ("supersedes", "superseded_by")


def _relation_label(pair: dict) -> str:
    rt = pair.get("relation_type")
    return RELATION_LABELS.get(rt, "未判断") if rt else "未判断"


def _relation_color(pair: dict) -> str | None:
    """LLM 判断结果对应的标色;未判断返回 None。"""
    return RELATION_COLORS.get(pair.get("relation_type"))


def _older_newer(pair: dict) -> tuple[str | None, str | None]:
    """返回 (旧版标题, 新版标题)。依据 newer_item_id 判断。"""
    if pair.get("newer_item_id") == pair["item_a_id"]:
        return pair.get("item_b_title"), pair.get("item_a_title")
    return pair.get("item_a_title"), pair.get("item_b_title")


# ── 后台 Worker ──


class ScanWorker(QThread):
    """同步执行完整扫描。"""

    finished_ok = Signal(str)  # session_id
    error = Signal(str)

    def __init__(self, rescan_ignored: bool = False):
        super().__init__()
        self._rescan = rescan_ignored

    def run(self) -> None:
        try:
            session_id = _service().start_scan_session(
                rescan_ignored=self._rescan, run_synchronously=True
            )
            self.finished_ok.emit(session_id)
        except Exception as exc:  # noqa: BLE001 — 后台线程兜底,经 error 信号上报
            self.error.emit(str(exc))


class JudgeWorker(QThread):
    """同步批量 LLM 判断。"""

    finished_ok = Signal(int, list)  # judged_count, errors
    error = Signal(str)

    def __init__(self, session_id: str, limit: int = 20):
        super().__init__()
        self._session_id = session_id
        self._limit = limit

    def run(self) -> None:
        try:
            result = _service().judge_pending_pairs(
                self._session_id, limit=self._limit, run_synchronously=True
            )
            self.finished_ok.emit(result.get("judged", 0), result.get("errors", []))
        except Exception as exc:  # noqa: BLE001
            self.error.emit(str(exc))


class RejudgeWorker(QThread):
    """单 pair 重新判断(judge_pair 默认同步)。"""

    finished_ok = Signal(str)  # pair_id
    error = Signal(str)

    def __init__(self, pair_id: str):
        super().__init__()
        self._pair_id = pair_id

    def run(self) -> None:
        try:
            result = _service().judge_pair(self._pair_id)
            if result.get("ok"):
                self.finished_ok.emit(self._pair_id)
            else:
                msg = result.get("error", {}).get("message", "判断失败")
                self.error.emit(msg)
        except Exception as exc:  # noqa: BLE001
            self.error.emit(str(exc))


# ── 候选对卡片 ──


class PairItemWidget(QFrame):
    """单个候选对的卡片行:A/B 标题 + 关系 + 操作按钮。"""

    def __init__(
        self,
        pair: dict,
        on_delete: Callable[[dict], None],
        on_ignore: Callable[[str], None],
        on_rejudge: Callable[[str], None],
    ) -> None:
        super().__init__()
        self._pair = pair
        self._on_delete = on_delete
        self._on_ignore = on_ignore
        self._on_rejudge = on_rejudge
        self._reason_label: QLabel | None = None
        self._build()
        if pair.get("status") == "deleted":
            self.setProperty("deleted", True)
            self.setStyleSheet("opacity: 0.5;")

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)
        self.setMinimumHeight(76)

        top = QHBoxLayout()
        top.setSpacing(12)

        # A ↔ B 两列
        ab = QHBoxLayout()
        ab.setSpacing(6)
        ab.addWidget(self._item_col(self._pair.get("item_a_title"), self._pair.get("item_a_created")), 1)
        arrow = QLabel("↔")
        arrow.setObjectName("hintLabel")
        arrow.setFixedWidth(14)
        arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ab.addWidget(arrow)
        ab.addWidget(self._item_col(self._pair.get("item_b_title"), self._pair.get("item_b_created")), 1)
        top.addLayout(ab, 3)

        # 关系 + 置信度
        rel_col = QVBoxLayout()
        rel_col.setSpacing(0)
        rel = QLabel(_relation_label(self._pair))
        rel.setObjectName("sectionLabel")
        rel.setAlignment(Qt.AlignmentFlag.AlignRight)
        color = _relation_color(self._pair)
        if color:
            rel.setStyleSheet(f"color: {color}; font-weight: 600;")
        rel_col.addWidget(rel)
        conf = self._pair.get("confidence")
        sim = self._pair.get("similarity_score")
        if conf is not None:
            cl = QLabel(f"置信度 {int(conf * 100)}%")
        elif sim is not None:
            cl = QLabel(f"相似度 {int(sim * 100)}%")
        else:
            cl = None
        if cl is not None:
            cl.setObjectName("hintLabel")
            cl.setAlignment(Qt.AlignmentFlag.AlignRight)
            rel_col.addWidget(cl)
        top.addLayout(rel_col)

        # 操作按钮组
        btns = QHBoxLayout()
        btns.setSpacing(6)
        status = self._pair.get("status")
        rt = self._pair.get("relation_type")

        reason = self._pair.get("reason")
        btn_detail = QPushButton("详情")
        btn_detail.setProperty("compact", True)
        btn_detail.setCursor(Qt.CursorShape.PointingHandCursor)
        btns.addWidget(btn_detail)
        if not reason:
            btn_detail.setVisible(False)

        if status == "pending":
            if _can_delete(self._pair):
                btn_del = QPushButton("确认删除旧版")
                btn_del.setObjectName("dangerBtn")
                btn_del.setProperty("compact", True)
                btn_del.setCursor(Qt.CursorShape.PointingHandCursor)
                btn_del.clicked.connect(lambda: self._on_delete(self._pair))
                btns.addWidget(btn_del)
            elif rt == "partial_overlap":
                hint = QLabel("部分重叠,需手动处理")
                hint.setObjectName("hintLabel")
                btns.addWidget(hint)
            btn_ignore = QPushButton("忽略")
            btn_ignore.setProperty("compact", True)
            btn_ignore.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_ignore.clicked.connect(lambda: self._on_ignore(self._pair["id"]))
            btns.addWidget(btn_ignore)
            btn_re = QPushButton("重新判断")
            btn_re.setProperty("compact", True)
            btn_re.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_re.clicked.connect(lambda: self._on_rejudge(self._pair["id"]))
            btns.addWidget(btn_re)
        top.addLayout(btns)
        layout.addLayout(top)

        if reason:
            self._reason_label = QLabel(reason)
            self._reason_label.setObjectName("hintLabel")
            self._reason_label.setWordWrap(True)
            self._reason_label.setVisible(False)
            layout.addWidget(self._reason_label)
            btn_detail.clicked.connect(self._toggle_reason)

    def _toggle_reason(self) -> None:
        if self._reason_label is not None:
            self._reason_label.setVisible(not self._reason_label.isVisible())

    @staticmethod
    def _item_col(title: str | None, created: str | None) -> QWidget:
        col = QVBoxLayout()
        col.setSpacing(0)
        t = QLabel(title or "(已删除)")
        t.setObjectName("sectionLabel")
        col.addWidget(t)
        c = QLabel((created or "")[:10])
        c.setObjectName("hintLabel")
        col.addWidget(c)
        w = QWidget()
        w.setLayout(col)
        return w


class IgnoreItemWidget(QFrame):
    """忽略列表的单行:titleA ↔ titleB + 撤销按钮。"""

    def __init__(self, ignore: dict, on_undo: Callable[[str], None]) -> None:
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 4, 8, 4)
        row.setSpacing(8)
        a = ignore.get("item_a_title") or "(已删除)"
        b = ignore.get("item_b_title") or "(已删除)"
        title = QLabel(f"{a}  ↔  {b}")
        title.setObjectName("hintLabel")
        row.addWidget(title, 1)
        btn = QPushButton("撤销忽略")
        btn.setProperty("compact", True)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(lambda: on_undo(ignore["id"]))
        row.addWidget(btn)


# ── 主视图 ──


class MaintenanceView(QWidget):
    """维护中心:版本冲突扫描 → LLM 判断 → 删除旧版/忽略。"""

    def __init__(self) -> None:
        super().__init__()
        self._current_session_id: str | None = None
        self._status_filter = "pending"
        self._scan_worker: ScanWorker | None = None
        self._judge_worker: JudgeWorker | None = None
        self._rejudge_worker: RejudgeWorker | None = None
        self._setup_ui()

    # ── UI 构建 ──

    def _setup_ui(self) -> None:
        self.setObjectName("pageSurface")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        # 顶部工具栏
        header = QFrame()
        header.setObjectName("toolbarCard")
        h = QVBoxLayout(header)
        h.setContentsMargins(12, 8, 12, 8)
        h.setSpacing(6)
        top = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(0)
        title = QLabel("维护中心")
        title.setObjectName("pageTitle")
        subtitle = QLabel("版本冲突扫描与清理 — 找出重复/旧版知识条目并合并")
        subtitle.setObjectName("pageSubtitle")
        title_col.addWidget(title)
        title_col.addWidget(subtitle)
        top.addLayout(title_col)
        top.addStretch()
        self.btn_scan = QPushButton("开始新扫描")
        self.btn_scan.setObjectName("accentBtn")
        set_named_icon(self.btn_scan, "refresh", "on_accent", 16)
        self.btn_scan.clicked.connect(self._on_scan)
        top.addWidget(self.btn_scan)
        h.addLayout(top)
        layout.addWidget(header)

        # 当前会话进度卡片
        self.progress_card = QFrame()
        self.progress_card.setObjectName("toolbarCard")
        pc = QVBoxLayout(self.progress_card)
        pc.setContentsMargins(12, 8, 12, 8)
        pc.setSpacing(6)
        prow = QHBoxLayout()
        self.session_status_label = QLabel("")
        self.session_status_label.setObjectName("sectionLabel")
        prow.addWidget(self.session_status_label)
        prow.addStretch()
        self.btn_judge = QPushButton("触发 LLM 判断")
        self.btn_judge.setObjectName("accentBtn")
        set_named_icon(self.btn_judge, "quality", "on_accent", 14)
        self.btn_judge.clicked.connect(self._on_judge)
        self.btn_judge.setVisible(False)
        prow.addWidget(self.btn_judge)
        pc.addLayout(prow)

        stats = QHBoxLayout()
        stats.setSpacing(20)
        self.lbl_scanned = QLabel("扫描条目: 0")
        self.lbl_candidates = QLabel("候选对: 0")
        self.lbl_judged = QLabel("已判断: 0")
        self.lbl_deleted = QLabel("已删除: 0")
        for w in (self.lbl_scanned, self.lbl_candidates, self.lbl_judged, self.lbl_deleted):
            w.setObjectName("hintLabel")
            stats.addWidget(w)
        stats.addStretch()
        pc.addLayout(stats)
        self.progress_card.setVisible(False)
        layout.addWidget(self.progress_card)

        # 候选对区
        pairs_header = QHBoxLayout()
        lbl = QLabel("候选对")
        lbl.setObjectName("sectionLabel")
        pairs_header.addWidget(lbl)
        self.combo_filter = QComboBox()
        for text, _val in STATUS_FILTERS:
            self.combo_filter.addItem(text)
        self.combo_filter.currentIndexChanged.connect(self._on_filter_changed)
        pairs_header.addWidget(self.combo_filter)

        rel_lbl = QLabel("关系:")
        rel_lbl.setObjectName("hintLabel")
        pairs_header.addWidget(rel_lbl)
        self.combo_relation = QComboBox()
        for text, _val in RELATION_FILTERS:
            self.combo_relation.addItem(text)
        self.combo_relation.currentIndexChanged.connect(lambda _i: self._load_pairs())
        pairs_header.addWidget(self.combo_relation)
        pairs_header.addStretch()
        layout.addLayout(pairs_header)

        self.pairs_stack = QStackedWidget()
        self.pairs_list = QListWidget()
        self.pairs_list.setSpacing(2)
        self.pairs_stack.addWidget(self.pairs_list)
        self.empty_state = EmptyState(
            icon_key="maintenance",
            title="暂无候选对",
            description="点击右上角“开始新扫描”,检测知识库中的版本冲突。",
        )
        self.pairs_stack.addWidget(self.empty_state)
        layout.addWidget(self.pairs_stack, 1)

        # 历史会话(折叠)
        self.history_group = QGroupBox("历史会话 (0)")
        self.history_group.setCheckable(True)
        self.history_group.setChecked(False)
        hg = QVBoxLayout(self.history_group)
        self.history_list = QListWidget()
        self.history_list.itemClicked.connect(self._on_history_clicked)
        hg.addWidget(self.history_list)
        self.history_list.setVisible(False)
        self.history_group.toggled.connect(self.history_list.setVisible)
        layout.addWidget(self.history_group)

        # 忽略列表(折叠)
        self.ignores_group = QGroupBox("忽略列表 (0)")
        self.ignores_group.setCheckable(True)
        self.ignores_group.setChecked(False)
        ig = QVBoxLayout(self.ignores_group)
        self.ignores_list = QListWidget()
        ig.addWidget(self.ignores_list)
        self.ignores_list.setVisible(False)
        self.ignores_group.toggled.connect(self.ignores_list.setVisible)
        layout.addWidget(self.ignores_group)

    # ── 数据加载 ──

    def refresh_on_show(self) -> None:
        """切换进入本页时刷新历史会话与忽略列表(不触发扫描)。"""
        try:
            self._load_sessions()
            self._load_ignores()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "加载失败", str(exc))

    def _load_sessions(self) -> None:
        sessions = _service().list_sessions(limit=50)
        self.history_list.clear()
        for s in sessions:
            text = (
                f"{s['id'][:8]}  ·  {_STATUS_TEXT.get(s.get('status'), s.get('status'))}"
                f"  ·  候选 {s.get('candidates_found', 0)} / 删除 {s.get('pairs_deleted', 0)}"
            )
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, s["id"])
            self.history_list.addItem(item)
        self.history_group.setTitle(f"历史会话 ({len(sessions)})")

    def _load_ignores(self) -> None:
        ignores = _service().list_ignores(limit=100)
        self.ignores_list.clear()
        for ig in ignores:
            widget = IgnoreItemWidget(ig, on_undo=self._on_undo_ignore)
            item = QListWidgetItem()
            item.setSizeHint(widget.sizeHint())
            self.ignores_list.addItem(item)
            self.ignores_list.setItemWidget(item, widget)
        self.ignores_group.setTitle(f"忽略列表 ({len(ignores)})")

    def _load_pairs(self) -> None:
        self.pairs_list.clear()
        if not self._current_session_id:
            self.pairs_stack.setCurrentIndex(1)
            return
        status = self._status_filter or None
        pairs = _service().list_pairs(self._current_session_id, status=status, limit=200)
        pairs = self._apply_relation_filter(pairs)
        pairs = self._sort_pairs(pairs)
        if not pairs:
            self.pairs_stack.setCurrentIndex(1)
            return
        self.pairs_stack.setCurrentIndex(0)
        for pair in pairs:
            widget = PairItemWidget(
                pair,
                on_delete=self._on_delete,
                on_ignore=self._on_ignore,
                on_rejudge=self._on_rejudge,
            )
            item = QListWidgetItem()
            item.setSizeHint(QSize(0, max(widget.sizeHint().height(), 76)))
            self.pairs_list.addItem(item)
            self.pairs_list.setItemWidget(item, widget)

    def _relation_filter_key(self) -> str | None:
        idx = self.combo_relation.currentIndex()
        if 0 <= idx < len(RELATION_FILTERS):
            return RELATION_FILTERS[idx][1]
        return None

    def _apply_relation_filter(self, pairs: list[dict]) -> list[dict]:
        key = self._relation_filter_key()
        if key is None:
            return pairs
        if key == "deletable":
            return [p for p in pairs if _can_delete(p)]
        if key == "unjudged":
            return [p for p in pairs if not p.get("relation_type")]
        return [p for p in pairs if p.get("relation_type") == key]

    def _sort_pairs(self, pairs: list[dict]) -> list[dict]:
        """可删旧版优先,其次按 confidence/similarity_score 降序,未判断排后。"""
        def rank(p: dict) -> tuple[int, float]:
            sim = p.get("similarity_score") or p.get("similarity") or 0.0
            if _can_delete(p):
                return (2, p.get("confidence") or sim)
            if p.get("relation_type"):
                return (1, p.get("confidence") or sim)
            return (0, sim)
        return sorted(pairs, key=rank, reverse=True)

    def _update_session_status(self, status: dict) -> None:
        if status.get("error") == "session not found":
            self.session_status_label.setText("会话不存在")
            self.progress_card.setVisible(True)
            self.btn_judge.setVisible(False)
            return
        if status.get("error"):
            self.session_status_label.setText(f"会话异常: {status['error']}")
            self.progress_card.setVisible(True)
            self.btn_judge.setVisible(False)
            return
        st = status.get("status", "")
        self.session_status_label.setText(f"当前会话:{_STATUS_TEXT.get(st, st)}")
        self.lbl_scanned.setText(f"扫描条目: {status.get('total_items_scanned', 0)}")
        self.lbl_candidates.setText(f"候选对: {status.get('candidates_found', 0)}")
        self.lbl_judged.setText(f"已判断: {status.get('pairs_judged', 0)}")
        self.lbl_deleted.setText(f"已删除: {status.get('pairs_deleted', 0)}")
        self.progress_card.setVisible(True)
        self.btn_judge.setVisible(st == "ready")
        self.btn_scan.setEnabled(st not in ("scanning", "judging"))
        self.btn_judge.setEnabled(st != "judging")

    def _refresh_current(self) -> None:
        if not self._current_session_id:
            return
        status = _service().get_session_status(self._current_session_id)
        self._update_session_status(status)
        self._load_pairs()

    # ── 用户动作 ──

    def _on_scan(self) -> None:
        if QMessageBox.question(
            self, "开始扫描", "开始新扫描?\n已忽略的对将不会被重新扫描。"
        ) != QMessageBox.StandardButton.Yes:
            return
        self.btn_scan.setEnabled(False)
        self.progress_card.setVisible(True)
        self.session_status_label.setText("当前会话:扫描中...")
        self.btn_judge.setVisible(False)
        self._scan_worker = ScanWorker(rescan_ignored=False)
        self._scan_worker.finished_ok.connect(self._on_scan_finished)
        self._scan_worker.error.connect(self._on_worker_error)
        self._scan_worker.start()

    def _on_scan_finished(self, session_id: str) -> None:
        self._scan_worker = None
        self._current_session_id = session_id
        self._refresh_current()
        self._load_sessions()

    def _on_judge(self) -> None:
        if not self._current_session_id:
            return
        self.btn_judge.setEnabled(False)
        self.session_status_label.setText("当前会话:LLM 判断中...")
        self._judge_worker = JudgeWorker(self._current_session_id, limit=20)
        self._judge_worker.finished_ok.connect(self._on_judge_finished)
        self._judge_worker.error.connect(self._on_worker_error)
        self._judge_worker.start()

    def _on_judge_finished(self, judged: int, errors: list) -> None:
        self._judge_worker = None
        self._refresh_current()
        msg = f"已判断 {judged} 对。"
        if errors:
            msg += f"\n其中 {len(errors)} 对判断失败。"
        QMessageBox.information(self, "判断完成", msg)

    def _on_rejudge(self, pair_id: str) -> None:
        self._rejudge_worker = RejudgeWorker(pair_id)
        self._rejudge_worker.finished_ok.connect(self._on_rejudge_finished)
        self._rejudge_worker.error.connect(self._on_worker_error)
        self._rejudge_worker.start()

    def _on_rejudge_finished(self, pair_id: str) -> None:  # noqa: ARG002
        self._rejudge_worker = None
        self._refresh_current()

    def _on_delete(self, pair: dict) -> None:
        older, newer = _older_newer(pair)
        if QMessageBox.question(
            self,
            "确认删除旧版",
            f"将删除旧版 [{older or '?'}],新版 [{newer or '?'}] 保留。\n\n确认删除?",
        ) != QMessageBox.StandardButton.Yes:
            return
        result = _service().execute_delete(pair["id"], operator="user")
        if result.get("ok"):
            QMessageBox.information(self, "已删除", "旧版本已删除,可在「回收站」恢复。")
            self._refresh_current()
        else:
            err = result.get("error", {}).get("message", "删除失败")
            QMessageBox.warning(self, "删除失败", err)

    def _on_ignore(self, pair_id: str) -> None:
        result = _service().ignore_pair(pair_id)
        if result.get("ok"):
            self._refresh_current()
            self._load_ignores()
        else:
            QMessageBox.warning(self, "忽略失败", "操作失败")

    def _on_undo_ignore(self, ignore_id: str) -> None:
        if QMessageBox.question(
            self, "撤销忽略", "撤销忽略?\n下次扫描会重新判断。"
        ) != QMessageBox.StandardButton.Yes:
            return
        result = _service().delete_ignore(ignore_id)
        if result.get("ok"):
            self._load_ignores()
        else:
            QMessageBox.warning(self, "撤销失败", "操作失败")

    def _on_filter_changed(self, index: int) -> None:
        if 0 <= index < len(STATUS_FILTERS):
            self._status_filter = STATUS_FILTERS[index][1]
        self._load_pairs()

    def _on_history_clicked(self, item: QListWidgetItem) -> None:
        session_id = item.data(Qt.ItemDataRole.UserRole)
        if session_id:
            self._current_session_id = session_id
            self._refresh_current()

    def _on_worker_error(self, msg: str) -> None:
        self._scan_worker = None
        self._judge_worker = None
        self._rejudge_worker = None
        self.btn_scan.setEnabled(True)
        QMessageBox.warning(self, "操作失败", msg)
