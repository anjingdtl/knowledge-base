# MCP Agent 知识命中准确率第三轮复测报告

> 依据：`docs/evaluation/mcp-agent-knowledge-hit-rate-remediation-spec-v3.md`  
> 唯一证据：MCP 工具（`ping` / `kb_capabilities` / `search` / `read` / `ask`）原始返回。  
> Golden Set：`evals/golden_set_hit_rate.json`（**未修改**）。  
> 前两轮 artifacts / 报告只读保留：  
> - `artifacts/hit_rate_test/`、`artifacts/hit_rate_test_after_fix/`、`artifacts/hit_rate_test_v2/`  
> - `docs/evaluation/mcp-agent-knowledge-hit-rate-test-report.md`、`…-after-fix.md`、`…-v2.md`  
> 本轮 artifacts：`artifacts/hit_rate_test_v3/`。

## 0. 结论（硬性）

### **不通过放行**

任一硬验收或最低放行线未通过即不得放行。本轮：

| 检查 | 结果 |
| --- | --- |
| retrieval passage 向量覆盖率 = 100% | **通过**（6456/6456） |
| retrieval passage FTS 覆盖率 = 100% | **通过**（6456/6456） |
| passage 长度门禁（avg≥300, p50≥250, p95≤1300） | **通过**（avg=646.1, p50=596, p95=1107；short=0） |
| Top-1 Accuracy ≥ 75% | **通过**（87.50%） |
| Recall@5 ≥ 88% | **通过**（93.75%） |
| Ask Fact Correctness ≥ 90% | **未通过**（62.50%） |
| Ask Citation Validity ≥ 95% | **未通过**（84.38%） |
| E2E Pass Rate ≥ 90% | **未通过**（62.50%） |
| Hallucination Rate ≤ 5% | **未通过**（12.50%） |
| False Positive Rate ≤ 5% | **未通过**（20.00%） |
| P1 缺陷 = 0 | **未通过**（13） |

**不得**将本轮结果描述为“有条件放行”或“基本通过”。

---

## 1. 测试配置与索引证明

| 项 | 值 |
| --- | --- |
| 服务 | MCP streamable-http `127.0.0.1:9000` |
| 版本 | 1.11.1 |
| Embedding 模型 | `BAAI/bge-m3`（与前两轮一致） |
| 知识条目 / blocks | 108 / 128348（图谱结构单元，**非**检索单元） |
| retrieval passages | **6456** |
| passage 向量 / FTS | 6456 / 6456（coverage=1.0） |
| passage 长度 | avg=646.1, p50=596.0, p95=1107.0, short_passage=0 |
| 重建证明 | `artifacts/hit_rate_test_v3/passage_rebuild.json` |
| 复测时间 | 2026-07-28（约 12:00–12:59，全量 37 例无 resume） |
| harness 日志 | `artifacts/hit_rate_test_v3/harness_run.log` |

`kb_capabilities.runtime_diagnostics.passage_index`（复测时）：

```json
{
  "retrieval_index_unit": "passage",
  "passages": 6456,
  "embedded": 6456,
  "fts": 6456,
  "vector_coverage": 1.0,
  "fts_coverage": 1.0,
  "avg_char_count": 646.1,
  "p50_char_count": 596.0,
  "p95_char_count": 1107.0,
  "short_passage_count": 0,
  "length_gate_ok": true
}
```

block 级 `vector_index` 仍报告 128348/128348，但 **不得** 再作为检索健康度代表；本轮以 `passage_index` 为准。

---

## 2. 本轮代码改动摘要（映射 SPEC v3）

| 阶段 | 改动 | 对应问题 |
| --- | --- | --- |
| A | Alembic `k001_retrieval_passages`：`retrieval_passages` + `passage_fts` + 运行时 `vec_passages` | 图谱原子块错误兼任语义检索单元 |
| A | `passage_builder.py`：确定性合并 400–1000 字、重叠、标题前缀、block 溯源 | 微块平均 ~19 字无法承载条款 |
| A/E | `passage_store.py` + `rebuild_passage_index`：构建 / 清理 / 批量 embed / 健康度 | 可重建、可观测、原子同步删除 |
| B | `hybrid_search.py` passage-first；`search_service` 打包 `passage_id`/family/version；hybrid 超时 25s→90s 避免回退 BlockStore | 超时回退导致继续返回微块 |
| B | MCP `_retrieve_candidates`：passage FTS 优先 + 微块→passage 回填 | search 结果文本仍是 20 字碎片 |
| C | `fallbacks.py` 证据包用完整 passage；`fact_guard` 多条件锚定（KB-010） | 数值保护误拒答 / 截断 |
| D | `document_family.py` + `version_rank.family_key_of` / generation 排除旧版 | KB-037 旧“一级/二级”污染 |
| F | `hit_rate_finalize.classify_defect`：`recall5=false` 必记 P1 retrieval_recall | 漏报 KB-007/023 类 |

