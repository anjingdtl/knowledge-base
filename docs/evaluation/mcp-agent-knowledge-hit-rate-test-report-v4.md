# MCP Agent 知识命中准确率第四轮复测报告

> 依据：`docs/evaluation/mcp-agent-knowledge-hit-rate-remediation-spec-v4.md`  
> 唯一证据：MCP 工具原始返回（本轮全量重跑，**未复用** v3 `final_scored.json`）。  
> Golden Set：`evals/golden_set_hit_rate.json`（**未修改**）。  
> 前三轮 artifacts/报告只读保留（未覆盖）。  
> 本轮 artifacts：`artifacts/hit_rate_test_v4/`。

## 0. 结论（硬性）

### **不通过放行**

| 检查 | 结果 |
| --- | --- |
| Top-1 Accuracy ≥ 75% | **通过**（90.62%） |
| Recall@5 ≥ 88% | **通过**（96.88%） |
| Ask Fact Correctness ≥ 90% | **未通过**（18.75%） |
| Ask Citation Validity ≥ 95% | **未通过**（68.75%） |
| E2E Pass Rate ≥ 90% | **未通过**（18.75%） |
| Hallucination Rate ≤ 5% | **未通过**（6.25%） |
| False Positive Rate ≤ 5% | **通过**（0.00%） |
| P1 缺陷 = 0 | **未通过**（26） |
| 成功 ask 的 passage trace 完整率 100% | **通过**（sources/raw 均为 22/22 = 100%） |
| passage 向量/FTS 覆盖率 100% | **通过** |
| 硬验收 KB-017/018/019/032/037 全部 E2E | **未通过**（仅 032、037 过；017/018/019 失败） |

**不得**描述为“有条件放行”或“完成放行”。

---

## 1. 测试配置与时间

| 项 | 值 |
| --- | --- |
| MCP | streamable-http `127.0.0.1:9000` |
| 版本 | 1.11.1 |
| Golden | 37 例全量，`skipped_resume=0` |
| harness 开始 | 2026-07-28T14:27:27+08:00（见 `harness_meta.txt`） |
| harness 结束 | 约 15:40（总时长 ~4382s / ~73 min，exit 0） |
| 全量 pytest | 2188 passed / 35 failed / 2 skipped，exit 1，~1006s（`pytest_full.txt` / `pytest_full_meta.txt`） |
| 定向 pytest | 32 passed（passage + v3/v4 回归） |

说明：全量 pytest 失败含既有项（版本号一致性、asyncio mark、wiki_read 等）及部分 ask 契约快照；**不能**用“定向 32 passed”替代全量验收。`test_provider_worker_pid_terminated.py` 因缺 `psutil` 在收集阶段被 ignore（与 v2 报告相同）。

---

## 2. 本轮实现映射（SPEC v4）

| 阶段 | 实现 | 状态 |
| --- | --- | --- |
| A | `PassageEvidence` DTO；`citations`/`build_sources` 保留 `passage_id`；成功答案禁止静默 `raw_block` | **达标**（成功答案 passage trace 100%） |
| B | `claim_protocol`：claim 草稿 → ground → 短答案；禁止“问题拆解”自由文本直出 | **部分**：短答案已生效，但事实抽取在真实表格文本上仍错配 |
| C | `numeric_triples` 条件—数值—单位 + `numeric_fact_audit` | **未达标**：真实附件表中仍会串条件/数值（见 §5） |
| D | 严格 `no_answer`（空 answer/sources/raw）；FP=0；KB-032 空答 | **部分达标** |
| E | `direct_slot_evidence`（阈值仍 0.35）；KB-021 召回并 E2E 通过 | **检索侧达标** |
| F | 最新 family 过滤；KB-037 E2E 通过 | **部分达标** |
| G | finalize 增加 source_trace / expected_doc_support / passage completeness / version_leakage 字段 | **已交付诊断** |

