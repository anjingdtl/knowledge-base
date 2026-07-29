# MCP Agent 知识命中准确率第五轮复测报告

> 依据：`docs/evaluation/mcp-agent-knowledge-hit-rate-remediation-spec-v5.md`  
> 唯一证据：本轮全量 MCP 重跑（`artifacts/hit_rate_test_v5/`），**未复用** v1–v4 `final_scored.json` 作为通过证明。  
> Golden Set：`evals/golden_set_hit_rate.json`（**未修改**）。  
> 前四轮 artifacts/报告只读保留（未覆盖）。

## 0. 结论（硬性）

### **不通过放行**

| 检查 | 结果 |
| --- | --- |
| Top-1 Accuracy ≥ 75% | **通过**（90.62%） |
| Recall@5 ≥ 88% | **通过**（96.88%） |
| Ask Fact Correctness ≥ 90% | **未通过**（40.62%） |
| Ask Citation Validity ≥ 95% | **未通过**（78.12%） |
| E2E Pass Rate ≥ 90% | **未通过**（40.62%） |
| Hallucination Rate ≤ 5% | **通过**（0.00%） |
| False Positive Rate ≤ 5% | **通过**（0.00%） |
| P1 缺陷 = 0 | **未通过**（19） |
| 硬验收 KB-017/018/019/021/032/037 | **通过**（本轮全部达标） |
| 全量 37 例未省略 | **通过** |
| 全局 `no_answer_threshold=0.35` 未下调 | **通过** |
| 性能目标 ≤35 min（相对 v4≈73 min 降 ≥50%） | **未通过**（全量 workers=1 约 **41.8 min**；相对 v4 降约 **42.8%**） |

**不得**描述为“有条件放行”或“完成放行”。

---

## 1. 测试配置与时间

| 项 | 值 |
| --- | --- |
| MCP | streamable-http `127.0.0.1:9000` |
| 版本 | 1.11.1 |
| Golden | 37 例全量，`skipped_resume=0` |
| harness 参数 | `--reuse-snapshot --read-mode unique --workers 1` |
| harness 总耗时 | **2505.0 s ≈ 41.8 min**（见 `summary.json` / `harness_console.txt`） |
| snapshot reuse | **30/37** 命中；`retrieval_count_sum=6` |
| search_ms_sum | 2,086,715.67 ms |
| ask_ms_sum | 416,963.63 ms（reuse 后多数 ask 仅数十 ms） |
| unique reads | 25 个 knowledge_id（`unique_reads.json`） |
| workers=1 8 例基准 | 321.3 s（`workers1_bench/`） |
| workers=2 8 例基准 | 201.8 s（`workers2_bench/`） |
| 全量 pytest 基线 | 23 failed / 2219 passed（`pytest_baseline.txt`） |
| 全量 pytest 最终 | 22 failed / 2220 passed / 2 skipped，exit 1，~966 s（`pytest_final.txt`） |

---

## 2. 本轮实现映射（SPEC v5）

| 阶段 | 实现 | 状态 |
| --- | --- | --- |
| 1 | v4 原始 passage 确定性回放（Tier 1） | **已交付** `tests/mcp/test_hit_rate_v5_replay.py` |
| 2 | passage 邻接 fail-closed；passage 路径不加载整页 blocks | **已交付** `context_builder.expand_adjacent_evidence` + `canonical_snapshot` |
| 3 | evidence metadata / `body_text` 分离 | **已交付** `passage_evidence.split_metadata_and_body` |
| 4 | LogicalEvidenceRecord + FactCandidate + query planner | **已交付** `logical_evidence.py` / `fact_candidates.py` / `query_planner.py` |
| 5 | 删除无条件数值先行控制流 | **已交付** `claim_protocol.rule_extract_claims` 改由 planner 驱动 |
| 6 | 表格同行绑定；歧义表拒答而非全文 fallback | **部分达标**（KB-017/018/019 过；仍有 OCR 表类 case 失败） |
| 7 | 校验 FactCandidate span/模板渲染 + answer_plan | **已交付** |
| 8 | 精确失败原因；MCP 不覆盖 validation reason | **已交付**（仍有检索门禁路径使用 `insufficient_relevant_evidence`） |
| 9 | search snapshot ID + ask 复用指纹 | **已交付** `snapshot_registry.py` + MCP `evidence_snapshot_id` |
| 10 | harness `--reuse-snapshot/--read-mode/--workers/--manifest/--resume` | **已交付** |

