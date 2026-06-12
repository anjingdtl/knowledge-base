"""首次启动检测 — 管理 Setup Wizard 触发时机

首次运行标记存储在 data/.first_run 文件中：
- 文件不存在 → 首次运行（应展示 Setup Wizard）
- 文件存在且内容为时间戳 → 非首次运行
"""
import logging
from datetime import datetime
from pathlib import Path

from src.utils.config import Config
from src.utils.paths import get_data_dir

logger = logging.getLogger(__name__)

_MARKER_FILE = ".first_run"


def _marker_path() -> Path:
    return get_data_dir() / _MARKER_FILE


def is_first_run() -> bool:
    """判断是否为首次启动。

    判定条件：
    1. data/.first_run 标记文件不存在
    2. 且 config.yaml 中未配置有效的 LLM API Key（非占位符）
    两者同时满足才视为首次运行。
    """
    marker = _marker_path()
    if marker.exists():
        return False

    # 即使标记文件不存在，如果已有有效 API Key 配置，
    # 说明是老用户或已通过其他方式配置过，不触发向导
    try:
        api_key = Config.get("llm.api_key", "")
        if api_key and not _is_placeholder(api_key):
            return False
    except Exception:
        pass

    return True


def mark_completed() -> None:
    """标记 Setup Wizard 已完成，写入当前时间戳"""
    marker = _marker_path()
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(datetime.now().isoformat(), encoding="utf-8")
    logger.info("Setup Wizard 已完成，标记写入 %s", marker)


def get_first_run_time() -> str | None:
    """获取首次运行完成的时间戳字符串，未完成则返回 None"""
    marker = _marker_path()
    if not marker.exists():
        return None
    return marker.read_text(encoding="utf-8").strip() or None


def _is_placeholder(value: str) -> bool:
    """检测 API Key 是否为占位符"""
    placeholders = {
        "your_llm_api_key",
        "your_embedding_api_key",
        "your_api_key",
        "",
    }
    return value.lower().strip() in placeholders