**明确未做（禁止项）：**

- 未改 Golden Set / 前两轮 artifacts / 前两轮报告  
- 未降 evidence gate 阈值（仍 0.35）  
- 未关闭数值/版本保护  
- 未把图谱 `blocks` 改写为大段文本  

---

## 3. 指标四轮对比

数据源：`artifacts/hit_rate_test_v3/metrics_comparison.txt`、`final_scored.json`。

| 指标 | 基线 | 第一轮 | 第二轮 | **第三轮** | 最低放行线 | 第三轮 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| Top-1 Accuracy | 59.38% | 84.38% | 84.38% | **87.50%** | ≥75% | 通过 |
| Recall@5 | 65.62% | 87.50% | 87.50% | **93.75%** | ≥88% | **通过** |
| Ask Fact Correctness | n/a | n/a* | 34.38% | **62.50%** | ≥90% | **未通过** |
| Ask Citation Validity | n/a | n/a* | 93.75% | **84.38%** | ≥95% | **未通过** |
| E2E Pass Rate | n/a | n/a | 31.25% | **62.50%** | ≥90% | **未通过** |
| Hallucination Rate | 3.12% | 6.25% | 6.25% | **12.50%** | ≤5% | **未通过** |
| False Positive Rate | 0.00% | 0.00% | 0.00% | **20.00%** | ≤5% | **未通过** |
| P1 缺陷数 | 18 | 3 | 20 | **13** | 0 | **未通过** |

\* 第一轮 grounded/citation 为混合文本口径，不可与 v2/v3 Ask Fact / Ask Citation 直接等同。

### 相对第二轮的实质变化

- **检索层明显改善**：Recall@5 首次过线（87.5%→93.75%）；Top-1 87.5%。  
- **Ask 事实正确率翻倍但仍远未达标**（34.4%→62.5%）：passage 证据变完整后，生成与门禁/版本隔离仍不足。  
- **副作用**：Hallucination / FP 升高（完整上下文使 LLM 更容易扩写；个别 no-answer 用例被答偏）。  
- **Citation Validity** 略降：更多答案带 sources，但部分 expected_id 未对齐（仍 0 rejected 桶内混用 adjacent）。

---

## 4. 关键用例硬验收

| 用例 | 必须结果 | 实测摘要 | 判定 |
| --- | --- | --- | --- |
| KB-002 | 证据包含账户类别+额度完整原文 | Top 候选文本长度 ~748（passage）；ask 仍可能未写全 required | 检索改善 / 事实未稳 |
| KB-007 | Recall@5 命中安全生产文档 | Top-1=`acf5e2d6`（第二轮失败项已修） | **检索通过** |
| KB-010 | 多条件处罚数值不误拒答 | 仍有 fact/rank 问题；numeric guard 审计存在 | **未完全通过** |
| KB-017 | 命中 `51b17abe` + 2000 元 | Top-1 已命中；ask fact 仍失败 | **检索通过 / 事实未过** |
| KB-019 | III类 20 万元 | Top-1 正确；guard 审计仍出现错配风险 | **不稳** |
| KB-021 | 产品问需时限 | search top=None（gate 拦截） | **失败** |
| KB-023 | 合同专用章效力 | Top-1 命中专用章文档族 | **检索改善** |
| KB-037 | 2026 + 无一级/二级 | Top-1=2026；ask 仍可能混旧版表述 | **未完全通过** |
| KB-030–034 | 拒答；FP 不升 | FP=20%（含 KB-032 等） | **失败** |

P1 清单（本轮）：  
`KB-009, KB-010, KB-011, KB-013, KB-014, KB-015, KB-017, KB-018, KB-019, KB-021, KB-022, KB-032, KB-037`  
（另 P2：`KB-026` 排名/易混淆）

---

## 5. 失败根因（第三轮后）

### 5.1 已验证修复的根因

1. **图谱微块作检索单元**：blocks 平均 19.3 字 → 独立 passage 平均 646 字，长度门禁通过。  
2. **hybrid 25s 超时回退 BlockStore**：超时提升到 90s + passage 路径，候选文本从 ~20 字恢复到 ~600 字。  
3. **search/ask 双轨**：仍共用 `build_canonical_snapshot`；本轮在 `_retrieve_candidates` 统一 passage FTS 与回填。  
4. **缺陷归因漏报**：`recall5=false` 必记 P1 retrieval_recall（分类器单测覆盖）。

