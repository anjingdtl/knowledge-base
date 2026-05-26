"""主题管理器 - ShineHe Knowledge 现代双主题系统"""
from pathlib import Path

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from src.utils.config import Config

RESOURCES_DIR = Path(__file__).parent / "resources"

INK = "#101827"
SLATE = "#334155"
MUTED = "#64748B"
PAPER = "#F7FAFD"
CANVAS = "#EEF3F8"
CYAN = "#0EA5B7"
BLUE = "#2563EB"
INDIGO = "#6366F1"
EMERALD = "#10B981"
AMBER = "#F59E0B"
RED = "#EF4444"

DARK_CANVAS = "#0B1020"
DARK_PANEL = "#111827"
DARK_SURFACE = "#182235"
DARK_TEXT = "#E5EDF7"
DARK_DIM = "#95A3B8"
DARK_MUTED = "#667085"

LIGHT = {
    "bg": CANVAS,
    "surface": PAPER,
    "surface_alt": "#FFFFFF",
    "surface_hover": "#EAF2FA",
    "panel": "#FFFFFF",
    "accent": BLUE,
    "on_accent": "#FFFFFF",
    "accent_2": CYAN,
    "accent_3": INDIGO,
    "accent_hover": "#1D4ED8",
    "accent_soft": "rgba(37, 99, 235, 24)",
    "text": INK,
    "text_dim": MUTED,
    "text_light": "#94A3B8",
    "sidebar_bg": "#F8FBFF",
    "sidebar_text": SLATE,
    "sidebar_active_text": INK,
    "border": "rgba(31, 42, 68, 22)",
    "border_hover": "rgba(37, 99, 235, 70)",
    "border_focus": BLUE,
    "border_card": "rgba(37, 99, 235, 38)",
    "indicator_idle": EMERALD,
    "indicator_running": BLUE,
    "indicator_error": RED,
    "mcp_online": EMERALD,
    "mcp_offline": RED,
    "success": EMERALD,
    "warning": AMBER,
    "danger": RED,
    "danger_bg": "rgba(239, 68, 68, 18)",
    "danger_border": "rgba(239, 68, 68, 80)",
    "scroll_handle": "rgba(100, 116, 139, 70)",
    "scroll_handle_hover": "rgba(37, 99, 235, 115)",
    "progress_bg": "rgba(100, 116, 139, 40)",
    "garbled_fg": AMBER,
    "typing_dots": CYAN,
    "tag_bg": "rgba(14, 165, 183, 24)",
    "tag_text": "#0F766E",
    "chat_user_bubble": "rgba(37, 99, 235, 24)",
    "chat_ai_bubble": "#FFFFFF",
    "accent_surface": "rgba(37, 99, 235, 24)",
    "shadow": "rgba(15, 23, 42, 45)",
}

DARK = {
    "bg": DARK_CANVAS,
    "surface": DARK_SURFACE,
    "surface_alt": DARK_PANEL,
    "surface_hover": "#1D2A40",
    "panel": "#0F172A",
    "accent": CYAN,
    "on_accent": "#FFFFFF",
    "accent_2": INDIGO,
    "accent_3": EMERALD,
    "accent_hover": "#22D3EE",
    "accent_soft": "rgba(14, 165, 183, 32)",
    "text": DARK_TEXT,
    "text_dim": DARK_DIM,
    "text_light": DARK_MUTED,
    "sidebar_bg": "#0A0F1D",
    "sidebar_text": "#A7B3C7",
    "sidebar_active_text": "#FFFFFF",
    "border": "rgba(148, 163, 184, 26)",
    "border_hover": "rgba(14, 165, 183, 90)",
    "border_focus": CYAN,
    "border_card": "rgba(14, 165, 183, 52)",
    "indicator_idle": EMERALD,
    "indicator_running": CYAN,
    "indicator_error": RED,
    "mcp_online": EMERALD,
    "mcp_offline": RED,
    "success": EMERALD,
    "warning": AMBER,
    "danger": RED,
    "danger_bg": "rgba(239, 68, 68, 28)",
    "danger_border": "rgba(239, 68, 68, 95)",
    "scroll_handle": "rgba(148, 163, 184, 70)",
    "scroll_handle_hover": "rgba(14, 165, 183, 130)",
    "progress_bg": "rgba(148, 163, 184, 35)",
    "garbled_fg": AMBER,
    "typing_dots": CYAN,
    "tag_bg": "rgba(14, 165, 183, 30)",
    "tag_text": "#67E8F9",
    "chat_user_bubble": "rgba(37, 99, 235, 32)",
    "chat_ai_bubble": "rgba(255, 255, 255, 14)",
    "accent_surface": "rgba(14, 165, 183, 30)",
    "shadow": "rgba(0, 0, 0, 90)",
}

_THEMES = {"light": LIGHT, "dark": DARK}


def current_theme() -> str:
    return Config.get("appearance.theme", "light")


def is_dark() -> bool:
    return current_theme() == "dark"


def colors() -> dict:
    return _THEMES.get(current_theme(), LIGHT)


def get_color(role: str) -> str:
    fallback = INK if not is_dark() else DARK_TEXT
    return colors().get(role, fallback)


def _gradient_accent() -> str:
    palette = colors()
    return (
        "qlineargradient(x1:0, y1:0, x2:1, y2:0, "
        f"stop:0 {palette['accent']}, stop:1 {palette['accent_2']})"
    )


def _gradient_accent_v() -> str:
    palette = colors()
    return (
        "qlineargradient(x1:0, y1:0, x2:0, y2:1, "
        f"stop:0 {palette['accent']}, stop:1 {palette['accent_2']})"
    )


def apply(app: QApplication):
    """加载当前主题 QSS 和字体。"""
    theme = current_theme()
    font_size = Config.get("appearance.font_size", 13)

    qss_file = "style-dark.qss" if theme == "dark" else "style.qss"
    qss_path = RESOURCES_DIR / qss_file

    if qss_path.exists():
        qss_text = qss_path.read_text(encoding="utf-8")
        qss_text = qss_text.replace("{{font_size}}", str(font_size))
        qss_text = qss_text.replace("{{font_size_lg}}", str(font_size + 1))
        qss_text = qss_text.replace("{{font_size_brand}}", str(font_size + 5))
        qss_text = qss_text.replace("{{font_size_sm}}", str(max(10, font_size - 2)))
        qss_text = qss_text.replace("{{font_size_title}}", str(font_size + 7))
        qss_text = qss_text.replace("{{font_size_subtitle}}", str(font_size + 2))
        qss_text = qss_text.replace("{{font_size_xs}}", str(max(9, font_size - 4)))
        qss_text = qss_text.replace("{{gradient_accent}}", _gradient_accent())
        qss_text = qss_text.replace("{{gradient_accent_v}}", _gradient_accent_v())
        app.setStyleSheet(qss_text)

    font = QFont("Microsoft YaHei", font_size)
    app.setFont(font)
