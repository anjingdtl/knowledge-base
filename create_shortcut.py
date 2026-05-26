"""生成 Windows 桌面快捷方式 (.lnk)

运行此脚本后，项目目录下会生成 ShineHeKnowledge.lnk，
复制到桌面即可双击启动泰坦知识库。
"""
import sys
import os
from pathlib import Path
from ctypes import windll, c_buffer, sizeof, byref
from ctypes.wintypes import DWORD, HANDLE, BOOL, HRESULT
import pythoncom
from win32com.shell import shell, shellcon


PROJECT_DIR = Path(__file__).resolve().parent
ICON_PATH = PROJECT_DIR / "icon" / "knolege.ico"
MAIN_PY = PROJECT_DIR / "main.py"
SHORTCUT_NAME = "ShineHeKnowledge.lnk"
SHORTCUT_PATH = PROJECT_DIR / SHORTCUT_NAME


def create_shortcut():
    if not ICON_PATH.exists():
        print(f"错误: 图标文件不存在: {ICON_PATH}")
        sys.exit(1)

    if not MAIN_PY.exists():
        print(f"错误: 入口文件不存在: {MAIN_PY}")
        sys.exit(1)

    # 使用 pythonw.exe 避免 Windows 弹出终端窗口
    python_exe = sys.executable.replace("python.exe", "pythonw.exe")

    # 使用 Windows Shell API 创建快捷方式
    shell_link = pythoncom.CoCreateInstance(
        shell.CLSID_ShellLink,
        None,
        pythoncom.CLSCTX_INPROC_SERVER,
        shell.IID_IShellLink,
    )

    # 设置快捷方式属性
    shell_link.SetPath(python_exe)
    shell_link.SetArguments(str(MAIN_PY))
    shell_link.SetWorkingDirectory(str(PROJECT_DIR))
    shell_link.SetDescription("泰坦知识库 - ShineHeKnowledge")
    shell_link.SetIconLocation(str(ICON_PATH), 0)

    # 保存 .lnk 文件
    persist_file = shell_link.QueryInterface(pythoncom.IID_IPersistFile)
    persist_file.Save(str(SHORTCUT_PATH), True)

    print(f"快捷方式已生成: {SHORTCUT_PATH}")
    print(f"  目标: {python_exe} main.py")
    print(f"  图标: {ICON_PATH}")
    print(f"  工作目录: {PROJECT_DIR}")
    print(f"\n将 {SHORTCUT_NAME} 复制到桌面即可使用。")


if __name__ == "__main__":
    create_shortcut()