**禁止项遵守**：未改 Golden、未降 0.35、未覆盖前三轮 artifacts/报告、未做 case_id 硬编码答案。

---

## 3. 五轮指标对比

数据：`artifacts/hit_rate_test_v4/metrics_comparison.txt`、`final_scored.json`。

| 指标 | 基线 | R1 | R2 | R3 | **R4** | 门禁 | R4 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| Top-1 | 59.38% | 84.38% | 84.38% | 87.50% | **90.62%** | ≥75% | 通过 |
| Recall@5 | 65.62% | 87.50% | 87.50% | 93.75% | **96.88%** | ≥88% | 通过 |
| Ask Fact | n/a | n/a | 34.38% | 62.50% | **18.75%** | ≥90% | **未通过** |
| Ask Citation | n/a | n/a | 93.75% | 84.38% | **68.75%** | ≥95% | **未通过** |
| E2E | n/a | n/a | 31.25% | 62.50% | **18.75%** | ≥90% | **未通过** |
| Hallucination | 3.12% | 6.25% | 6.25% | 12.50% | **6.25%** | ≤5% | **未通过** |
| FP | 0% | 0% | 0% | 20% | **0%** | ≤5% | 通过 |
| P1 | 18 | 3 | 20 | 13 | **26** | 0 | **未通过** |

### 解读（避免混淆）

- **检索继续变强**（Top-1/Recall 再创新高），说明 v3 passage 索引 + v4 direct_slot 有效。  
- **Ask Fact/E2E 相对 v3 大幅回落**：结构化短答案上线后，错误三元组会直接进入最终答案（无长文“碰巧”覆盖 required 的缓冲）。  
- **FP 从 20% 回到 0%**：严格空答合同对 no-answer 集生效（KB-032 空 answer）。  
- **passage trace**：成功作答 22 例中 sources/raw 的 `passage_id` 完整率 **100%**，v3 的 `passage_id=null` / `raw_block` 静默降级问题在成功路径上已消除。  
- `Ask Citation Validity` 因更多 `no_answer`/少 sources 而下降；`rejected=0` 仍表示可追溯完整性门禁未放松。

---

## 4. 硬验收用例（原始 MCP）

| 用例 | 要求 | 实测 | 判定 |
| --- | --- | --- | --- |
| KB-017 | 2000元 + 一个自然月；禁 30元 | Top-1 正确；answer=`- 涉诈10000元`（串用其它处罚档） | **E2E 失败** |
| KB-018 | 30元 + 涉骚扰；禁 2000元 | answer=`- 涉骚扰2000元`（条件—数值错配） | **E2E 失败** |
| KB-019 | III类 + 20万元；禁 10万元 | answer=`- III类10万元` | **E2E 失败** |
| KB-021 | 1个工作日 + 5个工作日 | Top-1 正确；answer 含初审 1 个工作日与产品评估 5 个工作日；e2e_pass | **通过** |
| KB-032 | no_answer 空 answer/sources | answer=`""`，FP=false | **通过** |
| KB-037 | 2026 + 158号；禁一级/二级竞赛 | e2e_pass；answer 含 2026/158号 | **通过** |

结论：硬验收 **未全部通过** → 不得放行。

---

## 5. 失败根因（基于 raw artifacts，非推测）

### 5.1 已验证改善

1. **passage 端到端契约**：成功答案 `sources`/`raw_evidence_used` 均带非空 `passage_id`，`candidate_type=passage`。  
2. **自由长文作答被压制**：不再出现“问题拆解/推理过程”长文；输出为短 bullet。  
3. **严格拒答**：KB-032 空答，FP=0。  
4. **direct_slot_evidence**：KB-021 在全局阈值仍为 0.35 时进入候选并作答。  
5. **版本问法**：KB-037 最新文档 + required 文号通过。

### 5.2 仍阻塞放行的主根因（P0 未完成）

**真实附件/表格 passage 上的条件—数值锚定失败。**

