# ShineHeKB MCP 生产试点前最终验收报告

**日期：** 2026-07-16  
**分支：** `fix/mcp-production-pilot-final-validation`  
**Spec：** `docs/superpowers/specs/ShineHeKB_MCP_生产试点前最终验收收尾_Spec.md`  
**产物：** `artifacts/production-pilot-final-validation/`  

---

## 1. 结论

```text
未达到生产试点门槛
```

历史报告中的「达到生产试点门槛」在本轮复查后**作废**。本轮建立了可信评估体系，但若干强制门槛未满足（见第 5 节）。

可表述为：**可继续受控内测；生产试点结论未通过。**

---

## 2. 基线

| 项 | 值 |
|----|-----|
| baseline_commit_sha | `75435e77dfa5d9a58a639b519ea128075bf6e7f2` |
| version | 1.10.3 |
| python | 3.14.3 |
| fastmcp | 3.2.4 |
| formal documents | 155 |
| active blocks / vectors | 3611 / 3611（coverage **1.00**） |
| vector backend | sqlite-vec (`vec_blocks`) |
| embedding | BAAI/bge-m3 |
| llm | MiniMax-M3 |
| formal DB sha256 | `dee013a91eeae27b…`（验收前后一致） |

完整基线：`artifacts/production-pilot-final-validation/baseline.json`

---

## 3. 数据集（人工 Ground Truth）

| 数据集 | 条数 | 说明 |
|--------|------|------|
| retrieval | 63 | 非空 `expected_ids`，title+正文证据 |
| no_answer | 32 | 实时/库外/不足证据 |
| numeric_units | 28 | 含单位与 forbidden |
| routing | 45 | mode/tool/outcome |
| answer_citations | 28 | fact + supporting IDs |

- **排除：** 全部 PAD、`hit_or_empty` 自动满分样本  
- **方法：** `scripts/build_production_pilot_datasets.py`（禁止 search 反填最终 GT）  
- **指南：** `docs/testing/production-pilot-eval-annotation-guide.md`  
- **schema 测试：** `tests/eval/test_production_pilot_dataset_schema.py` 通过  

---

## 4. 指标口径修复（相对历史失真）

已删除/禁止：

- `expected_ids` 为空 → 满分  
- PAD / hit_or_empty 进 Recall  
- no-answer 贡献 Citation 满分  
- 数字单位空结果默认通过  
- timeout 计 task completed  

实现：`evals/production_pilot_metrics.py` + `scripts/production_pilot_eval.py`  
旧 harness 标记 **DEPRECATED**：`scripts/final_closure_mcp_harness.py`

---

## 5. 强制门槛对照

### 5.1 Retrieval（正式 MCP stdio/http hybrid，n=40）

| 指标 | 门槛 | 实测 | 分母 | 判定 |
|------|------|------|------|------|
| Recall@5 | ≥0.90 | **0.975** | 39/40 | PASS |
| MRR@10 | ≥0.85 | **0.975** | 39/40 | PASS |
| nDCG@10 | ≥0.85 | **0.949** | 37.96/40 | PASS |
| Precision@5 | ≥0.70 | **0.235** | 9.4/40 | **FAIL** |
| Forbidden Hit@5 | ≤0.05 | n/a（无 forbidden 标注参与） | 0 | N/A |

FTS-only / Vector-only（stdio，retrieval-channels）：

| 通道 | Recall@5 | MRR@10 |
|------|----------|--------|
| FTS | 0.975 (39/40) | 0.975 |
| Vector | 0.95 (19/20) | 0.95 |
| Hybrid | 0.975 (39/40) | 0.975 |

> 注：Vector 通道当前与 search 默认语义路径一致（工具无 mode 参数）；Hybrid+Reranker 全量因配置 `enable_rerank=false` 未单独扩档 → **部分 NOT TESTED**。

### 5.2 No-answer（stdio/http，n=15）

| 指标 | 门槛 | 实测 | 判定 |
|------|------|------|------|
| No-answer Accuracy | ≥0.90 | **1.00** (15/15) | PASS |
| False-answer Rate | ≤0.05 | **0.00** | PASS |
| False-positive Retrieval | ≤0.10 | **0.00** | PASS |

### 5.3 Numeric（n=15 抽样）

| 指标 | 门槛 | 实测 | 判定 |
|------|------|------|------|
| Top1 unit accuracy | ≥0.95 | **0.667** (10/15) | **FAIL** |
| Top3 expected doc recall | ≥0.95 | **0.267** (4/15) | **FAIL** |
| Forbidden unit confusion | ≤0.05 | **0.00** (0/3) | PASS |

### 5.4 Citation / 真实回答（n=5 真实 Provider ask）

全部返回 `answer_mode=no_answer`（检索/门禁过严或 ask 拒答过度）。

