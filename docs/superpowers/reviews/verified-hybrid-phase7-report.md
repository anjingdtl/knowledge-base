# Verified Hybrid 收束纠偏 Phase 7 报告

> 日期：2026-07-14
> 状态：本地门禁通过；远端 Matrix 与 Docker 门禁待最终 push 触发
> Spec：收束纠偏设计 §10 / 执行计划 Phase 7

## 交付

| 项目 | 结果 |
|---|---|
| Ruff | `python -m ruff check src tests evals tools scripts` 通过 |
| mypy | `python -m mypy src tools --ignore-missing-imports`，208 个源文件零错误 |
| Python Matrix | CI `test` job 覆盖 3.10 / 3.11 / 3.12，均为 required（无 allow-failure） |
| Docker | CI 构建 API / MCP target，并在 API target 上执行 `/api/health` smoke |
| Windows | 新增隔离的 `scripts/windows-smoke.ps1` 及其 pytest 契约测试；本机实际执行通过 |

## Windows 实跑证据

`powershell -ExecutionPolicy Bypass -File scripts/windows-smoke.ps1 -RepoRoot $PWD -PythonExe python`

- 在 `%TEMP%` 创建独立 `SHINEHE_HOME`；不读取、写入或删除仓库的 `config.yaml`、`data`、`raw`、`wiki`。
- `shinehe --help`、`shinehe init --local`、fixture `shinehe index --dry-run` 均返回成功。
- 真实 MCP streamable HTTP 生命周期完成：initialize 后实际调用 `kb_capabilities`、`search`、`ask`、`read`、`ping` 均成功。
- finally 块停止 MCP 子进程并删除临时工作区。

## 本阶段修复

`PathIndexService()` 的 CLI 默认构造此前把 `Database` 类型本身传给 `IndexedFileRepository`，使 `shinehe index --dry-run` 在首次调用 `get_conn()` 时崩溃。现在它确保绑定活动数据库实例；新增回归测试覆盖该默认构造。

## 验证

```text
python -m pytest tests/test_path_indexer.py tests/test_windows_smoke_contract.py tests/test_cli.py -q
44 passed

python -m ruff check src tests evals tools scripts
All checks passed

python -m mypy src tools --ignore-missing-imports
Success: no issues found in 208 source files
```

本机未安装 Docker CLI，故 Docker 构建和容器 health smoke 没有被伪报为本地通过；它们已作为 GitHub Actions 的 required `docker` job，待最终 push 后取得真实远端证据。

## 回滚

`git revert <phase7-sha>` 可移除 CI 与 Windows smoke 门禁；路径索引默认构造恢复前，CLI 直接索引将再次存在实例化风险。
