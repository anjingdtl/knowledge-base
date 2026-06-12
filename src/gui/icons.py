"""QtAwesome 图标辅助层，缺失依赖时安全降级为空图标。"""
from __future__ import annotations

from PySide6.QtCore import QSize
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QPushButton, QToolButton

from src.gui.theme import get_color

try:
    import qtawesome as qta
except Exception:  # pragma: no cover - 运行环境缺少可选 GUI 依赖时降级
    qta = None


NAV = {
    "knowledge": "fa5s.database",
    "chat": "fa5s.comments",
    "catalog": "fa5s.sitemap",
    "wiki": "fa5s.book-open",
    "settings": "fa5s.cog",
    "import": "fa5s.file-import",
    "add": "fa5s.plus",
    "more": "fa5s.ellipsis-h",
    "refresh": "fa5s.sync-alt",
    "rename": "fa5s.pen",
    "quality": "fa5s.search",
    "dedup": "fa5s.layer-group",
    "send": "fa5s.paper-plane",
    "save": "fa5s.save",
    "new": "fa5s.plus-circle",
    "classify": "fa5s.project-diagram",
    "catalog_generate": "fa5s.magic",
    "lint": "fa5s.heartbeat",
    "close": "fa5s.times",
    "delete": "fa5s.trash",
    "trash": "fa5s.trash-alt",
    "restore": "fa5s.trash-restore",
    "approve": "fa5s.check",
    "reject": "fa5s.undo",
    "upload": "fa5s.cloud-upload-alt",
    "folder": "fa5s.folder-open",
    "remove": "fa5s.minus-circle",
    "link": "fa5s.link",
    "paste": "fa5s.clipboard",
    "mcp": "fa5s.server",
    "llm": "fa5s.microchip",
    "graph": "fa5s.project-diagram",
    "graph_generate": "fa5s.magic",
    "layout": "fa5s.th",
    "fullscreen": "fa5s.expand",
    "play": "fa5s.play",
    "stop": "fa5s.stop",
    "database": "fa5s.database",
    "sync": "fa5s.sync",
    "exchange": "fa5s.exchange-alt",
}


def icon(name: str, color_role: str = "text_dim", scale_factor: float = 1.0) -> QIcon:
    if qta is None:
        return QIcon()
    return qta.icon(name, color=get_color(color_role), scale_factor=scale_factor)


def set_icon(
    button: QPushButton | QToolButton,
    name: str,
    color_role: str = "text_dim",
    size: int = 16,
) -> None:
    button.setIcon(icon(name, color_role=color_role))
    button.setIconSize(QSize(size, size))


def set_named_icon(
    button: QPushButton | QToolButton,
    key: str,
    color_role: str = "text_dim",
    size: int = 16,
) -> None:
    name = NAV.get(key)
    if name:
        set_icon(button, name, color_role=color_role, size=size)
