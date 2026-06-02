"""Configure ShineHeKnowledge MCP for common Agent clients."""
from __future__ import annotations

import json
import os
import platform
import shutil
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SERVER_NAME = "shinehe-kb"


def get_agent_config_paths() -> dict[str, Path]:
    home = Path.home()
    if platform.system() == "Windows":
        appdata = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
        return {
            "claude-code": home / ".claude.json",
            "cursor": home / ".cursor" / "mcp.json",
            "cline": appdata
            / "Code"
            / "User"
            / "globalStorage"
            / "saoudrizwan.claude-dev"
            / "settings"
            / "cline_mcp_settings.json",
            "windsurf": appdata / "WindSurf" / "mcp_settings.json",
            "roo-code": appdata
            / "Code"
            / "User"
            / "globalStorage"
            / "rooveterinaryinc.roo-cline"
            / "settings"
            / "cline_mcp_settings.json",
            "opencode": home / ".config" / "opencode" / "opencode.json",
        }

    support = home / "Library" / "Application Support"
    return {
        "claude-code": home / ".claude.json",
        "cursor": home / ".cursor" / "mcp.json",
        "cline": support
        / "Code"
        / "User"
        / "globalStorage"
        / "saoudrizwan.claude-dev"
        / "settings"
        / "cline_mcp_settings.json",
        "windsurf": home / ".codeium" / "windsurf" / "mcp_config.json",
        "roo-code": support
        / "Code"
        / "User"
        / "globalStorage"
        / "rooveterinaryinc.roo-cline"
        / "settings"
        / "cline_mcp_settings.json",
        "opencode": home / ".config" / "opencode" / "opencode.json",
    }


def build_server_config() -> dict:
    shinehe_cmd = shutil.which("shinehe-mcp")
    if shinehe_cmd:
        return {
            "command": "shinehe-mcp",
            "args": [],
            "cwd": str(PROJECT_ROOT),
            "env": {"SHINEHE_HOME": str(PROJECT_ROOT)},
            "type": "stdio",
        }

    return {
        "command": sys.executable,
        "args": [str(PROJECT_ROOT / "run_mcp.py")],
        "cwd": str(PROJECT_ROOT),
        "env": {"SHINEHE_HOME": str(PROJECT_ROOT)},
        "type": "stdio",
    }


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: Path, config: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
        f.write("\n")


def add_to_agent_config(agent_name: str, config_path: Path, server_config: dict) -> None:
    config = _read_json(config_path)

    if agent_name == "opencode":
        config.setdefault("mcp", {})
        config["mcp"][SERVER_NAME] = {
            "type": "local",
            "command": [server_config["command"], *server_config.get("args", [])],
            "environment": server_config.get("env", {}),
            "enabled": True,
        }
    else:
        config.setdefault("mcpServers", {})
        config["mcpServers"][SERVER_NAME] = server_config

    _write_json(config_path, config)
    print(f"[OK] {agent_name}: {config_path}")


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