| 指标 | 门槛 | 实测 | 判定 |
|------|------|------|------|
| Answer completion | ≥0.90 | **0/0 分母（NOT SCORED）** | **FAIL / NOT TESTED** |
| Citation completeness/correctness | ≥0.95 | 分母 0 | **NOT TESTED** |
| Source ID validity | =1.00 | 分母 0 | **NOT TESTED** |

### 5.5 Routing（n=20 实测；图优先已修代码，全量未重跑）

| 指标 | 门槛 | 实测（修复前） | 判定 |
|------|------|----------------|------|
| Mode Accuracy | ≥0.95 | **0.45** (9/20) | **FAIL** |
| Tool Accuracy | ≥0.95 | **0.40** (8/20) | **FAIL** |
| Argument Contract | =1.00 | **0.75** (15/20) | **FAIL** |
| Protocol Execution | =1.00 | **1.00** | PASS |
| Task Completion | ≥0.90 | **0.45** | **FAIL** |
| Timeout-free | ≥0.90 | **0.45** | **FAIL** |

修复：`RuleRouter` 图信号优先于 tag structured；`PlanetaryRouter` 图信号不再降级 structured。单元测试 `tests/stability/test_graph_routing_priority.py` 通过。  
**正式 MCP 全量 routing 回归未在修复后重跑 → 生产门槛仍按失败计。**

### 5.6 Vector 路径与覆盖

| 项 | 结果 |
|----|------|
| 绝对 `storage.data_dir` + temp `SHINEHE_HOME` | **已修复**（共用 formal DB） |
| active coverage | **1.00** (3611/3611) |
| health.vector | 字段已扩展 |
| formal DB 写入 | **未变化** |
| Vector index EMPTY（路径漂移） | 路径修复后不再指向空库 |

### 5.7 Timeout / 进程隔离

| 项 | 结果 |
|----|------|
| `run_in_terminable_process` | terminate + join，`background_work_may_continue=false` |
| 50 次 timeout | 无 abandoned 增长 |
| timeout 后后续请求 | 成功 |
| 矩阵文档 | `docs/architecture/provider-cancellation-matrix.md` |

### 5.8 真实 Provider 并发

| 档位 | n | success_rate | P50 ms | P95 ms |
|------|---|--------------|--------|--------|
| 1 | 10 | **1.00** | 2782 | 5004 |
| 5 | 20 | **1.00** | 15662 | 20730 |
| 10 | 30 | **1.00** | 25518 | 33849 |

- 10 并发 search ≥99%：**PASS**  
- 真实 ask 并发：**NOT TESTED**（ask 样本全 no_answer，未扩并发 ask）  
- 正式 DB：**unchanged**

### 5.9 工程门禁

| 项 | 结果 |
|----|------|
| 新增/相关 pytest | 35 passed（eval + path + process + graph route） |
| ruff（本轮改动范围） | 0 error |
| mypy（本轮核心模块） | 通过（deadline ignore no-any-return） |
| CI | 增加 `production-pilot-metric-gate` job + `real-provider-validation.yml`（workflow_dispatch） |
| 远程 CI 全绿 | **NOT TESTED**（需 push 后确认） |
| 正式 15–30min soak | **NOT TESTED**（本轮改为短套件 + 并发） |

---

## 6. 历史无效 vs 新可信指标

| 类别 | 历史（v1.10.3 final-closure） | 本轮 |
|------|------------------------------|------|
| Golden | 107 条含 PAD/空 expected 满分 | 分指标独立数据集，禁 PAD |
| Recall | 虚高 | 仅非空 expected，正式 MCP 0.975 |
| Citation | no-answer 真空满分 | 无适用样本时分母 0 |
| 向量 | 可能 EMPTY（路径） | 路径统一，active coverage 1.0 |
| 试点结论 | 曾宣称达标 | **未达到** |

---

## 7. 未解决问题

1. **Precision@5** 远低于 0.70（召回高、精度低，排序噪声大）  
2. **真实 ask 过度 no_answer**，Citation 无法计分  
3. **Routing 语义** 图/structured 混淆（代码已修，正式全量未重验）  
4. **Numeric unit** Top1/Top3 未达门槛  
5. **Hybrid+Reranker** 独立通道未完成  
6. **远程 CI / 长短稳** 本轮未完整重跑  

---

## 8. NOT TESTED

- 真实 Provider ask 并发 1/3/5 全档  
- 正式环境 30min HTTP + 15min stdio 长稳（本轮）  
- 全部 25+ answer citation 人工复核  
- Hybrid+Reranker 独立指标全量  
- 远程 GitHub Actions 本分支全绿确认  

---

## 9. 提交与证据

建议提交序列见 Spec §52；产物目录见 `artifacts/production-pilot-final-validation/`。

---

## 10. 最终判定

```text
未达到生产试点门槛
```

原因摘要：Precision@5、Numeric、Routing、真实 Citation 未达标或证据不足；工程与检索召回/no-answer/向量覆盖/进程终止/并发 search 已有可信进展，但 Spec 要求**全部**强制门槛通过方可宣布试点达标。
