"""Configure ShineHeKnowledge MCP for common Agent clients.

核心逻辑已迁移至 src.services.project_setup，本文件保留为独立脚本入口。
可通过 `python scripts/setup_mcp.py` 交互式运行。
"""
from __future__ import annotations

import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中，以支持直接脚本运行
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.services.project_setup import (
    add_to_agent_config,
    build_server_config,
    get_agent_config_paths,
)


def main() -> None:
    print("=" * 56)
    print("  ShineHeKnowledge MCP 一键配置")
    print("=" * 56)

    server_config = build_server_config()
    print(f"\nMCP stdio command: {server_config['command']}")
    print(f"MCP args: {server_config.get('args', [])}")

    agents = list(get_agent_config_paths().items())
    print("\n可配置 Agent:")
    for idx, (name, path) in enumerate(agents, 1):
        installed = "已检测" if path.parent.exists() else "未检测"
        marker = " *已有配置" if path.exists() else ""
        print(f"  {idx}. {name:12s} [{installed}]{marker}")
    print("  0. 全部配置")

    choice = input("\n请选择编号，多个编号用逗号分隔: ").strip()
    if choice == "0":
        selected = agents
    else:
        indices = [int(x.strip()) - 1 for x in choice.split(",") if x.strip().isdigit()]
        selected = [agents[i] for i in indices if 0 <= i < len(agents)]

    if not selected:
        print("未选择任何 Agent。")
        return

    for agent_name, config_path in selected:
        add_to_agent_config(agent_name, config_path, server_config)

    print(f"\n完成：已配置 {len(selected)} 个 Agent。请重启对应 Agent 使配置生效。")


if __name__ == "__main__":
    main()
