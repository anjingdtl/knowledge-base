# v1.10.2 一次性最终收尾验收报告

> **执行依据：** `docs/superpowers/specs/06-one-shot-final-closure-spec.md`  
> **日期：** 2026-07-14  
> **基线：** v1.10.1（`245e810`）

## 基线

- Base SHA：`245e810`（release: v1.10.1 final migration governance closure）
- Branch：`master`
- Working tree：干净（仅 spec 文件 `06-one-shot-final-closure-spec.md` 未跟踪）
- Alembic head：`j004_runtime_schema_parity`（单一 head，未变）
- 基线全量 pytest：1861 passed, 2 skipped（无未知失败）

## 修改

- **FIX-1**：`kb_capabilities.hidden_by_policy` 返回真实 `RegistrationState.hidden_by_policy`（`5490b6c`）
- **FIX-2**：Wiki 契约进入独立 Contract Gate（`296304f`）
- **FIX-3**：`ruff check .` 覆盖全仓库含 Alembic（`296304f`）
- **FIX-4**：前后端版本元数据统一 1.10.2（`e80e5aa`）
- **FIX-5**：发布与远端 CI 闭环（本提交）

## 验收期根因修复

- `tools/run_retrieval_shadow_eval.py`：`summary` dict 补 `dict[str, Any]` 注解，消除 mypy 2.1.0 下 11 个 operator/arg-type 错误（`777cb0a`，v1.10.1 基线既有退化，运行时行为不变）。
- `scripts/windows-smoke.ps1`：MCP 启动等待 30→60s 并在失败时 dump MCP 日志；init 后新增 `alembic upgrade head`，修复 `index --dry-run` 经 `Database.connect()` legacy constructor 建 `_SCHEMA` 表不 stamp、导致 MCP 启动被 migration gate 拦截的根因（`a70b19f` / `feaadcd`）。
- `tests/test_wiki_rebuild_scheduler.py`：`test_distinct_kids_not_merged` 改用长 debounce，消除 `Timer(0)` 异步 flush 与 `pending_count` 断言的时序竞争（`5870386`，v1.10.1 既有 flaky）。

## 明确未修改

- Retrieval：未触碰（算法 / 排序 / 召回 / RRF / Rerank）
- Wiki：未触碰（Claim / Evidence / Serving Gate）
- Database Schema：未触碰（无 Alembic Revision）
- Container：未触碰（Provider 结构不变）
- Public MCP Contract：未触碰（工具名 / 参数 / 公开返回结构）

## 验收

| 门禁 | 命令 | 结果 |
|------|------|------|
| Debt Strict | `python tools/report_closure_debt.py --strict` | No residual debt |
| Ruff | `ruff check .` | All checks passed |
| MyPy | `python -m mypy src tools --ignore-missing-imports` | Success, 0 issues in 270 files |
| Full pytest | `pytest tests/ -q` | 1878 passed, 2 skipped |
| Contract | `pytest tests/test_public_search_contract.py tests/test_public_ask_contract.py tests/test_wiki_serving_contract.py tests/test_mcp_contract.py` | 70 passed, 1 skipped |
| Migration | `pytest tests/migrations/ tests/test_alembic_baseline.py tests/storage/` | 62 passed |
| Retrieval Eval | `python evals/run_retrieval_eval.py --all --fake-embedding --baseline evals/baselines/local.json --max-regression 0.05` | PASS（diff=0.0000） |
| Hybrid Eval | `python evals/run_hybrid_eval.py --strict` | PASS（175 cases，raw/wiki/hybrid=1.000） |
| Frontend Build | `cd client && npm ci && npm run build` | client@1.10.2，44 modules，810ms |
| Version Consistency | `pytest tests/architecture/test_version_consistency.py` | 6 passed |
| Architecture | `pytest tests/architecture/ -q` | 30 passed |
| Remote CI | GitHub Actions run `29347919152` | success — 全部 11 个 job 绿（2026-07-14） |

skipped 说明：2 个 skip 为基线既有的环境条件 skip（`tests/test_mcp_contract.py:129` FastMCP 注册表暴露），非本轮引入、非隐藏失败。

## 发布

- Release Commit：`release: v1.10.2 one-shot final closure`（`758e802`）+ 验收期 CI 根因修复（`777cb0a` / `a70b19f` / `feaadcd` / `5870386`）
- Tag：`v1.10.2`
- GitHub Release：引用 `docs/release/v1.10.2-release-notes.md`

## v2.0 技术债登记

见 `docs/architecture/v1-maintainability-freeze.md` 第 4 节，共 9 项：

1. `Database._instance` 兼容入口
2. `get_active_container()` 兼容入口
3. `execute_primary_legacy` shim
4. legacy MCP aliases
5. `src/mcp_server.py` 兼容层 re-export
6. `SearchService` 私有 retrieval helpers
7. MCP 残余直接 SQL
8. Wiki Projection Provider 归属
9. MCP 大型域文件进一步拆分

这些项目不得再被描述为 v1.10.2 "未完成"。

## 最终结论

- **是否关闭 v1.x 可维护性专项：YES**
- **原因：** Spec §12 硬停止条件全部满足——`hidden_by_policy` 返回真实值、Wiki 契约进入独立 CI Gate、Ruff 覆盖整个仓库、前后端版本元数据一致、Architecture strict 通过、全量 pytest 通过、Ruff 通过、MyPy 通过、Migration 测试通过、Search/Ask/Wiki/MCP 契约通过、Retrieval Eval 通过、Hybrid Eval 通过、Frontend Build 通过、v1.10.2 Tag 和 Release 完成、v1.x 架构冻结文档完成。剩余技术债已登记转入 v2.0，不再作为 v1.x 竣工条件。

> 不得再发起新的 v1.x 可维护性专项复核，不得因为存在 v2.0 技术债而判定当前项目未竣工。
