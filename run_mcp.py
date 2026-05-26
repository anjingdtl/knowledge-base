"""ShineHeKnowledge MCP Server 启动入口（源码开发用）"""
import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# pythonw.exe 没有 stdout/stderr，uvicorn 日志会崩溃，补上 dummy
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

from src.mcp_cli import main

if __name__ == "__main__":
    main()