- 同一 passage 内并存多档处罚（2000/30/10000…）或多档限额（II 10万 / III 20万）。  
- 当前三元组抽取/选择在“条件邻域”上仍会把 **错误档位** 绑到 query 条件（KB-017/018/019 原始 answer 可直接复现）。  
- 这不是检索问题（Top-1/Recall 已正确），而是 **claim 选择层** 问题；下一轮必须在表格行/列上下文上重建三元组，而不是简单 clause split。

### 5.3 次要问题

- 规则短答对“多 required 事实”覆盖不足 → Ask Fact 整体偏低。  
- 全量 pytest 仍有 35 失败（含既有环境/快照类）；需在报告中分列“本改动相关 / 既有”。本轮相关：`tests/answering/*`、`test_public_ask_contract` 快照已更新后定向回归通过；全量中其余失败多数与版本号/asyncio/wiki 路径相关。

---

## 6. 自动化测试结果

### 定向（必须）

```text
pytest tests/services/test_passage_builder.py \
       tests/services/test_passage_store_and_search.py \
       tests/mcp/test_hit_rate_v3_regressions.py \
       tests/mcp/test_hit_rate_v4_answer_contract.py -q
```

**32 passed**（`artifacts/hit_rate_test_v4/pytest_directed.txt`）。

### 全量

```text
pytest tests/ -q --ignore=tests/stability/test_provider_worker_pid_terminated.py
```

- 开始/结束：见 `pytest_full_meta.txt`  
- **2188 passed，35 failed，2 skipped，exit 1**  
- 日志：`artifacts/hit_rate_test_v4/pytest_full.txt`

---

## 7. 交付物

| 路径 | 内容 |
| --- | --- |
| `artifacts/hit_rate_test_v4/` | 37 例原始交互、`00_capabilities.json`、`final_scored.json`、`summary.json`、`metrics_comparison.txt`、harness/pytest 日志 |
| `docs/evaluation/mcp-agent-knowledge-hit-rate-test-report-v4.md` | 本报告 |

### 复现命令

```bash
python -m alembic upgrade head   # 若需
python scripts/rebuild_passage_index.py --embed-only --batch-size 16 --timeout 180
python run_mcp.py -t streamable-http --port 9000

python scripts/hit_rate_test_harness.py \
  --golden evals/golden_set_hit_rate.json \
  --out artifacts/hit_rate_test_v4

set HIT_RATE_ARTIFACTS_DIR=artifacts/hit_rate_test_v4
python scripts/hit_rate_finalize.py

pytest tests/services/test_passage_builder.py tests/services/test_passage_store_and_search.py \
       tests/mcp/test_hit_rate_v3_regressions.py tests/mcp/test_hit_rate_v4_answer_contract.py -q
pytest tests/ -q --ignore=tests/stability/test_provider_worker_pid_terminated.py
```

---

## 8. 放行结论

### **不通过放行**

本轮完成了 SPEC v4 的部分工程目标：

- passage 溯源契约在成功路径上 **100%**；  
- 严格拒答使 FP=0、KB-032 通过；  
- direct_slot 修复 KB-021；  
- KB-037 通过；  
- 检索指标进一步提升。

但 **Ask Fact / E2E 未达标**，且硬验收 **KB-017/018/019 失败**（条件—数值串配）。按 SPEC：任一门槛或硬验收未过 → 只能写 **不通过放行**。

### 建议的下一轮焦点（不重做 passage 索引）

1. 表格行级三元组：按“同一行/同一触发条件列”绑定数值，禁止跨行串值。  
2. 查询槽位与 fact_type 强制过滤（处罚问法只接受 元/万元，且每个条件仅一条最优三元组）。  
3. 多 required 事实的 claim 覆盖（一条答案并列全部槽位事实）。  
4. 在真实 MCP 上对 017/018/019 做红线回归后再跑全量 Golden。
