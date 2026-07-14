"""ShineHeKnowledge MCP Server 启动入口（源码开发用）"""
import atexit
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# pythonw.exe 没有 stdout/stderr，uvicorn 日志会崩溃，补上 dummy
# 保存引用以便进程退出时清理（实际长期运行进程由 OS 回收）
_devnull_handles = []
if sys.stdout is None:
    _h = open(os.devnull, "w", encoding="utf-8")
    _devnull_handles.append(_h)
    sys.stdout = _h
if sys.stderr is None:
    _h = open(os.devnull, "w", encoding="utf-8")
    _devnull_handles.append(_h)
    sys.stderr = _h


def _cleanup_devnull():
    """进程退出时关闭 devnull 句柄"""
    for h in _devnull_handles:
        try:
            h.close()
        except Exception:
            pass


atexit.register(_cleanup_devnull)

from src.mcp_cli import main  # noqa: E402

if __name__ == "__main__":
    main()
