"""LLM 状态指示器，与 MCP 状态统一风格。"""
from PySide6.QtWidgets import QLabel
from PySide6.QtCore import Qt, QTimer, QPointF
from PySide6.QtGui import QColor, QRadialGradient, QBrush, QPainter, QPolygonF


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
                center_color = QColor(16, 185, 129, 70)
                edge_color = QColor(16, 185, 129, 0)
            else:
                center_color = QColor(14, 165, 183, 75)
                edge_color = QColor(14, 165, 183, 0)
            glow = QRadialGradient(QPointF(center_x, center_y), 12)
            glow.setColorAt(0, center_color)
            glow.setColorAt(1, edge_color)
            painter.setBrush(QBrush(glow))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QPointF(center_x, center_y), 12, 12)
            painter.end()
        super().paintEvent(event)

    def set_status(self, status: str, detail: str = ""):
        self._status = status
        self._detail = detail
        if status == self.RUNNING:
            self._blink_on = True
            self._elapsed_ms = 0
            self._timer.start()
        else:
            self._timer.stop()
        self._apply()

    def _tick(self):
        self._blink_on = not self._blink_on
        self._elapsed_ms += 500
        self._apply()

    def _apply(self):
        if self._status == self.RUNNING:
            prop = "running" if self._blink_on else "dim"
            elapsed = self._elapsed_ms / 1000
            text = f"LLM 运行中 ({elapsed:.1f}s)"
        elif self._status == self.ERROR:
            prop = "error"
            text = "LLM 错误"
        else:
            prop = "idle"
            text = "LLM 就绪"

        self.setProperty("status", prop)
        self._update_text(text)
        self.style().unpolish(self)
        self.style().polish(self)

    def _update_text(self, text=None):
        if text is None:
            text = "LLM 就绪"
        self.setText(text)
