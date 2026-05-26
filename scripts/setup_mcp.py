"""一键配置 ShineHeKnowledge MCP 到各种 Agent"""
import json
import os
import platform
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def get_agent_config_paths() -> dict[str, Path]:
    """返回各 Agent 的 MCP 配置文件路径"""
    paths = {}
    home = Path.home()

    if platform.system() == "Windows":
        appdata = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
        paths["claude-code"] = home / ".claude.json"
        paths["cursor"] = home / ".cursor" / "mcp.json"
        paths["cline"] = (
            appdata / "Code" / "User" / "globalStorage"
            / "saoudrizwan.claude-dev" / "settings" / "cline_mcp_settings.json"
        )
        paths["windsurf"] = appdata / "WindSurf" / "mcp_settings.json"
        paths["roo-code"] = (
            appdata / "Code" / "User" / "globalStorage"
            / "rooveterinaryinc.roo-cline" / "settings" / "cline_mcp_settings.json"
        )
        paths["opencode"] = home / ".config" / "opencode" / "opencode.json"
    else:
        support = home / "Library" / "Application Support"
        paths["claude-code"] = home / ".claude.json"
        paths["cursor"] = home / ".cursor" / "mcp.json"
        paths["cline"] = (
            support / "Code" / "User" / "globalStorage"
            / "saoudrizwan.claude-dev" / "settings" / "cline_mcp_settings.json"
        )
        paths["windsurf"] = home / ".codeium" / "windsurf" / "mcp_config.json"
        paths["roo-code"] = (
            support / "Code" / "User" / "globalStorage"
            / "rooveterinaryinc.roo-cline" / "settings" / "cline_mcp_settings.json"
        )
        paths["opencode"] = home / ".config" / "opencode" / "opencode.json"

    return paths


def build_server_config() -> dict:
    """构建 MCP Server 配置片段"""
    shinehe_cmd = shutil.which("shinehe-mcp")
    if shinehe_cmd:
        return {
            "command": "shinehe-mcp",
            "args": [],
            "env": {"SHINEHE_HOME": str(PROJECT_ROOT)},
        }
    # Windows 上不要用 cmd /c 包装 Python，会拦截 stdin 破坏 JSON-RPC
    # 使用绝对路径避免子进程 PATH 找不到 python
    config = {
        "command": sys.executable,
        "args": [str(PROJECT_ROOT / "run_mcp.py")],
        "cwd": str(PROJECT_ROOT),
    }
    if platform.system() == "Windows":
        config["env"] = {"SHINEHE_HOME": str(PROJECT_ROOT)}
    else:
        config["env"] = {"SHINEHE_HOME": str(PROJECT_ROOT)}
    return config


def add_to_agent_config(agent_name: str, config_path: Path, server_config: dict):
    """将 MCP 配置添加到 Agent 的配置文件"""
    config_path.parent.mkdir(parents=True, exist_ok=True)

    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
    else:
        config = {}

    # OpenCode 使用不同的配置格式
    if agent_name == "opencode":
        command_list = [server_config["command"]] + server_config.get("args", [])
        opencode_config = {
            "type": "local",
            "command": command_list,
            "environment": server_config.get("env", {}),
            "enabled": True,
        }
        if "mcp" not in config:
            config["mcp"] = {}
        config["mcp"]["shinehe-kb"] = opencode_config
    else:
        if "mcpServers" not in config:
            config["mcpServers"] = {}
        config["mcpServers"]["shinehe-kb"] = server_config

    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

    print(f"  [OK] {agent_name}: {config_path}")


def main():
    print("=" * 50)
    print("  ShineHeKnowledge MCP 一键配置")
    print("=" * 50)

    # 检测安装方式
    shinehe_cmd = shutil.which("shinehe-mcp")
    server_config = build_server_config()

    if shinehe_cmd:
        print(f"\n[检测到] shinehe-mcp 已安装: {shinehe_cmd}")
    else:
        print(f"\n[源码模式] 使用: {sys.executable} {PROJECT_ROOT / 'run_mcp.py'}")

    # 列出可选 Agent
    agents = get_agent_config_paths()
    items = list(agents.items())
    print("\n可选 Agent:")
    for i, (name, path) in enumerate(items, 1):
        parent_exists = "已安装" if path.parent.exists() else "未检测到"
        exists_mark = " *" if path.exists() else ""
        print(f"  {i}. {name:15s} [{parent_exists}]{exists_mark}")
    print(f"  0. 全部配置")

    choice = input("\n请选择 (0-6，多选用逗号分隔): ").strip()

    if choice == "0":
        targets = [n for n, _ in items]
    else:
        indices = [int(c.strip()) - 1 for c in choice.split(",") if c.strip().isdigit()]
        targets = [items[i][0] for i in indices if 0 <= i < len(items)]

    if not targets:
        print("\n未选择任何 Agent。")
        return

    print()
    for agent_name in targets:
        config_path = agents[agent_name]
        add_to_agent_config(agent_name, config_path, server_config)

    print(f"\n[DONE] 已配置 {len(targets)} 个 Agent。")
    print("请重启对应 Agent 使配置生效。")


if __name__ == "__main__":
    main()
