"""Create root Windows shortcuts for ShineHeKnowledge.

The app shortcut is written to the project root and replaces the old
"泰坦知识库.lnk" shortcut when present.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent
ICON_PATH = PROJECT_DIR / "icon" / "knolege.ico"
APP_TARGET = PROJECT_DIR / "start_app.bat"
MCP_TARGET = PROJECT_DIR / "start_mcp.bat"
APP_SHORTCUT = PROJECT_DIR / "ShineHeKnowledge 一键启动.lnk"
MCP_SHORTCUT = PROJECT_DIR / "ShineHeKnowledge MCP服务.lnk"
OLD_SHORTCUTS = [
    PROJECT_DIR / "泰坦知识库.lnk",
    PROJECT_DIR / "ShineHeKnowledge 涓€閿惎鍔?lnk",
    PROJECT_DIR / "ShineHeKnowledge MCP鏈嶅姟.lnk",
]


def _ps_quote(value: Path | str) -> str:
    return str(value).replace("'", "''")


def create_shortcut(target: Path, shortcut: Path, description: str) -> None:
    if sys.platform != "win32":
        raise RuntimeError("Windows .lnk shortcuts can only be created on Windows.")
    if not target.exists():
        raise FileNotFoundError(target)
    if not ICON_PATH.exists():
        raise FileNotFoundError(ICON_PATH)

    script = f"""
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut('{_ps_quote(shortcut)}')
$shortcut.TargetPath = '{_ps_quote(target)}'
$shortcut.WorkingDirectory = '{_ps_quote(PROJECT_DIR)}'
$shortcut.IconLocation = '{_ps_quote(ICON_PATH)},0'
$shortcut.Description = '{description.replace("'", "''")}'
$shortcut.Save()
"""
    subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        check=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Create ShineHeKnowledge Windows shortcuts.")
    parser.add_argument(
        "--mcp",
        action="store_true",
        help="Also create a shortcut for the streamable-http MCP service.",
    )
    args = parser.parse_args()

    create_shortcut(APP_TARGET, APP_SHORTCUT, "ShineHeKnowledge 一键启动")
    for old_shortcut in OLD_SHORTCUTS:
        if old_shortcut.exists():
            old_shortcut.unlink()
    print(f"Created: {APP_SHORTCUT}")

    if args.mcp:
        create_shortcut(MCP_TARGET, MCP_SHORTCUT, "ShineHeKnowledge MCP服务")
        print(f"Created: {MCP_SHORTCUT}")


if __name__ == "__main__":
    main()
