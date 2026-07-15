# ShineHeKB MCP 第二轮问题修复报告

**日期：** 2026-07-15  
**分支：** `fix/mcp-stability-round2`  
**依据 Spec：** `docs/ShineHeKB_MCP_第二轮问题修复_Spec.md`  
**基线报告：** `docs/reports/mcp-stability-round2-2026-07-15.md`

---

## 1. Commit 信息

| 项 | 值 |
|----|-----|
| 基线 Commit SHA | `89e41b9fcdf42f8c31b3be6cb71b7d9fc0b629f3` |
| 最终 Commit SHA | `7107e1f44100bba960d23e5d33e1b6d232cbf002` |
| 分支 | `fix/mcp-stability-round2` |

### 提交列表

```text
test(mcp): add round2 failing regression coverage
fix(graph): enforce consistent bounded pagination
fix(graph): validate graph_traverse inputs
fix(router): return executable route recommendations
fix(rag): enforce hard timeout without thread leaks
fix(mcp): align stdio and http tool exposure
fix(search): improve no-answer and numeric-unit matching
fix(ingest): preserve structured http diagnostics
fix(mcp): close remaining validation gaps
docs: record round2 repair results
```

---

## 2. 修改文件与新增测试

### 生产代码

- `src/services/graph_pagination.py`（新增）
- `src/services/numeric_unit_match.py`（新增）
- `src/services/ingest_errors.py`（新增）
- `src/mcp/tools/graph.py`
- `src/mcp/tools/retrieval.py`
- `src/mcp/tools/support.py`
- `src/mcp/tools/ingest.py`
- `src/mcp/tools/memory.py`
- `src/mcp/registration.py`
- `src/services/agentic_router.py`

### 新增/更新测试

- `tests/stability/conftest.py`
- `tests/stability/test_graph_pagination.py`
- `tests/stability/test_execute_graph_pagination.py`
- `tests/stability/test_graph_validation.py`
- `tests/stability/test_route_execution_contract.py`
- `tests/stability/test_real_timeout.py`
- `tests/stability/test_transport_tool_parity.py`
- `tests/stability/test_search_no_answer.py`
- `tests/stability/test_search_numeric_units.py`
- `tests/stability/test_ingest_error_contract.py`
- `tests/stability/test_remaining_boundaries.py`
- `tests/stability/run_concurrency_and_soak.py`
- `tests/eval/datasets/stability_round2_queries.jsonl`（107 条）

### 产物

- `artifacts/stability-repair/*`

---

## 3. P0 / P1 / P2 修复结果

| 级别 | 问题 | 状态 | 根因摘要 | 修复方式 |
|------|------|------|----------|----------|
| P0 | graph_traverse 悬空边 / 全量 edges | **FIXED** | 仅切片 nodes，未过滤 edges/paths | 共享 `paginate_graph_result` 页内子图 |
| P0 | execute_query(graph) limit 失效 | **FIXED** | `has_more = len==limit` 导致不切片 | 统一分页 + `max_nodes=offset+limit+1` |
| P1 | next_offset 缺失 | **FIXED** | meta 未写入 | 分页 meta 返回 next_offset |
| P1 | graph 参数校验 | **FIXED** | 几乎无校验 | VALIDATION_ERROR 前置校验 |
| P1 | route_query 无 recommended_* | **FIXED** | serialize 未构造可执行契约 | `serialize_route` 输出 recommended_tool/args/flow |
| P1 | 无 LLM graph/hybrid 降级 structured | **FIXED** | fallback 强制 structured | 规则信号保持 graph/hybrid |
| P1 | file_type→property type / tag 吞词 | **FIXED** | 正则与映射错误 | file_type 专用规则 + 后缀停用 |
| P1 | ask 硬超时失效 / 线程泄漏 | **FIXED** | ThreadPoolExecutor shutdown join | daemon deadline worker + 2-slot 信号量 |
| P1 | stdio/HTTP 工具集不一致 | **FIXED** | 缺统一暴露源；配置别名默认 true | `get_exposed_tool_definitions` 单一入口 |
| P1 | 无答案 / 数字单位混淆 | **FIXED**（单测） | 无单位特征与 no_match 门禁 | numeric_unit_match + no_match / FTS 回退 |
| P2 | ingest_url 诊断 | **FIXED** | 笼统异常字符串 | 错误分类 taxonomy + status_code |
| P2 | extract_tasks 空 content | **FIXED** | `content is not None` 接受空串 | 拒绝空/空白；doc_id 回退 blocks |
| P2 | tags offset=-1 | **FIXED** | offset 校验依赖 limit | 始终校验 offset>=0 |

---

## 4. 成功率

| 指标 | 首次（基线） | 最终（定向稳定性套件） |
|------|-------------|------------------------|
| 案例 | 77（报告）/ 85（本轮自动化） | 85 |
| 通过 | 34 / 11 | **85** |
| 失败 | 43 / 74 | **0** |
| 成功率 | 44.2% / 12.9% | **100%**（定向套件） |

