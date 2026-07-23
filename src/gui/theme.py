"""主题管理器 — 桌面工作台的湖蓝金色双主题系统。"""
from pathlib import Path

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from src.utils.config import Config

RESOURCES_DIR = Path(__file__).parent / "resources"

# 桌面端主色：深湖蓝负责操作与层级，暖金负责状态与强调。
COCOA = "#0F5C67"
OLIVE = "#E2A64A"
PARCHMENT = "#F4F8F8"
WARM_NOIR = "#102426"
WARM_MOON = "#E7F2F1"
AMBER = "#E2A64A"
CORAL = "#BA4E55"

# 兼容旧常量名（外部模块可能直接 import 这些名称）
TEAL = COCOA
GOLD = OLIVE
CREAM = PARCHMENT
ROSE = CORAL
NOIR = WARM_NOIR
MOON = WARM_MOON
EMERALD = OLIVE

LIGHT = {
    "bg": PARCHMENT,
    "surface": "#FFFFFF",
    "surface_alt": "#F9FBFB",
    "surface_hover": "#EEF6F5",
    "panel": "#FFFFFF",
    "primary": COCOA,
    "on_accent": "#FFFFFF",
    "accent": OLIVE,
    "accent_2": COCOA,
    "accent_3": "#C7D8D9",
    "primary_hover": "#09424B",
    "primary_light": "rgba(15,92,103,10)",
    "accent_hover": "#C98B32",
    "accent_light": "rgba(226,166,74,14)",
    "accent_soft": "rgba(15,92,103,8)",
    "text": "#163034",
    "text_secondary": "#60757A",
    "text_dim": "#8A9DA1",
    "text_light": "#B5C6C8",
    "sidebar_bg": "#F9FBFB",
    "sidebar_text": "#60757A",
    "sidebar_active_text": COCOA,
    "border": "rgba(15,92,103,13)",
    "border_hover": "rgba(15,92,103,35)",
    "border_focus": COCOA,
    "border_card": "rgba(15,92,103,15)",
    "indicator_idle": OLIVE,
    "indicator_running": "#0F5C67",
    "indicator_error": CORAL,
    "mcp_online": "#0F5C67",
    "mcp_offline": CORAL,
    "success": "#0F5C67",
    "warning": AMBER,
    "danger": CORAL,
    "danger_bg": "rgba(186,78,85,10)",
    "danger_border": "rgba(186,78,85,55)",
    "scroll_handle": "rgba(96,117,122,30)",
    "scroll_handle_hover": "rgba(15,92,103,40)",
    "progress_bg": "rgba(96,117,122,20)",
    "garbled_fg": AMBER,
    "typing_dots": "#0F5C67",
    "tag_bg": "rgba(15,92,103,10)",
    "tag_text": "#0F5C67",
    "chat_user_bubble": "rgba(15,92,103,8)",
    "chat_ai_bubble": "#FFFFFF",
    "accent_surface": "rgba(15,92,103,8)",
    "shadow": "rgba(15,92,103,0)",
}

DARK = {
    "bg": WARM_NOIR,
    "surface": "#172C2E",
    "surface_alt": "#13292B",
    "surface_hover": "#20383B",
    "panel": "#172C2E",
    "primary": "#D8EBEA",
    "on_accent": WARM_NOIR,
    "accent": "#E2A64A",
    "accent_2": "#D8EBEA",
    "accent_3": "#E2A64A",
    "primary_hover": "#E7F2F1",
    "primary_light": "rgba(216,235,234,12)",
    "accent_hover": "#F0BC67",
    "accent_light": "rgba(226,166,74,16)",
    "accent_soft": "rgba(216,235,234,10)",
    "text": WARM_MOON,
    "text_secondary": "#A8C0C2",
    "text_dim": "#789397",
    "text_light": "#5B777B",
    "sidebar_bg": "#0C1C1E",
    "sidebar_text": "#A8C0C2",
    "sidebar_active_text": WARM_MOON,
    "border": "rgba(231,242,241,10)",
    "border_hover": "rgba(216,235,234,35)",
    "border_focus": "#E2A64A",
    "border_card": "rgba(231,242,241,8)",
    "indicator_idle": "#E2A64A",
    "indicator_running": WARM_MOON,
    "indicator_error": "#F27B82",
    "mcp_online": "#E2A64A",
    "mcp_offline": "#F27B82",
    "success": "#E2A64A",
    "warning": AMBER,
    "danger": "#F27B82",
    "danger_bg": "rgba(242,123,130,12)",
    "danger_border": "rgba(242,123,130,50)",
    "scroll_handle": "rgba(168,192,194,30)",
    "scroll_handle_hover": "rgba(216,235,234,45)",
    "progress_bg": "rgba(168,192,194,20)",
    "garbled_fg": AMBER,
    "typing_dots": "#D8EBEA",
    "tag_bg": "rgba(226,166,74,15)",
    "tag_text": "#E2A64A",
    "chat_user_bubble": "rgba(216,235,234,10)",
    "chat_ai_bubble": "#172C2E",
    "accent_surface": "rgba(216,235,234,8)",
    "shadow": "rgba(0,0,0,0)",
}

_THEMES = {"light": LIGHT, "dark": DARK}


def current_theme() -> str:
    return str(Config.get("appearance.theme", "light"))


def is_dark() -> bool:
    return current_theme() == "dark"


def colors() -> dict:
    return _THEMES.get(current_theme(), LIGHT)


def get_color(role: str) -> str:
    fallback = COCOA if not is_dark() else WARM_MOON
    return str(colors().get(role, fallback))


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


# GUI 以 Windows 中文无衬线为首选，避免宋体/衬线字体造成的字面不齐。
FONT_FAMILY = '"Microsoft YaHei UI", "Microsoft YaHei", "Noto Sans CJK SC", "Segoe UI", sans-serif'


def apply(app: QApplication):
    """加载当前主题 QSS 和字体。"""
    theme = current_theme()
    font_size = Config.get("appearance.font_size", 14)

    qss_file = "style-dark.qss" if theme == "dark" else "style.qss"
    qss_path = RESOURCES_DIR / qss_file

    if qss_path.exists():
        qss_text = qss_path.read_text(encoding="utf-8")
        # Replace longer/more-specific template names first to avoid
        # partial-match corruption (e.g. {{font_size}} inside {{font_size_lg}}).
        qss_text = qss_text.replace("{{font_size_brand}}", str(font_size + 8))
        qss_text = qss_text.replace("{{font_size_title}}", str(font_size + 7))
        qss_text = qss_text.replace("{{font_size_subtitle}}", str(font_size + 2))
        qss_text = qss_text.replace("{{font_size_lg}}", str(font_size + 1))
        qss_text = qss_text.replace("{{font_size_md}}", str(font_size + 1))
        qss_text = qss_text.replace("{{font_size_sm}}", str(max(11, font_size - 2)))
        qss_text = qss_text.replace("{{font_size_xs}}", str(max(10, font_size - 4)))
        qss_text = qss_text.replace("{{font_size}}", str(font_size))
        qss_text = qss_text.replace("{{gradient_accent}}", _gradient_accent())
        qss_text = qss_text.replace("{{gradient_accent_v}}", _gradient_accent_v())
        app.setStyleSheet(qss_text)

    font = QFont("Microsoft YaHei UI", font_size)
    font.setStyleHint(QFont.StyleHint.SansSerif)
    app.setFont(font)
