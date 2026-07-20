"""主题管理器 — Golden Time 暖色编辑感双主题系统

设计基调：暖白羊皮纸底色 + 深可可棕主色 + 橄榄/沙色辅色，
衬线字体优先，大圆角扁平无阴影，低对比暖色调，安静书桌气质。
"""
from pathlib import Path

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from src.utils.config import Config

RESOURCES_DIR = Path(__file__).parent / "resources"

# 浅色基础色 — Golden Time 暖色系
COCOA = "#3B352B"      # 深可可棕 — 主色（替代原 TEAL）
OLIVE = "#9B965F"      # 橄榄 — 辅色（替代原 GOLD）
PARCHMENT = "#FBFAF9"  # 羊皮纸暖白 — 底色（替代原 CREAM）
# 深色基础色 — 暖色暗调
WARM_NOIR = "#1C1B1A"  # 暖深棕黑（替代原 NOIR）
WARM_MOON = "#F5F2EC"  # 暖白（替代原 MOON）
AMBER = "#C9976C"      # 琥珀暖橙 — 警告/装饰（保留）
CORAL = "#EF4444"      # 暖红 — 危险/错误（替代原 CORAL 冷调）

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
    "surface_alt": "#F5F2EC",
    "surface_hover": "#EFE9DD",
    "panel": "#FFFFFF",
    "primary": COCOA,
    "on_accent": PARCHMENT,
    "accent": OLIVE,
    "accent_2": COCOA,
    "accent_3": "#CBC0AA",
    "primary_hover": "#2A2519",
    "primary_light": "rgba(59,53,43,10)",
    "accent_hover": "#8A8553",
    "accent_light": "rgba(155,150,95,12)",
    "accent_soft": "rgba(59,53,43,8)",
    "text": COCOA,
    "text_secondary": "#6B6358",
    "text_dim": "#9B9387",
    "text_light": "#BCB4A8",
    "sidebar_bg": "#F5F2EC",
    "sidebar_text": "#6B6358",
    "sidebar_active_text": COCOA,
    "border": "rgba(59,53,43,12)",
    "border_hover": "rgba(59,53,43,35)",
    "border_focus": COCOA,
    "border_card": "rgba(59,53,43,15)",
    "indicator_idle": OLIVE,
    "indicator_running": COCOA,
    "indicator_error": CORAL,
    "mcp_online": OLIVE,
    "mcp_offline": CORAL,
    "success": OLIVE,
    "warning": AMBER,
    "danger": CORAL,
    "danger_bg": "rgba(239,68,68,10)",
    "danger_border": "rgba(239,68,68,55)",
    "scroll_handle": "rgba(155,147,135,30)",
    "scroll_handle_hover": "rgba(59,53,43,40)",
    "progress_bg": "rgba(155,147,135,20)",
    "garbled_fg": AMBER,
    "typing_dots": COCOA,
    "tag_bg": "rgba(59,53,43,10)",
    "tag_text": COCOA,
    "chat_user_bubble": "rgba(59,53,43,8)",
    "chat_ai_bubble": "#FFFFFF",
    "accent_surface": "rgba(59,53,43,8)",
    "shadow": "rgba(59,53,43,0)",
}

DARK = {
    "bg": WARM_NOIR,
    "surface": "#2A2825",
    "surface_alt": "#252320",
    "surface_hover": "#353230",
    "panel": "#2A2825",
    "primary": "#E8E2D4",
    "on_accent": WARM_NOIR,
    "accent": "#B5AB97",
    "accent_2": "#E8E2D4",
    "accent_3": "#9B965F",
    "primary_hover": "#F5F2EC",
    "primary_light": "rgba(232,226,212,12)",
    "accent_hover": "#C8BEAA",
    "accent_light": "rgba(181,171,151,14)",
    "accent_soft": "rgba(232,226,212,10)",
    "text": WARM_MOON,
    "text_secondary": "#BCB4A8",
    "text_dim": "#8E8678",
    "text_light": "#6B6358",
    "sidebar_bg": "#161513",
    "sidebar_text": "#BCB4A8",
    "sidebar_active_text": WARM_MOON,
    "border": "rgba(245,242,236,10)",
    "border_hover": "rgba(232,226,212,35)",
    "border_focus": "#B5AB97",
    "border_card": "rgba(245,242,236,8)",
    "indicator_idle": "#B5AB97",
    "indicator_running": WARM_MOON,
    "indicator_error": "#F87171",
    "mcp_online": "#B5AB97",
    "mcp_offline": "#F87171",
    "success": "#B5AB97",
    "warning": AMBER,
    "danger": "#F87171",
    "danger_bg": "rgba(248,113,113,12)",
    "danger_border": "rgba(248,113,113,50)",
    "scroll_handle": "rgba(188,180,168,30)",
    "scroll_handle_hover": "rgba(232,226,212,45)",
    "progress_bg": "rgba(188,180,168,20)",
    "garbled_fg": AMBER,
    "typing_dots": WARM_MOON,
    "tag_bg": "rgba(181,171,151,15)",
    "tag_text": "#B5AB97",
    "chat_user_bubble": "rgba(232,226,212,10)",
    "chat_ai_bubble": "#2A2825",
    "accent_surface": "rgba(232,226,212,8)",
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


# 衬线字体优先 — 英文走 Georgia/Cambria，中文自动 fallback 到宋体
FONT_FAMILY = '"Georgia", "Cambria", "SimSun", "Songti SC", serif'


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

    # 衬线字体：Georgia 优先，Qt 会为中文自动 fallback 到系统宋体
    font = QFont("Georgia", font_size)
    font.setStyleHint(QFont.StyleHint.Serif)
    app.setFont(font)