**禁止项遵守**：未改 Golden、未降 0.35、未覆盖前四轮 artifacts/报告、未做 case_id 硬编码答案、未少跑 case。

---

## 3. v1–v5 指标对比

数据：`artifacts/hit_rate_test_v5/metrics_comparison.txt`、`final_scored.json`。

| 指标 | R1 | R2 | R3 | R4 | **R5** | 门禁 | R5 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| Top-1 | 84.38% | 84.38% | 87.50% | 90.62% | **90.62%** | ≥75% | 通过 |
| Recall@5 | 87.50% | 87.50% | 93.75% | 96.88% | **96.88%** | ≥88% | 通过 |
| Ask Fact | n/a | 34.38% | 62.50% | 18.75% | **40.62%** | ≥90% | **未通过** |
| Ask Citation | n/a | 93.75% | 84.38% | 68.75% | **78.12%** | ≥95% | **未通过** |
| E2E | n/a | 31.25% | 62.50% | 18.75% | **40.62%** | ≥90% | **未通过** |
| Hallucination | 6.25% | 6.25% | 12.50% | 6.25% | **0.00%** | ≤5% | 通过 |
| FP | 0% | 0% | 20% | 0% | **0%** | ≤5% | 通过 |
| P1 | 3 | 20 | 13 | 26 | **19** | 0 | **未通过** |

### 解读

- **检索指标维持 R4 高位**（Top-1/Recall 不变），passage 索引未回退。  
- **Ask Fact / E2E 相对 R4 明显回升**（18.75% → 40.62%），主要来自 FactCandidate 取消“数值先行污染政策答案”以及表格条件绑定修复。  
- **相对 R3（62.5%）仍有缺口**：多事实覆盖、跨文档职责/关系问法、部分金额类 case 仍失败。  
- **Hallucination=0、FP=0**：严格 no-answer 合同与校验渲染生效。  
- **硬验收表格三例（017/018/019）本轮全部 E2E 通过**，消除 R4 的条件—数值串配阻塞点。

### E2E 通过列表（13）

`KB-003, KB-005, KB-008, KB-016, KB-017, KB-018, KB-019, KB-021, KB-024, KB-026, KB-029, KB-035, KB-037`

### P1（19）

`KB-001, KB-002, KB-004, KB-006, KB-007, KB-009, KB-010, KB-011, KB-012, KB-013, KB-014, KB-015, KB-020, KB-022, KB-023, KB-025, KB-027, KB-028, KB-036`

---

## 4. 硬验收与表格行级审计

| 用例 | 要求 | 实测 | 判定 |
| --- | --- | --- | --- |
| KB-017 | 2000元 + 一个自然月；禁 30元 | answer 含 `2000元` 与 `一个自然月`/`涉诈`；无 30 | **E2E 通过** |
| KB-018 | 30元 + 涉骚扰；禁 2000元 | answer 含 `30元` 与 `涉骚扰`；无 2000 | **E2E 通过** |
| KB-019 | III类 + 20万元；禁 10万元 | answer=`- III类支付账户，其余额年付款限额为20万元（不含提现）。` | **E2E 通过** |
| KB-021 | 1个工作日 + 5个工作日 | 双 deadline 覆盖 | **E2E 通过** |
| KB-032 | 空 answer/sources | `answer=""`，FP=false | **通过** |
| KB-037 | 2026 + 158号；禁一级/二级竞赛 | answer 含 2026/158；无禁用短语 | **E2E 通过** |

### KB-017 / 018 / 019 condition→value→unit / span

| case | condition | value | unit | record_id / passage_id | exact_text（摘要） |
| --- | --- | --- | --- | --- | --- |
| KB-017 | 涉诈 | 2000 | 元 | `83fb103c…:r4` / `83fb103c…` | `一个自然月内涉诈号码每个号码-处罚2000元/个…` |
| KB-018 | 涉骚扰 | 30 | 元 | `f9159242…:r3` / `f9159242…` | `…涉骚扰号码每个号码-处罚30元/个…`（邻接 passage 扩展命中） |
| KB-019 | III类 | 20 | 万元 | `3c7a0856…:r3` / `3c7a0856…` | `III类支付账户，其余额年付款限额为20万元（不含提现）。` |

完整 `numeric_fact_audit` / `claims_used` 见各 case 原始 JSON。

---

## 5. FactCandidate / Logical Evidence Record

### 字段（内存层，不重建检索索引）

