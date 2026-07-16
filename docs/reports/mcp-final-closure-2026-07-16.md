# ShineHeKB MCP 最终收口报告

**日期：** 2026-07-16  
**分支：** `fix/mcp-final-closure`  
**依据 Spec：** `docs/superpowers/specs/ShineHeKB_MCP_最终收口与本地真实MCP验证_Spec.md`

---

## 1. Commit 信息

| 项 | 值 |
|----|-----|
| 基线 Commit SHA | `02f71b0036703f1c36174b11ef2e7036341436f6` |
| 最终 Commit SHA | 见本分支 `HEAD`（含本报告提交） |
| 分支 | `fix/mcp-final-closure` |

### 提交列表

```text
chore(final-closure): record Phase 0 baseline artifacts
fix(mcp): deadline cancel, graph hard-limit, structured page, FTS gate
fix(mcp): final-closure harness, CI branches, golden enrich, router timeout
fix(mcp): tune relevance gate and Windows subprocess UTF-8 decoding
docs(final-closure): soak results, full regression, final report
```

---

## 2. 每个问题根因与修复

| 问题 | 根因 | 修复 |
|------|------|------|
| Timeout 假取消 / slot 占用 | daemon 线程 + semaphore 持有到 worker 结束；`cancelled=true` 不诚实 | `src/services/deadline.py`：cancel event、诚实 `cancelled`/`background_work_may_continue`、超时后释放请求路径；slot 不永久占用 |
| Graph `next_offset` 自循环 | `offset>=max` 空页仍 `has_more=true` 且 `next_offset==offset` | `paginate_graph_result` 硬上限终止 + `hard_limit_reached`；禁止 `next_offset<=offset` |
| Structured 分页不准 | `has_more = len==limit` | `structured_pagination` 多取一条；两入口共用 |
| FTS 回退绕过 no-answer | fulltext 有命中即当有效证据 | `relevance_gate` 统一评分；当前信息查询短路；ask 证据门禁 |
| route_query LLM 挂死 | `_try_llm` 无 timeout | 注入 `rag.route_llm_timeout` |
| Windows 子进程 UTF-8 | wmic/sc 系统码页字节用 utf-8 硬解 | `_run_hidden` `errors=replace` |

---

## 3. 单元 / 定向测试

新增/强化：

- `tests/stability/test_provider_deadline_propagation.py`
- `tests/stability/test_real_cancellation.py`
- `tests/stability/test_timeout_slot_recovery.py`
- `tests/stability/test_graph_hard_limit.py`
- `tests/stability/test_graph_pagination_monotonic.py`
- `tests/stability/test_structured_pagination.py`
- `tests/stability/test_fts_no_answer_gate.py`
- `tests/stability/test_current_information_no_answer.py`
- `tests/stability/test_pre_llm_evidence_gate.py`

定向 stability：**120 passed**

---

## 4. stdio / streamable-http 实际 MCP

### Phase 0

- 工具数：stdio=49，http=49，集合一致
- 产物：`artifacts/final-closure/phase0-mcp-probe.json`

### Phase 6

| 项 | http | stdio |
|----|------|-------|
| 工具集一致 | 是 | 是 |
| 路由原样执行成功率 | **100%** (30/30) | **100%** (30/30) |
| Graph 自动分页终止 | 是 | 是 |
| 自循环 | 否 | 否 |
| Timeout 后 ping/search | OK | OK |
| Timeout 墙钟 (配置 3s) | 3173 ms | 3162 ms |

覆盖工具：`initialize/tools/list`、`ping`、`search`、`search_fulltext`、`route_query`、`execute_query`、`graph_traverse`、`ask`、`ask_with_query`、`kb_health_check`、`tags`、`ingest_url`（SSRF 拒内网，稳定错误契约）。

---

## 5. 107 条 Golden 指标（真实 MCP）

数据集：`tests/eval/datasets/stability_round2_queries.jsonl`（已补齐 Spec 字段）

产物：`eval-stdio.jsonl` / `eval-http.jsonl` / `eval-metrics.json`

| 指标 | 结果 | 门槛 |
|------|------|------|
| Recall@5 | **0.9065** | >= 0.90 |
| MRR | **0.9065** | >= 0.85 |
| nDCG@10 | **0.9065** | >= 0.85 |
| No-answer Accuracy | **1.00** | >= 0.85 |
| False-answer Rate | **0.00** | <= 0.10 |
| Citation completeness | **1.00** | = 1.00 |
| Citation correctness | **1.00** | >= 0.95 |
| 数字单位准确率 | **1.00** | >= 0.95 |
| Transport 一致率 | **1.00** | >= 0.95 |

**PASS**

说明：正式库在部分 MCP 启动路径下曾告警 vector index empty，评估主要走 FTS + 相关性门禁；指标仍满足门槛。见未解决问题。

---

## 6. 真实 HTTP 并发

产物：`artifacts/final-closure/concurrency-http.json`  
方式：独立 FastMCP Client 多会话（非进程内直接调 Python search/ask）

### Search（每档 n=100）

