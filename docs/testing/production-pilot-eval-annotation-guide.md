# 生产试点评估数据集人工标注指南

## 目标

为 Recall / No-answer / Numeric / Routing / Citation 指标提供**可复核**的 ground truth。  
禁止将当前检索结果、LIKE 自动 top-N、或模型回答直接当作最终标准答案。

## 流程

1. **候选收集**：可从正式库标题/正文关键词检索得到候选（仅候选）。
2. **打开正文**：阅读 `knowledge_items.title` 与正文关键段落。
3. **判定相关性**：
   - `expected_ids`：主相关，标题或正文明确支持查询意图；
   - `acceptable_ids`：可接受替代，弱相关但合理；
   - `forbidden_ids`：明确误命中（如测试工件、单位混淆文档）。
4. **记录 corpus snapshot**：`kb.db` SHA256 前缀，写入 `corpus_snapshot_sha`。
5. **第二次复核**：另一轮检查 expected 是否仍成立。
6. **争议样本**：标记 `excluded_pending_review=true`，**不得计分**。

## 禁止

- PAD 样本进入准确率指标；
- `hit_or_empty` 计入 Recall/MRR/nDCG；
- `expected_ids` 为空却计满分；
- 用 search 反填最终 expected；
- no-answer 样本贡献 Citation 满分；
- 数字单位空结果默认通过。

## 数据集文件

| 文件 | 最低条数 | 分母规则 |
|------|---------|----------|
| `production_pilot_retrieval.jsonl` | 60 | 仅非空 `expected_ids` |
| `production_pilot_no_answer.jsonl` | 30 | 仅 `expected_no_answer=true` |
| `production_pilot_numeric_units.jsonl` | 25 | 有 expected_units / forbidden / expected_ids 或 no-answer |
| `production_pilot_routing.jsonl` | 40 | 全量路由样本 |
| `production_pilot_answer_citations.jsonl` | 25 | 有人工 fact + supporting IDs |

## 本轮标注说明

- 脚本：`scripts/build_production_pilot_datasets.py`
- 方法：正式库只读，标题+正文证据校验；非 search reverse-fill
- 排除：全部 PAD；全部 `hit_or_empty` 填充样本
- 产物摘要：`artifacts/production-pilot-final-validation/dataset-validation.json`