**LogicalEvidenceRecord**：`record_id, passage_id, knowledge_id, type(paragraph|list_item|table_row|unstructured_table), body_text, source_span, table_id/row_index, unstructured_table, family/version/section`

**FactCandidate**：`candidate_id, record_id, passage_id, knowledge_id, fact_kind(policy|prohibition|responsibility|scope|relationship|numeric|deadline|version), condition/value/unit, exact_text, evidence_spans, table_row_ref, score`

**QueryPlan**：意图（policy/numeric/deadline/version/…）→ `allow_fact_kinds`；非数值 query **不**默认抽 numeric。

### 关键规则

1. 仅 `body_text` 参与事实/数值抽取；`【文档】/【章节】` 前缀剥离可审计。  
2. OCR soft-join 后再分句，避免条件与数值跨行断裂。  
3. 数值绑定窗口为 value 附近 ±40 字（含后置条件，如“30 元…涉骚扰”）。  
4. 禁止整段 passage 作为 fallback clause（修复 R4 串配根因）。  
5. 歧义扁平表可 `unstructured_table=true` 并 `table_structure_ambiguous` 拒答。

---

## 6. retrieval_decision 与 answer_validation_decision 分离

全量 37 例统计（原始 ask 字段）：

| retrieval_decision | 计数 |
| --- | ---: |
| accepted | 30 |
| insufficient_relevant_evidence | 6 |
| requires_current_external_data | 1 |

| answer_validation_decision / reason | 计数 |
| --- | ---: |
| structured_claim_answer | 25 |
| insufficient_relevant_evidence | 6 |
| direct_slot_not_satisfied | 3 |
| answer_plan_incomplete | 1 |
| passage_trace_failed | 1 |
| requires_current_external_data | 1 |

说明：

- 当检索 gate 拒绝时，外层仍可能给出 `insufficient_relevant_evidence`（检索决策本身）。  
- 当检索已 accept 但回答层失败时，保留 `direct_slot_not_satisfied` / `no_fact_candidate` / `answer_plan_incomplete` / `passage_trace_failed` 等，**不再统一改写成泛化 evidence gate**（对比 R4 的 KB-012/022 类覆盖问题）。  
- 本轮仍有部分跨文档关系类 case 在“检索 accept + 答案事实不全”路径上记入 P1，而非伪报 gate。

`adjacent_count`：max=10，mean≈3.5；passage 模式单位为 `passage`（邻接 passage_index±1），非整页 741 blocks。

---

## 7. 测试加速与正确性

| 项 | v4 | v5 | 说明 |
| --- | ---: | ---: | --- |
| 全量 37 例 | ~4382 s / ~73 min | **2505 s / ~41.8 min** | 降约 **42.8%** |
| 目标 | — | ≤35 min 且降≥50% | **性能验收未通过** |
| ask 重复检索 | 每例 search+ask 双检索 | reuse 后 ask 多在 10–50 ms | `snapshot_reuse_hits=30/37` |
| read | 每例 top-1 | unique 去重 25 次 | 不参与评分 |
| workers=1 8 例 | — | 321.3 s | 稳定 |
| workers=2 8 例 | — | 201.8 s | 无锁死/超时；wall-clock 更快 |

### 正确性等价（workers=1 vs workers=2，同一 8 例）

| 维度 | 结果 |
| --- | --- |
| top_candidate_id / answer_mode / answer 文本 | **8/8 一致** |
| no-answer 合同（KB-032 空 answer） | **一致** |
| reason 字符串 | **KB-032 不一致**（`passage_trace_failed` vs `direct_slot_not_satisfied`） |

按 SPEC：存在任何结果不一致则**不以 workers=2 作为放行执行默认**。本轮 **全量 37 例以 workers=1 结果为准**；workers=2 仅作加速可行性报告。未观察到 SQLite 锁冲突或超时。

主耗时仍在 **search/rerank**（日志大量 `Rerank timed out`）；snapshot 复用显著压缩 ask，但无法单独把总时长压到 35 分钟门禁以下。

---

## 8. 自动化测试（pytest）

### 定向（Tier 0/1 相关）

```text
pytest tests/answering/test_hit_rate_v5_fact_pipeline.py \
       tests/mcp/test_hit_rate_v5_replay.py \
       tests/mcp/test_hit_rate_v4_answer_contract.py -q
```

**35 passed**。

### 全量

```text
pytest tests/ -q --ignore=tests/stability/test_provider_worker_pid_terminated.py
```

