"""构建 Windows 安装包

用法: python scripts/build_windows.py
需要: pip install pyinstaller
可选: 安装 Inno Setup 以生成安装向导 exe

步骤:
1. PyInstaller 打包为单文件 exe
2. (可选) Inno Setup 打包为安装向导
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.version import APP_NAME, VERSION

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def run(cmd, cwd=None):
    print(f"> {cmd}")
    subprocess.run(cmd, shell=True, cwd=cwd or ROOT, check=True)


def build_pyinstaller():
    print(f"\n{'='*50}")
    print(f"[1/2] PyInstaller 打包 {APP_NAME} v{VERSION}")
    print(f"{'='*50}")
    spec_file = os.path.join(ROOT, "shinehe.spec")
    run(f'pyinstaller --clean --noconfirm "{spec_file}"')
    exe_path = os.path.join(ROOT, "dist", "ShineHeKnowledge.exe")
    if os.path.exists(exe_path):
        size_mb = os.path.getsize(exe_path) / 1024 / 1024
        print(f"[OK] 已生成: {exe_path} ({size_mb:.1f} MB)")
    else:
        print("[ERROR] exe 生成失败")
        sys.exit(1)


def build_inno_setup():
    print(f"\n{'='*50}")
    print("[2/2] Inno Setup 打包安装向导")
    print(f"{'='*50}")
    # 查找 Inno Setup
    iscc_paths = [
        r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        r"C:\Program Files\Inno Setup 6\ISCC.exe",
    ]
    iscc = None
    for p in iscc_paths:
        if os.path.exists(p):
            iscc = p
            break
    if not iscc:
        print("[SKIP] 未找到 Inno Setup，跳过安装向导打包。")
        print("       安装 Inno Setup 6: https://jrsoftware.org/isdl.php")
        print("       然后运行: ISCC installer\\setup.iss")
        return
    iss_file = os.path.join(ROOT, "installer", "setup.iss")
    run(f'"{iscc}" "{iss_file}"')
    output = os.path.join(ROOT, "dist", f"{APP_NAME}_v{VERSION}_Setup.exe")
    if os.path.exists(output):
        size_mb = os.path.getsize(output) / 1024 / 1024
        print(f"[OK] 安装包已生成: {output} ({size_mb:.1f} MB)")
    else:
        print("[WARN] 安装包未找到，请检查 Inno Setup 输出")


def main():
    os.makedirs(os.path.join(ROOT, "dist"), exist_ok=True)
    build_pyinstaller()
    build_inno_setup()
    print("\n[DONE] 构建完成! 输出目录: dist/")


if __name__ == "__main__":
    main()