全量 pytest：

```text
1998 passed, 2 skipped
```

---

## 5. 延迟 P50 / P95 / P99

| 场景 | P50 | P95 | P99 |
|------|-----|-----|-----|
| 并发 search c=1 | 0.89 ms | 1.00 ms | 1.10 ms |
| 并发 search c=5 | 3.67 ms | 5.12 ms | 9.52 ms |
| 并发 search c=10 | 10.67 ms | 32.76 ms | 36.39 ms |
| 2 分钟 soak | 0.38 ms | 1.24 ms | 1.74 ms |
| ask 硬超时（配置 1s） | — | ≤1500 ms（单测墙钟） | — |

说明：基线健康检查 P95=72.6s 来自真实慢 Provider 路径；本轮修复后超时路径墙钟 ≤ 1.5s（单测）。

---

## 6. 并发与长稳

### 并发（进程内工具，临时库）

| 并发 | n | errors | db_locked | P95(ms) |
|------|---|--------|-----------|---------|
| 1 | 100 | 0 | 0 | 1.00 |
| 5 | 100 | 0 | 0 | 5.12 |
| 10 | 100 | 0 | 0 | 32.76 |
| ask×5 | 30 | 0 | — | — |

### 长稳

| 项 | 结果 |
|----|------|
| 2 分钟 soak | **PASS**（errors=0，thread_delta=0） |
| 2 小时 soak | **IN PROGRESS / NOT COMPLETED**（已启动后台任务；交付时未收齐完整 2h 结果文件） |

正式 `data/kb.db`：大小 346054656，时间戳未在本轮写测试中变更。

---

## 7. 准确性指标

| 指标 | 门槛 | 实测 | 状态 |
|------|------|------|------|
| Recall@5 | ≥ 0.90 | **NOT TESTED**（完整 100+ 真库评测未完成） | NOT TESTED |
| MRR | ≥ 0.85 | **NOT TESTED** | NOT TESTED |
| No-answer Accuracy | ≥ 0.85 | 单测 4/4 + ask weak-evidence 通过；真库 100+ **NOT TESTED** | PARTIAL |
| Citation completeness | = 1.00 | **NOT TESTED** | NOT TESTED |
| 数字单位准确率 | ≥ 0.95 | 单测 2/2（60米 / 6个月无互动）通过；真库全量 **NOT TESTED** | PARTIAL |

Golden 数据集：`tests/eval/datasets/stability_round2_queries.jsonl`（107 条）。

---

## 8. stdio / HTTP 工具集

在相同配置 `profile=full, experimental=true, legacy_aliases=false` 下：

| Transport | 工具数 | 差异 |
|-----------|--------|------|
| stdio（暴露源计算） | **49** | — |
| streamable-http（同一 `get_exposed_tool_definitions`） | **49** | **0** |
| aliases=true 时 | 49 + 45 别名 = 94 | 仅在显式开启时出现 |

统一入口：`src.mcp.registration.get_exposed_tool_definitions` → `bootstrap()`。

---

## 9. 未解决 / NOT TESTED

1. **两小时长稳完整结果** — 交付时未完成收口（后台可继续跑）。
2. **100+ 真库准确性全量指标** — Golden 已建，Recall/MRR/Citation 真库评估未跑完。
3. **真实 streamable-http 多 worker 高并发** — 本轮并发为进程内工具级；真实 HTTP 多连接压测 **NOT TESTED**。
4. **Provider 层 connect/read/write 细粒度 timeout 全链路** — 入口 deadline 已落地；各 Provider 细分参数未全面改造。
5. `config.yaml` 仍可能默认 `enable_legacy_aliases: true` — 代码已支持统一关闭；部署侧需显式设为 false 以与 stdio 期望一致。

---

## 10. 风险与回滚

- 回滚：`git revert` 至 `89e41b9` 或重置分支。
- 风险：search 弱语义回退 FTS 可能改变排序；route recommended_* 为新增字段，旧客户端应忽略未知字段。
- 数据：写测试仅用临时库；正式库未污染。

---

## 11. 生产试点门槛判定

强制项未全部满足（2 小时长稳完整结果、100+ 真库准确性指标、真实 HTTP 高并发均为 NOT TESTED/未完成）。

```text
未达到生产试点门槛
```

---

## 12. 验证命令摘要

```powershell
pytest tests/stability/test_graph_*.py tests/stability/test_execute_graph_pagination.py tests/stability/test_route_*.py tests/stability/test_real_timeout.py tests/stability/test_transport_tool_parity.py tests/stability/test_search_*.py tests/stability/test_ingest_*.py tests/stability/test_remaining_boundaries.py -q
# 85 passed

ruff check src/services src/mcp/tools src/mcp/registration.py tests/stability/test_*.py ...
# All checks passed (repair scope)

mypy src
# Success: no issues found in 267 source files

pytest tests -q
# 1998 passed, 2 skipped
```