| 阶段 | 结果 |
| --- | --- |
| 改动过程中基线 | 23 failed / 2219 passed（`pytest_baseline.txt`） |
| 最终 | **22 failed / 2220 passed / 2 skipped**，exit 1，~966 s（`pytest_final.txt` / `pytest_final_meta.txt`） |

**本轮相关修复清零：**

- `tests/test_50round_bugfix.py::TestAskTimeoutControl::test_do_ask_returns_partial_result_on_timeout`（基线失败 → 最终通过）  
- `tests/test_upgrade_regression_review.py::test_do_ask_catches_non_timeout_exception`（基线失败 → 最终通过）  

**剩余 22 失败归类（非本轮 answering 契约新增）：**

- 版本号一致性（`test_version_consistency` 等）  
- eval routing / asyncio mark  
- wiki_read stage / size_aware  
- `test_public_search_contract` snapshot  
- `test_mcp_gui_status`（v4 全量亦曾失败；本基线偶发）  
- architecture debt baseline  

不得用“定向 35 passed”替代全量验收。

---

## 9. 交付物

| 路径 | 内容 |
| --- | --- |
| `artifacts/hit_rate_test_v5/` | 37 例原始交互、`00_capabilities.json`、`final_scored.json`、`summary.json`、`metrics_comparison.txt`、`manifest.json`、`unique_reads.json`、harness/pytest 日志 |
| `artifacts/hit_rate_test_v5/tier2/` | 17 例高风险冒烟 |
| `artifacts/hit_rate_test_v5/workers1_bench/` / `workers2_bench/` | 8 例并发基准 |
| `docs/evaluation/mcp-agent-knowledge-hit-rate-test-report-v5.md` | 本报告 |

### 复现命令

```bash
# MCP
python run_mcp.py -t streamable-http --port 9000

# Tier 0/1
pytest tests/answering/test_hit_rate_v5_fact_pipeline.py \
       tests/mcp/test_hit_rate_v5_replay.py \
       tests/mcp/test_hit_rate_v4_answer_contract.py -q

# Tier 2
python scripts/hit_rate_test_harness.py \
  --golden evals/golden_set_hit_rate.json \
  --out artifacts/hit_rate_test_v5/tier2 \
  --reuse-snapshot --read-mode unique --workers 1 \
  --cases KB-001,KB-003,KB-005,KB-011,KB-012,KB-014,KB-015,KB-017,KB-018,KB-019,KB-021,KB-022,KB-023,KB-025,KB-026,KB-032,KB-037

# Tier 3 全量（放行执行以 workers=1 为准）
python scripts/hit_rate_test_harness.py \
  --golden evals/golden_set_hit_rate.json \
  --out artifacts/hit_rate_test_v5 \
  --reuse-snapshot --read-mode unique --workers 1

set HIT_RATE_ARTIFACTS_DIR=artifacts/hit_rate_test_v5
python scripts/hit_rate_finalize.py

pytest tests/ -q --ignore=tests/stability/test_provider_worker_pid_terminated.py
```

---

## 10. 放行结论

### **不通过放行**

本轮完成了 SPEC v5 的主干工程目标：

1. **FactCandidate 统一管线**替代“数值先行 + 政策兜底”，政策类不再被年份/文号碎片劫持。  
2. **表格条件—数值绑定**修复，**KB-017/018/019 硬验收通过**（R4 阻塞点消除）。  
3. **passage 邻接 fail-closed** 与 passage 路径不扩整页 blocks。  
4. **snapshot 复用 + harness 加速**：全量约 42 分钟（v4≈73 分钟），reuse 命中 30/37。  
5. **失败原因分离**与 no-answer/FP/幻觉门禁保持严格。  

但 **Ask Fact / E2E / Citation / P1 / 性能目标未全部达标**，按 SPEC 任一门槛未过 → 只能写 **不通过放行**。

### 建议的下一轮焦点（不重做 passage 索引、不降 0.35）

1. 多 required 事实覆盖规划（如 KB-001 需同时覆盖「收支两条线」与「小金库」）。  
2. 跨文档职责/关系类 answer_plan（KB-012/022/023/025 等）在检索 accept 后的候选排序。  
3. 金额/限额类多 passage 召回与行级绑定（KB-002/006/013/020/028）。  
4. search/rerank 超时治理（全量主耗时来源），否则性能门禁难以单独靠 snapshot 达成。  
5. workers=2 在 no-answer reason 标签上的确定性（本轮 answer 等价但 reason 字符串偶发不一致）。
