"""空状态引导组件。"""
from PySide6.QtCore import Qt, QSize
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
)

from src.gui.icons import NAV, icon as make_icon
from src.gui.theme import get_color


class EmptyState(QWidget):
    """可复用的空状态占位组件。"""

    def __init__(
        self,
        icon: str = "",
        title: str = "",
        description: str = "",
        buttons: list[dict] | None = None,
        icon_key: str | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._build_ui(icon, title, description, buttons or [], icon_key)

    def _build_ui(
        self,
        icon: str,
        title: str,
        description: str,
        buttons: list[dict],
        icon_key: str | None,
    ):
        colors = lambda role: get_color(role)
        try:
            from src.utils.config import Config
            base_font = Config.get("appearance.font_size", 13)
        except Exception:
            base_font = 13

        outer = QVBoxLayout(self)
        outer.setAlignment(Qt.AlignCenter)
        outer.setContentsMargins(40, 40, 40, 40)

        inner = QVBoxLayout()
        inner.setAlignment(Qt.AlignCenter)
        inner.setSpacing(12)

        if icon_key and NAV.get(icon_key):
            icon_label = QLabel()
            icon_label.setAlignment(Qt.AlignCenter)
            pixmap = make_icon(NAV[icon_key], "accent", 1.0).pixmap(QSize(46, 46))
            icon_label.setPixmap(pixmap)
            icon_label.setStyleSheet("border: none; background: transparent;")
            inner.addWidget(icon_label)
        elif icon:
            icon_label = QLabel(icon)
            icon_label.setAlignment(Qt.AlignCenter)
            icon_label.setStyleSheet("font-size: 42px; border: none; background: transparent;")
            inner.addWidget(icon_label)

        # 标题
        if title:
            title_font = base_font + 2
            title_label = QLabel(title)
            title_label.setAlignment(Qt.AlignCenter)
            title_label.setStyleSheet(
                f"font-size: {title_font}px; font-weight: bold;"
                f"color: {colors('text')}; border: none; background: transparent;"
            )
            inner.addWidget(title_label)

        # 描述
        if description:
            desc_font = max(10, base_font - 1)
            desc_label = QLabel(description)
            desc_label.setAlignment(Qt.AlignCenter)
            desc_label.setWordWrap(True)
            desc_label.setMaximumWidth(360)
            desc_label.setStyleSheet(
                f"font-size: {desc_font}px;"
                f"color: {colors('text_dim')}; border: none; background: transparent;"
                f"line-height: 1.5;"
            )
            inner.addWidget(desc_label)

        # 操作按钮
        if buttons:
            btn_row = QHBoxLayout()
            btn_row.setAlignment(Qt.AlignCenter)
            btn_row.setSpacing(12)
            accent = colors("accent")
            accent_hover = colors("accent_hover")
            text_on_accent = "#ffffff"
            for btn_info in buttons:
                btn = QPushButton(btn_info["text"])
                if btn_info.get("objectName"):
                    btn.setObjectName(btn_info["objectName"])
                btn.setCursor(Qt.PointingHandCursor)
                btn.setFixedHeight(36)
                btn.setMinimumWidth(100)
                if not btn_info.get("objectName"):
                    btn.setStyleSheet(
                        f"QPushButton {{"
                        f"  background: {accent}; color: {text_on_accent};"
                        f"  border: none; border-radius: 8px;"
                        f"  font-size: {base_font}px; font-weight: bold;"
                        f"  padding: 0 20px;"
                        f"}}"
                        f"QPushButton:hover {{ background: {accent_hover}; }}"
                    )
                if btn_info.get("callback"):
                    btn.clicked.connect(btn_info["callback"])
                btn_row.addWidget(btn)
            inner.addLayout(btn_row)

        outer.addLayout(inner)
        self.setMaximumWidth(500)
