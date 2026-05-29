"""主题管理器 — 学院书斋双主题系统"""
from pathlib import Path

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from src.utils.config import Config

RESOURCES_DIR = Path(__file__).parent / "resources"

# 浅色基础色
TEAL = "#1F4A48"
GOLD = "#C9A87C"
CREAM = "#F5F1EB"
# 深色基础色
ROSE = "#D64A6C"
NOIR = "#222222"
MOON = "#F8F9FA"

EMERALD = "#3A8A6E"
AMBER = "#C9976C"
CORAL = "#C45B5B"

LIGHT = {
    "bg": CREAM,
    "surface": "#FFFFFF",
    "surface_alt": "#FAF8F5",
    "surface_hover": "#F0EBE3",
    "panel": "#FFFFFF",
    "primary": TEAL,
    "on_accent": "#FFFFFF",
    "accent": GOLD,
    "accent_2": TEAL,
    "accent_3": "#8B7355",
    "primary_hover": "#163B39",
    "primary_light": "rgba(31,74,72,10)",
    "accent_hover": "#B89560",
    "accent_light": "rgba(201,168,124,12)",
    "accent_soft": "rgba(31,74,72,8)",
    "text": "#1A1A1A",
    "text_secondary": "#5C5C5C",
    "text_dim": "#8C8C8C",
    "text_light": "#ACACAC",
    "sidebar_bg": "#EDE8E0",
    "sidebar_text": "#5C5C5C",
    "sidebar_active_text": TEAL,
    "border": "rgba(0,0,0,10)",
    "border_hover": "rgba(31,74,72,35)",
    "border_focus": TEAL,
    "border_card": "rgba(31,74,72,15)",
    "indicator_idle": EMERALD,
    "indicator_running": TEAL,
    "indicator_error": CORAL,
    "mcp_online": EMERALD,
    "mcp_offline": CORAL,
    "success": EMERALD,
    "warning": AMBER,
    "danger": CORAL,
    "danger_bg": "rgba(196,91,91,10)",
    "danger_border": "rgba(196,91,91,55)",
    "scroll_handle": "rgba(140,140,140,25)",
    "scroll_handle_hover": "rgba(31,74,72,40)",
    "progress_bg": "rgba(140,140,140,20)",
    "garbled_fg": AMBER,
    "typing_dots": TEAL,
    "tag_bg": "rgba(31,74,72,10)",
    "tag_text": "#1F4A48",
    "chat_user_bubble": "rgba(31,74,72,8)",
    "chat_ai_bubble": "#FFFFFF",
    "accent_surface": "rgba(31,74,72,8)",
    "shadow": "rgba(31,74,72,12)",
}

DARK = {
    "bg": NOIR,
    "surface": "#2C2C2C",
    "surface_alt": "#272727",
    "surface_hover": "#363636",
    "panel": "#272727",
    "primary": ROSE,
    "on_accent": "#FFFFFF",
    "accent": "#E8A87C",
    "accent_2": ROSE,
    "accent_3": "#D4A574",
    "primary_hover": "#E55D7D",
    "primary_light": "rgba(214,74,108,15)",
    "accent_hover": "#F0B88E",
    "accent_light": "rgba(232,168,124,12)",
    "accent_soft": "rgba(214,74,108,10)",
    "text": MOON,
    "text_secondary": "#B8B8B8",
    "text_dim": "#808080",
    "text_light": "#666666",
    "sidebar_bg": "#1C1C1C",
    "sidebar_text": "#B8B8B8",
    "sidebar_active_text": MOON,
    "border": "rgba(255,255,255,10)",
    "border_hover": "rgba(214,74,108,35)",
    "border_focus": ROSE,
    "border_card": "rgba(255,255,255,8)",
    "indicator_idle": "#4CAF82",
    "indicator_running": ROSE,
    "indicator_error": "#E55D7D",
    "mcp_online": "#4CAF82",
    "mcp_offline": "#E55D7D",
    "success": "#4CAF82",
    "warning": "#E8A87C",
    "danger": "#E55D7D",
    "danger_bg": "rgba(229,93,125,10)",
    "danger_border": "rgba(229,93,125,55)",
    "scroll_handle": "rgba(128,128,128,25)",
    "scroll_handle_hover": "rgba(214,74,108,40)",
    "progress_bg": "rgba(128,128,128,20)",
    "garbled_fg": "#E8A87C",
    "typing_dots": ROSE,
    "tag_bg": "rgba(214,74,108,15)",
    "tag_text": "#E8A87C",
    "chat_user_bubble": "rgba(214,74,108,10)",
    "chat_ai_bubble": "#2C2C2C",
    "accent_surface": "rgba(214,74,108,10)",
    "shadow": "rgba(0,0,0,35)",
}

_THEMES = {"light": LIGHT, "dark": DARK}


def current_theme() -> str:
    return Config.get("appearance.theme", "light")


def is_dark() -> bool:
    return current_theme() == "dark"


def colors() -> dict:
    return _THEMES.get(current_theme(), LIGHT)


def get_color(role: str) -> str:
    fallback = "#1A1A1A" if not is_dark() else "#F8F9FA"
    return colors().get(role, fallback)


def _gradient_accent() -> str:
    palette = colors()
    return (
        "qlineargradient(x1:0, y1:0, x2:1, y2:0, "
        f"stop:0 {palette['primary']}, stop:1 {palette['accent']})"
    )


def _gradient_accent_v() -> str:
    palette = colors()
    return (
        "qlineargradient(x1:0, y1:0, x2:0, y2:1, "
        f"stop:0 {palette['primary']}, stop:1 {palette['accent']})"
    )


FONT_FAMILY = '"Inter", "Segoe UI", "Microsoft YaHei", sans-serif'


def apply(app: QApplication):
    """加载当前主题 QSS 和字体。"""
    theme = current_theme()
    font_size = Config.get("appearance.font_size", 14)

    qss_file = "style-dark.qss" if theme == "dark" else "style.qss"
    qss_path = RESOURCES_DIR / qss_file

    if qss_path.exists():
        qss_text = qss_path.read_text(encoding="utf-8")
        qss_text = qss_text.replace("{{font_size}}", str(font_size))
        qss_text = qss_text.replace("{{font_size_lg}}", str(font_size + 1))
        qss_text = qss_text.replace("{{font_size_md}}", str(font_size + 1))
        qss_text = qss_text.replace("{{font_size_brand}}", str(font_size + 8))
        qss_text = qss_text.replace("{{font_size_sm}}", str(max(11, font_size - 2)))
        qss_text = qss_text.replace("{{font_size_title}}", str(font_size + 7))
        qss_text = qss_text.replace("{{font_size_subtitle}}", str(font_size + 2))
        qss_text = qss_text.replace("{{font_size_xs}}", str(max(10, font_size - 4)))
        qss_text = qss_text.replace("{{gradient_accent}}", _gradient_accent())
        qss_text = qss_text.replace("{{gradient_accent_v}}", _gradient_accent_v())
        app.setStyleSheet(qss_text)

    font = QFont("Microsoft YaHei", font_size)
    font.setStyleHint(QFont.SansSerif)
    app.setFont(font)