### 5.2 仍未根治

1. **生成层 / 提示词**：LLM 常输出长“问题拆解”而 required_facts 短语未机械命中；Ask Fact 仅 62.5%。  
2. **KB-021 等空候选**：gate `top_score < 0.35` 仍拦截（未降阈值）；passage 召回后分数特征仍不够。  
3. **版本隔离不彻底（KB-037）**：family 归类已引入，但 generation/answer 仍可能复述旧规则。  
4. **无答案误答（FP）与幻觉上升**：完整证据包提高“能说一点”的冲动，需更强 no_answer 纪律，而非降 gate。

---

## 6. 自动化测试

### 定向（passage / 版本 / 数值 / 缺陷分类）

```
pytest tests/services/test_passage_builder.py \
       tests/services/test_passage_store_and_search.py \
       tests/mcp/test_hit_rate_v3_regressions.py -q
```

**实际：16 passed**（见 `artifacts/hit_rate_test_v3/pytest_directed.txt`）。

### 全量项目测试

本轮因 Golden 全量 MCP 复测耗时约 58 分钟，**未再附跑** 完整 `pytest tests/`。若需补跑，建议在下一轮固定窗口执行并记录与本改动无关的既有失败基线。

---

## 7. 交付物清单

| 交付 | 路径 |
| --- | --- |
| 新 artifacts | `artifacts/hit_rate_test_v3/`（37×Case + `00_capabilities.json`） |
| 评分明细 | `artifacts/hit_rate_test_v3/final_scored.json`、`summary.json` |
| 四轮对比 | `artifacts/hit_rate_test_v3/metrics_comparison.txt` |
| passage 重建 | `artifacts/hit_rate_test_v3/passage_rebuild.json` |
| harness 日志 | `artifacts/hit_rate_test_v3/harness_run.log` |
| 定向 pytest | `artifacts/hit_rate_test_v3/pytest_directed.txt` |
| 本报告 | `docs/evaluation/mcp-agent-knowledge-hit-rate-test-report-v3.md` |

### Migration / 重建 / 回滚

| 项 | 说明 |
| --- | --- |
| Migration | `alembic/versions/k001_retrieval_passages.py`（revises `j004_runtime_schema_parity`） |
| 重建命令 | `python scripts/rebuild_passage_index.py --out artifacts/hit_rate_test_v3/passage_rebuild.json` |
| 仅补向量 | `python scripts/rebuild_passage_index.py --embed-only --batch-size 16 --timeout 180` |
| 兼容字段 | MCP 仍返回 `knowledge_id` / `block_id`；新增可选 `passage_id`、`document_family_id`、`retrieval_unit`、`version_year` |
| 回滚 | `alembic downgrade j004_runtime_schema_parity`；配置 `rag.retrieval_unit=block` 可强制旧路径（hybrid 在 passage 表空时自动回退 block） |

### 复现命令

```bash
# 1) schema
python -m alembic upgrade head

# 2) passage 索引（生产 data/kb.db）
python scripts/rebuild_passage_index.py --batch-size 16 --timeout 180 \
  --out artifacts/hit_rate_test_v3/passage_rebuild.json

# 3) MCP
python run_mcp.py -t streamable-http --port 9000

# 4) Golden harness（勿覆盖前两轮目录）
python scripts/hit_rate_test_harness.py \
  --golden evals/golden_set_hit_rate.json \
  --out artifacts/hit_rate_test_v3

# 5) 评分与对比
set HIT_RATE_ARTIFACTS_DIR=artifacts/hit_rate_test_v3
python scripts/hit_rate_finalize.py
python scripts/hit_rate_compare.py \
  --baseline artifacts/hit_rate_test \
  --round1 artifacts/hit_rate_test_after_fix \
  --round2 artifacts/hit_rate_test_v2 \
  --round3 artifacts/hit_rate_test_v3 \
  --out artifacts/hit_rate_test_v3/metrics_comparison.txt
```

---

## 8. 放行结论

### **不通过放行**

本轮完成了 SPEC v3 的主工程目标——**独立 retrieval passage 索引**，并证明：

- 检索粒度从 ~19 字微块变为 ~650 字语义段；  
- Recall@5 / Top-1 达到放行线；  
- Ask Fact 从 34% 提升到 62.5%，但仍远低于 90%；  
- Hallucination / FP / Citation / E2E / P1 均未达标。

下一轮应在**不改 Golden、不降 gate** 的前提下，聚焦：  
(1) 直接作答提示与后处理；  
(2) KB-021 类弱特征召回；  
(3) 版本隔离的 generation 硬断言；  
(4) no_answer 纪律以压低 FP/幻觉。
