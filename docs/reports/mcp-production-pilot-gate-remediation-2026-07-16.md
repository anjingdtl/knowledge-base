# 生产试点门槛修复 Delta 报告

**日期：** 2026-07-16  
**PLAN：** `docs/superpowers/plans/2026-07-16-production-pilot-gate-remediation.md`  
**分支：** `fix/mcp-production-pilot-final-validation`  
**相对基线报告：** `docs/reports/mcp-production-pilot-final-validation-2026-07-16.md`

---

## 结论

```text
未达到生产试点门槛
```

本轮按 PLAN 落地代码修复与复测，**多项关键指标显著改善**，但 Precision@5、nDCG@10、Numeric Top1 仍未达到 Spec 强制线。

---

## 已落地改动

| Task | 内容 | Commit 主题 |
|------|------|-------------|
| 1 | `dedupe_by_knowledge_id` + search/FTS 接线 | `fix(retrieval): dedupe search hits by knowledge_id` |
| 2 | `珠/米` 复合单位抽取与降权 | `fix(search): compound numeric units` |
| 3 | CJK 分词窗 + 标题强证据接受 | `fix(rag): accept strong in-corpus title evidence` |
| 4 | file_type 结构化路由 | `fix(router): structured file_type rules` |
| + | 图优先 / hybrid 分析意图优先 | 既有 + `hybrid analytic beats tag` |
| + | 标题词项 boost、structured arg 提升 | `title-term boost and structured arg promotion` |

---

## 指标对比（before → after）

| 指标 | 修复前 | 修复后 | 门槛 | 判定 |
|------|--------|--------|------|------|
| Recall@5 (hybrid n=40) | 0.975 | **1.00** | ≥0.90 | PASS |
| MRR@10 | 0.975 | **1.00** | ≥0.85 | PASS |
| nDCG@10 | 0.949* | **0.55–0.62**† | ≥0.85 | **FAIL** |
| Precision@5 | 0.235 | **0.46–0.52** | ≥0.70 | **FAIL** |
| No-answer Acc | 1.00 | **1.00** | ≥0.90 | PASS |
| Answer completion (real ask n=10) | 0 (全 no_answer) | **1.00** | ≥0.90 | PASS |
| Citation correctness | NOT TESTED | **1.00** (10/10) | ≥0.95 | PASS |
| Source ID validity | — | **1.00** | =1.00 | PASS |
| Numeric Top1 | 0.667 | **0.733** | ≥0.95 | **FAIL** |
| Numeric Top3 doc | 0.267 | **0.267** | ≥0.95 | **FAIL** |
| Routing Mode (MCP 实测旧) | 0.45 | — | ≥0.95 | 旧证据 FAIL |
| Routing Mode (规则 L1 重跑 n=40) | — | **1.00** | ≥0.95 | PASS（规则路径） |
| Routing Arg (L1 重跑) | 0.75 | **1.00** | =1.00 | PASS（规则路径） |
| formal DB | 未变 | **未变** | — | PASS |

\* 修复前 nDCG 在 expected 集合较小时偏高。  
† 扩展 acceptable / 多 expected 时 ideal DCG 上升，nDCG 下降；属指标分母语义与排序仍偏「一击即中」的张力。

---

## 剩余失败根因（下一步）

### 1. Precision@5 / nDCG@10

- 多文档 expected/acceptable 下，top-5 仅约 2.3 个相关 → P@5 上限被「相关文档数 vs k=5」结构压制。  
- 排序仍偏「最相关 1 篇 + 噪声」，未把同主题多文档稳定抬进 top-5。  
- **建议：**  
  1) GT 改为每 query **1 主 expected + ≤3 acceptable** 并单独报告 P@1 / MAP；  
  2) 或增加 query-expansion / 同主题聚类 boost；  
  3) 打开 Reranker 通道并验收 Hybrid+Rerank。

### 2. Numeric Top1 / Top3

- 单位降权已改善 Top1（0.67→0.73），但 expected 文档进 top-3 仍弱。  
- **建议：** 数字单位 query 强制 `apply_numeric_unit_ranking` 后过滤 unit-mismatch；对「应 hit」样本禁止 no_match 短路。

### 3. Routing 正式 MCP 全量

- L1 规则路径离线 **Mode/Tool/Arg = 1.00**。  
- 正式 MCP 全量 harness 在部分路由修复**之前**已跑完；**应用本轮代码后需再跑一遍** `production_pilot_mcp_harness` routing 段作为最终证据。

---

## 工程门禁

```text
pytest tests/services/test_result_dedupe.py
      tests/services/test_numeric_unit_match_compound.py
      tests/services/test_relevance_gate_in_corpus.py
      tests/services/test_structured_file_type_routing.py
      tests/stability/test_graph_routing_priority.py
      tests/eval
→ 相关用例通过
正式 data/kb.db 评估只读，sha 未变化
```

---

## 最终判定

```text
未达到生产试点门槛
```

**原因：** Precision@5、nDCG@10、Numeric 强制线未过；Routing 规则路径已绿但正式 MCP 全量需复验。  
**收益：** 假拒答（Citation）已打通、召回/MRR 满分、图/hybrid 路由规则修复、向量路径与进程隔离仍保持。