| 并发 | success_rate | P50 ms | P95 ms | P99 ms |
|------|--------------|--------|--------|--------|
| 1 | 1.00 | 3085 | 3223 | 3300 |
| 5 | 1.00 | 3196 | 3465 | 3509 |
| 10 | 1.00 | 3538 | 4344 | 4367 |
| 20 | 1.00 | 5386 | 5961 | 6001 |
| 50 | 1.00 | 7079 | 11978 | 12892 |

### Ask controlled invalid LLM（每档 n=30）

| 并发 | success_rate | P50 | P95 | P99 |
|------|--------------|-----|-----|-----|
| 1 | 1.00 | 2187 | 2213 | 2222 |
| 3 | 1.00 | 2184 | 2257 | 2410 |
| 5 | 1.00 | 2193 | 2226 | 2231 |
| 10 | 1.00 | 2235 | 2290 | 2292 |

门槛：20 并发 search 成功率 100% >= 99%；5 并发 ask 无崩溃。**PASS**  
（延迟含 controlled provider 超时预算，属预期）

---

## 7. 真实 MCP 长稳

| 项 | streamable-http | stdio |
|----|-----------------|-------|
| 时长 | **7200 s** | **1800 s** |
| ops | 8402 | 2123 |
| errors | **0** | **0** |
| timeouts | 168（计划内 ask 超时场景） | 42（计划内） |
| P50 / P95 / P99 ms | 6.0 / 3131.95 / 3228.99 | 3.0 / 3106.8 / 3165.78 |
| 首 10 分钟窗口 P95 均值 vs 末段 | 3155.95 → 3111.63（**未恶化**） | 3117.2 → 3067.0（**未恶化**） |
| 线程 | 29 → 23（下降） | 采样无 pid 级线程（stdio 客户端侧） |
| 连接 | 稳定 4 | — |
| 正式 DB | 未变化 | 未变化 |

说明：

- harness 原始 `p95_degraded=true` 因首采样 ops=1、P95=19 的统计偏差；按 Spec「首 10 分钟」重算后 **未超过 50% 恶化**。
- timeouts 来自无效 LLM 下的计划性 `ask` 超时演练，**errors=0**。
- RSS 呈锯齿（约 140→629→~340 回落循环），线程/连接无持续单向上涨。

产物：`soak-http.json` / `soak-stdio.json` / `soak-resource.csv` / `soak-summary.json` / `soak-errors.jsonl`

---

## 8. Provider timeout 与取消证据

- 单元：cooperative 1s 内返回、`cancelled=true`；非协作 `cancelled=false` + `background_work_may_continue=true`
- 50 次 cooperative timeout 线程增量 <=1
- 双 hang 后第三次请求不被永久阻塞
- 真实 MCP：配置 3s 超时墙钟 ~3.1–3.2s，随后 ping/search 成功
- route LLM 硬超时：`rag.route_llm_timeout`

---

## 9. Graph / Structured

- Graph 极限 offset 190/195/199/200/205：无自循环；自动翻页可终止
- Structured：effective_limit、limit+1、两入口一致、total_estimate 不伪精确

---

## 10. 工程门禁（收口后全量回归）

| 门禁 | 结果 |
|------|------|
| `ruff check src tests` | **0 error** |
| `mypy src` | **0 error** |
| `pytest tests -q` | **2033 passed, 2 skipped** |
| PytestUnhandledThreadExceptionWarning（本轮新增路径） | **未再现**（Windows 子进程 UTF-8 已修） |
| 正式 `data/kb.db` size | **346054656 与基线一致** |

CI：workflow 已包含 `fix/**` 推送触发；最终以远程 Actions 状态为准。

---

## 11. 未解决问题

1. **非协作同步 SDK**：无法安全 kill 线程时，仅能诚实标记 `background_work_may_continue=true`；真进程级隔离仍可加强。
2. **正式库向量路径**：以 temp `SHINEHE_HOME` + 绝对 data 路径启动 MCP 时出现过 `Vector index is EMPTY` 告警，Golden 主要依赖 FTS 门禁路径通过。
3. **长稳 RSS 锯齿**：2h 内峰值 ~629MB，伴随 GC/缓存回落；非线程/连接泄漏，但值得后续做 heap 分析。
4. **Ask 真实 Provider 并发抽样**：成本控制下使用 controlled invalid LLM 全量档；真实 Provider 抽样未在本轮单独扩档。

---

## 12. NOT TESTED

- 真实付费 LLM 全量 107 条 `ask` 生成质量（仅对 no-answer 类执行了 ask；避免成本爆炸）
- 生产环境多机部署 / 反向代理后的 MCP
- 正式库向量索引在「项目根 SHINEHE_HOME」下的二次对照压测

---

## 13. 结论

**达到生产试点门槛**

依据：Timeout 诚实取消/slot 恢复、Graph 硬上限无自循环、Structured 分页正确、No-answer/FTS 门禁、107 Golden 指标达标、stdio/http 真实 MCP 与路由 100%、HTTP 并发成功率 100%、2h HTTP + 30m stdio 长稳 errors=0、工程门禁全绿、正式库未污染。
