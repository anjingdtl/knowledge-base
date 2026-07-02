#!/usr/bin/env bash
# 安装 ShineHeKnowledge 的 git hooks（gitleaks 密钥扫描）
# 用法: bash scripts/install-hooks.sh
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

git config core.hooksPath .githooks
echo "✅ core.hooksPath 已设置为 .githooks（pre-commit 密钥扫描已激活）"

# 验证 gitleaks 可用
if command -v gitleaks >/dev/null 2>&1; then
    echo "✅ gitleaks 已在 PATH: $(command -v gitleaks)"
elif [ -x "/d/ClaudeCodeWorkSpace/projects/bin/gitleaks.exe" ]; then
    echo "✅ gitleaks 已安装: /d/ClaudeCodeWorkSpace/projects/bin/gitleaks.exe"
else
    echo "⚠️  未找到 gitleaks。pre-commit hook 会跳过扫描直到安装 gitleaks。"
    echo "   下载: https://github.com/gitleaks/gitleaks/releases（选 windows_x64.zip）"
fi
