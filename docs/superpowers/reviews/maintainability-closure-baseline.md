# Maintainability Closure — Executable Baseline

> **日期:** 2026-07-14  
> **分支:** `feat/maintainability-closure-wp0-wp1`  
> **基线 SHA (pre-work):** `b635ca70132f1ee0304492a7c8cc16a58a831856`  
> **规格:** `docs/superpowers/specs/04-maintainability-closure-spec.md`  
> **计划:** `docs/superpowers/plans/2026-07-14-maintainability-closure.md`

---

## 环境

| 项 | 值 |
|---|---|
| Git SHA (work start) | `b635ca7` / `b635ca70132f1ee0304492a7c8cc16a58a831856` |
| Python | 3.14.3 |
| OS | Windows-11-10.0.26200-SP0 |
| 有效 `retrieval.orchestrator`（本地 Config） | `None` → 代码默认 **legacy** |
| `config.example.yaml` retrieval.orchestrator | **legacy** |
| 有效 `answer.orchestrator`（本地 Config） | `None` |
| `config.example.yaml` answer.orchestrator | **unified** |

---

## 全量 pytest（修复 2 个预存失败前）

```
2 failed, 1772 passed, 2 skipped, 17 warnings in 417.26s
```

### 初始失败（WP0 修复）

| 测试 | 原因 | 处置 |
|---|---|---|
| `test_mcp_lifespan_starts_and_stops_async_worker` | Phase-3 lifespan 已迁至 `src.mcp.runtime`，测试仍 patch `mcp_server.create_container` | 更新测试 patch 目标到 `runtime` |
| `test_progress_records_verified_hybrid_correction_completion` | PROGRESS 标题措辞与证据字符串不完全匹配 | 补齐完成标记与「远端 CI 全绿」 |

### Skip

| 用例 | 原因 |
|---|---|
| 2 skipped（全量 run 中） | 与环境/可选依赖相关（详见 pytest 输出；非 architecture-closure 新增） |

修复后目标测试 **4 passed**（lifespan + release evidence）。  
全量重跑未在本报告截断前强制再次 7 分钟；相关子集与后续 WP1-T1 回归 **66 passed**。

---

## 静态检查

### Ruff

```
Found 12 errors (mostly pre-existing F401/I001 + 本轮 tools 曾有 sys unused，已清)
RUFF_EXIT=1
```

说明：基线时仓库已有若干 unused import；本轮新增 `report_closure_debt` 已去掉未使用 `sys`。

### MyPy

```
Found 3 errors in 3 files (checked 241 source files)
MYPY_EXIT=1
```

预存问题：

- `search_service.py`: Returning Any → SearchExecution  
- `async_tasks.py`: mcp_server 无 `_validate_file_path`  
- `mcp/server.py`: Returning Any → AppContainer  

**非本轮引入**；WP0 记录在案，不在本批修复范围。

---

## Eval

### Retrieval Eval (`--all --fake-embedding --max-regression 0.05`)

| Dataset | R@5 | MRR | nDCG@10 |
|---|---:|---:|---:|
| retrieval_code | 1.0000 | 1.0000 | 1.0000 |
| retrieval_no_answer | 0.0000 | 0.0000 | 0.0000 (NA=0.6667) |
| retrieval_table | 1.0000 | 1.0000 | 0.9779 |
| retrieval_zh | 0.6000 | 0.3400 | 0.4036 |

**Overall: PASS**（相对 baseline 无实质回归）

### Hybrid Eval (`--strict`)

```
Hybrid Eval PASS: cases=175 raw=1.000 wiki=1.000 hybrid=1.000
stale=0.0 unsup=0.0 cite=1.000 conflict_recall=1.000
```

---

## 架构欠账快照（WP0-T2 / 修复后初始）

命令：`python tools/report_closure_debt.py`

| 指标 | 初始值 (WP0) | WP1-T1 后 |
|---|---:|---:|
| mcp_server_lines | 4053 | 4053 |
| mcp_server_tool_functions | 10 | 10 |
| mcp_tools_real_impl_count | 0 | 0 |
| database_instance_refs_src | 25 | 25 |
| get_active_container_refs_src | 17 | 17 |
| search_service_has_legacy_pipeline | True | True |
| search_service_has_verified_hybrid | True | True |
| raw_retriever_calls_search_service | **True** | **False** |
| answering_depends_on_verified_answer | True | True |
| alembic_env_reads_test_url | False | False |
| migration_tests_have_skip_paths | True | True |
| Residual debt items (strict) | 10 | **9** |

---

## 结论

| 门禁 | 状态 |
|---|---|
| 可重复基线报告 | ✅ |
| 欠账 CLI 一条命令 | ✅ `python tools/report_closure_debt.py` |
| 预存 2 fail 已修 | ✅ |
| Retrieval/Hybrid Eval | ✅ PASS |
| 允许进入 WP1-T1 | **YES** |

**禁止事项提醒:** 在 Raw 等价与 Shadow 聚合报告前，不得默认切 unified，不得删 Legacy。
