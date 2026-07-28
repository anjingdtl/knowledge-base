# MCP Agent 知识命中准确率第二轮复测报告

> 依据：`docs/evaluation/mcp-agent-knowledge-hit-rate-remediation-spec-v2.md`  
> 唯一证据：MCP 工具（`ping` / `kb_capabilities` / `search` / `read` / `ask`）原始返回。  
> Golden Set：`evals/golden_set_hit_rate.json`（**未修改**）。  
> 基线 artifacts：`artifacts/hit_rate_test/`（只读保留）。  
> 第一轮 artifacts：`artifacts/hit_rate_test_after_fix/`（只读保留）。  
> 本轮 artifacts：`artifacts/hit_rate_test_v2/`。

## 0. 结论（硬性）

### **不通过放行**

任一硬验收或最低放行线未通过即不得放行。本轮：

| 检查 | 结果 |
| --- | --- |
| 向量索引 coverage ≥ 95% | **通过**（`coverage=1.0`，128348/128348） |
| Top-1 Accuracy ≥ 75% | **通过**（84.38%） |
| Recall@5 ≥ 88% | **未通过**（87.50%） |
| Ask Fact Correctness ≥ 90% | **未通过**（34.38%） |
| Ask Citation Validity ≥ 95% | **未通过**（93.75%） |
| E2E Pass Rate ≥ 90% | **未通过**（31.25%） |
| Hallucination Rate ≤ 5% | **未通过**（6.25%） |
| False Positive Rate ≤ 5% | **通过**（0.00%） |
| 关键硬验收用例 | **未全部通过**（见 §4） |

**不得**将本轮结果描述为“有条件放行”。

---

## 1. 测试配置与索引证明

| 项 | 值 |
| --- | --- |
| 服务 | MCP streamable-http `127.0.0.1:9000` |
| 版本 | 1.11.1 |
| 配置档 | 运行时 extended（`kb_capabilities` 工具清单） |
| Embedding 模型 | `BAAI/bge-m3` |
| Embedding API | SiliconFlow（`api.siliconflow.cn`） |
| 知识条目 / blocks | 108 / 128348 |
| 重建前 coverage | 0.0（0/128348） |
| 重建后 coverage | **1.0**（128348/128348） |
| 重建证明 | `artifacts/hit_rate_test_v2/phase0_vector_rebuild.json` |
| 重建时间 | 2026-07-27 ~17:38 → 18:01（并行写入）；缺口 384 块补齐后 100% |
| 复测时间 | 2026-07-28（resume 完成 37 例） |
| wiki_serving_status | 见 `00_capabilities.json` runtime_diagnostics |

`kb_capabilities.runtime_diagnostics.vector_index`（复测时）：

```json
{
  "blocks": 128348,
  "vectors": 128348,
  "coverage": 1.0,
  "sqlite_vec_ok": true,
  "recommendation": "向量索引状态正常"
}
```

---

## 2. 本轮代码改动摘要（映射 SPEC）

| 阶段 | 改动 | 对应问题 |
| --- | --- | --- |
| 0 | 官方 `BlockStore.add_block_embeddings_batch` + EmbeddingService 并行重建向量 | coverage=0 导致语义召回不可用 |
| 1 | `src/retrieval/canonical_snapshot.py`；`search`/`ask` 共用 `_build_shared_snapshot`；`AnswerService` 接受 `evidence_snapshot`，禁止静默再检索 | search/ask 证据链不一致 |
| 2 | 删除 raw_evidence/claims 并入 allowlist；`source_in_allowlist`；sources 仅 preaccepted/adjacent | 引用 allowlist 绕过 |
| 2 | `rank_with_freshness` 后置 + 同制度族年份调整；`filter_to_latest_versions` 生成隔离 | KB-037 旧版污染 |
| 3 | 生产路径 `expand_results_with_adjacent`；`strip_unanchored_numeric_assertions(..., question=)` 主体锚定 | KB-019 III类/20万元 |
| 5 | `hit_rate_score.py` / `finalize.py` 拆分 Ask Fact / Ask Citation / E2E；三轮对比脚本 | 混合文本虚高 grounded |

**明确未做（禁止项）：**

- 未改 Golden Set / 基线 / 第一轮 artifacts  
- 未降阈值、未关 evidence gate、未下调缺陷等级  
- 未物理删除业务文档  

---

## 3. 指标三轮对比

数据源：`artifacts/hit_rate_test_v2/metrics_comparison.txt`、`final_scored.json`。

