"""LLM 状态指示器，与 MCP 状态统一风格。"""
from PySide6.QtWidgets import QLabel
from PySide6.QtCore import Qt, QTimer, QPointF
from PySide6.QtGui import QColor, QRadialGradient, QBrush, QPainter, QPolygonF

from src.gui.theme import is_dark


class LLMIndicator(QLabel):
    """侧边栏底部的 LLM 状态灯。"""
    IDLE = "idle"
    RUNNING = "running"
    ERROR = "error"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("indicatorDot")
        self.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
        self.setContentsMargins(0, 0, 0, 0)
        self.setProperty("status", "idle")
        self._status = self.IDLE
        self._detail = ""
        self._blink_on = True
        self._elapsed_ms = 0
        self._update_text()

        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._tick)

    def paintEvent(self, event):
        """在状态文字左侧添加状态光晕。"""
        status = self.property("status")
        if status in ("idle", "running"):
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)
            center_x = self.width() // 2 - self.fontMetrics().horizontalAdvance(self.text()) // 2 - 8
            center_y = self.height() // 2
            if status == "idle":
                if is_dark():
                    center_color = QColor(76, 175, 130, 70)
                else:
                    center_color = QColor(58, 138, 110, 70)
                edge_color = QColor(center_color.red(), center_color.green(), center_color.blue(), 0)
            else:
                if is_dark():
                    center_color = QColor(214, 74, 108, 75)
                else:
                    center_color = QColor(31, 74, 72, 75)
                edge_color = QColor(center_color.red(), center_color.green(), center_color.blue(), 0)
            glow = QRadialGradient(QPointF(center_x, center_y), 12)
            glow.setColorAt(0, center_color)
            glow.setColorAt(1, edge_color)
            painter.setBrush(QBrush(glow))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QPointF(center_x, center_y), 12, 12)
            painter.end()
        super().paintEvent(event)

    def set_status(self, status: str, detail: str = ""):
        prev_status = self._status
        self._status = status
        self._detail = detail
        if status == self.RUNNING:
            self._blink_on = True
            self._elapsed_ms = 0
            self._timer.start()
        else:
            self._timer.stop()
        # 仅在状态真变化时切 QSS 状态（避免无谓的 QSS 重算）
        if status != prev_status:
            self._apply_status()
        # 文字每次都要刷（包括 RUNNING 启动时归零 elapsed）
        self._update_text_for_status()

    def _tick(self):
        # 性能关键路径：500ms 一次，不能走 unpolish/polish。
        # 仅更新文字 + 触发重绘，状态属性保持 "running" 不变。
        self._blink_on = not self._blink_on
        self._elapsed_ms += 500
        if self._status == self.RUNNING:
            self._update_text(f"LLM 运行中 ({self._elapsed_ms / 1000:.1f}s)")
            # setText 已经触发 update；paintEvent 自行处理 _blink_on 的视觉差异
        # 兜底：极端情况下文本未变但需要重绘（如光晕需按 _blink_on 切换）
        self.update()

    def _apply_status(self):
        """状态真切换时调用：更新 dynamic property 并触发 QSS 重算（不再 unpolish）。"""
        if self._status == self.RUNNING:
            prop = "running"
        elif self._status == self.ERROR:
            prop = "error"
        else:
            prop = "idle"
        self.setProperty("status", prop)
        # unpolish 是不必要的：dynamic property 变化后 style().polish() 已足够重算 QSS。
        self.style().polish(self)

    def _update_text_for_status(self):
        if self._status == self.RUNNING:
            self._update_text(f"LLM 运行中 ({self._elapsed_ms / 1000:.1f}s)")
        elif self._status == self.ERROR:
            self._update_text("LLM 错误")
        else:
            self._update_text("LLM 就绪")

    def _update_text(self, text=None):
        if text is None:
            text = "LLM 就绪"
        self.setText(text)