| 指标 | 基线 | 第一轮 | 第二轮 | 最低放行线 | 第二轮 |
| --- | ---: | ---: | ---: | ---: | --- |
| Top-1 Accuracy | 59.38% | 84.38% | **84.38%** | ≥75% | 通过 |
| Recall@5 | 65.62% | 87.50% | **87.50%** | ≥88% | **未通过** |
| Ask Fact Correctness | n/a | n/a* | **34.38%** | ≥90% | **未通过** |
| Answer Groundedness（旧口径别名） | 40.62% | 84.38% | **34.38%** | ≥90% | **未通过** |
| Ask Citation Validity | n/a | n/a* | **93.75%** | ≥95% | **未通过** |
| Citation Validity（按 source×expected_id） | 70.48% | 63.89% | **68.94%** | ≥95% | **未通过** |
| E2E Pass Rate | n/a | n/a | **31.25%** | ≥90% | **未通过** |
| Hallucination Rate | 3.12% | 6.25% | **6.25%** | ≤5% | **未通过** |
| False Positive Rate | 0.00% | 0.00% | **0.00%** | ≤5% | 通过 |

\* 第一轮“Answer Groundedness / Citation Validity”使用 **search+read+ask 混合文本** 与“任一 source 命中 expected”口径，会系统性抬高 grounded，**不能**与第二轮 Ask Fact / Ask Citation 直接等同。第二轮按 SPEC v2 仅以 `ask.answer` / `ask.sources` 计。

### 引用分层（第二轮 ask.sources）

| 桶 | 条数 |
| --- | ---: |
| preaccepted | 49 |
| adjacent_extension | 83 |
| expected_id（仅当不在 snapshot 仍落在 Golden expected） | 0 |
| **rejected** | **0** |

说明：allowlist 静默并入 `raw_evidence_used` 的路径已删除；最终 sources 中 **rejected=0**，引用完整性工程门禁生效。但“可追溯 ≠ 命中 Golden expected 文档”，故 expected_id 口径的 Citation Validity 仍仅 68.94%。

---

## 4. 关键用例硬验收

| 用例 | 必须结果 | 实测 | 判定 |
| --- | --- | --- | --- |
| KB-007 | search Recall@5 命中 `acf5e2d6`；ask 同一候选“不少于5人” | search Top-5 均为采购类；未命中 `acf5e2d6` | **失败** |
| KB-009 | 2025 优先；住宿+伙食必需事实；不混旧 80 元 | Top-1=2025；ask 未覆盖伙食/住宿必需事实 | **失败** |
| KB-017 | 命中 `51b17abe`；ask `2000元/个` | search 空；gate `top_score=0.2004<0.35` | **失败** |
| KB-019 | III类 `20万元`；不以 `10万元` 作答 | e2e_pass=True；numeric guard 剥离未锚定值 | **通过** |
| KB-021 | 命中 `b40b8949`；`1个工作日`+`5个工作日` | search 空；gate `top_score=0.2936<0.35` | **失败** |
| KB-023 | search 命中合同专用章文档；ask 只引已接受证据 | search 仅内控细则；未命中专用章文档 | **失败** |
| KB-037 | 2026 Top-1；ask 无“一级竞赛/二级竞赛” | Top-1=2026；ask 仍出现旧版分级表述 | **失败** |
| KB-030–034 | 拒答；FP 不升 | FP=0.00% | **通过** |

---

## 5. 失败根因（抽样，原始 envelope）

### KB-017 / KB-021（语义+门禁）

- 向量覆盖已 100%，但正式 query 经 unified gate 后 **无接受候选**。  
- 警告：`evidence gate blocked generation (top_score=0.20xx/0.29xx < 0.35)`。  
- 说明：coverage 修复是必要条件，**不是**充分条件；query rewrite / 特征融合仍需针对这两类组合问法继续迭代（且须复测 KB-030–034）。

### KB-007 / KB-023（召回排序）

- KB-007：query 含“安全生产/专职/南宁/5人”，FTS/语义仍被采购招标片段（含“专职安全生产管理人员”字样）占满 Top-5。  
- KB-023：命中内控细则而非合同专用章管理办法。  
- 共享 snapshot 使 search/ask **一致失败**（符合“不得偶然答对”纪律），但召回本身未修复。

### KB-009 / KB-037（生成层事实）

- 检索 Top-1 已正确（2025 / 2026），但 `ask.answer` 未稳定写出 required facts，或仍混入禁止旧表述。  
- 本地版本生成隔离（`filter_to_latest_versions`）在 KB-037 上 **未完全阻止** 2023 源进入最终 sources（sources 中仍见 `1acb61b4`）。

### KB-019（已修复）

- 相邻 block 扩展 + 主体数值锚定生效；`numeric_fact_guard_stripped_unanchored_value` 出现且 e2e 通过。

### 评分口径下 Ask Fact 变低（预期）

- 第一轮 grounded 混入 search/read 文本，会把“库中有事实但答案未写清”标为正确。  
- 第二轮只看 `ask.answer`，故 Ask Fact Correctness=34.38% 更接近真实 Agent 可见结果。

---

## 6. 自动化测试实际结果

### 定向（SPEC §9.3）

```
pytest tests/mcp/test_hit_rate_regressions.py
       tests/stability/test_current_information_no_answer.py
       tests/stability/test_pre_llm_evidence_gate.py
       tests/services/test_relevance_gate_in_corpus.py -q
```

**实际：57 passed，0 failed**（约 3.4s）。  
（回归文件由 41 例扩展到含 v2 共享 snapshot / 版本隔离 / 主体锚定用例。）

### 全量

```
pytest tests/ -q --ignore=tests/stability/test_provider_worker_pid_terminated.py
```

**实际：2169 passed，22 failed，2 skipped**（约 838s）。

说明：

- `tests/stability/test_provider_worker_pid_terminated.py` 收集阶段因环境缺少 `psutil` 报错（**与本轮改动无关**），全量命令中 ignore。  
- 22 failed 含版本号一致性、asyncio mark 缺失的 routing eval、wiki_read_stage 等；**不全是本轮引入**。  
- 本轮相关：`tests/answering/*`、`tests/mcp/test_hit_rate_regressions.py`、`tests/test_public_ask_contract.py` 在定向复测中通过。

日志：`artifacts/hit_rate_test_v2/pytest_directed.txt`、`pytest_full.txt`。

---

## 7. 交付物清单

| 交付 | 路径 |
| --- | --- |
| 新 artifacts | `artifacts/hit_rate_test_v2/`（37×Case + `00_capabilities.json` + `summary.json`） |
| 评分明细 | `artifacts/hit_rate_test_v2/scored.json`、`final_scored.json` |
| 三轮对比 | `artifacts/hit_rate_test_v2/metrics_comparison.txt` |
| 向量重建证明 | `artifacts/hit_rate_test_v2/phase0_vector_rebuild.json` |
| 本报告 | `docs/evaluation/mcp-agent-knowledge-hit-rate-test-report-v2.md` |

主要变更文件：

- `src/retrieval/canonical_snapshot.py`（新）  
- `src/mcp/tools/retrieval.py`  
- `src/answering/service.py` / `assembler.py` / `fact_guard.py` / `context_builder.py`（既有）  
- `src/services/version_rank.py`  
- `src/application/retrieval_commands.py`  
- `scripts/hit_rate_score.py` / `hit_rate_finalize.py` / `hit_rate_compare.py` / `hit_rate_test_harness.py`（`--resume`）  
- `tests/mcp/test_hit_rate_regressions.py`  

---

## 8. 剩余风险与后续（非放行条件）

1. **召回**：KB-007/017/021/023 仍未稳定 Recall@5；需在 **不降阈值** 前提下强化标题精确匹配、条款级 FTS OR、以及组合问法改写，并回归 no-answer 集。  
2. **生成**：Top-1 正确仍常得不到 required facts（KB-009 等）；需约束生成模板 / claim 抽取，避免长篇“证据梳理”却不写结论句。  
3. **版本隔离**：KB-037 生成 sources 仍可能带历史版；`filter_to_latest_versions` 的 title 归并与 ask 出源过滤需再收紧。  
4. **延迟**：单例 search+ask 常 30–120s；全量 37 例约 45–50 分钟，Agent 体验仍差。  
5. **全量 pytest** 中既有失败与环境依赖（psutil/asyncio）应单独清债，勿与命中率放行混谈。

---

## 9. 最终结论

**不通过放行。**

理由（充分）：

1. Recall@5、Ask Fact Correctness、Ask Citation Validity、E2E Pass Rate、Hallucination Rate 均未达最低线；  
2. 硬验收 KB-007 / 009 / 017 / 021 / 023 / 037 未全部通过；  
3. 虽已完成向量 100% 覆盖、共享候选、引用 allowlist 去绕过、KB-019 条款锚定等工程修复，但 **真实 MCP envelope 指标仍未达到生产 Agent 放行线**。

下一轮必须在 **不改 Golden Set、不降阈值、不关 gate** 的前提下，优先打通 KB-017/021 召回与生成写实、KB-007/023 排序、KB-037 旧版源隔离，并保持本轮 Ask-only 评分口径复测。
