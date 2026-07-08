# Canonical Wiki V2 Claim 语义黄金评测集（Phase 3.5 / C2）

> 把 Claim 语义准确率变成 Phase 4 的强制门禁。覆盖中文知识库高风险场景。
> 纠偏方案：`docs/superpowers/plans/2026-07-08-canonical-wiki-v2-correction-and-continuation.md` §C2

## 数据集

| 文件 | 用途 | 确定性测试 | 真实模型评测 |
|---|---|:---:|:---:|
| `claim_matching.jsonl` | matcher merge action 分类准确率 | ✅ `tests/test_wiki_v2_golden_eval.py` | ✅ `run_wiki_v2_semantic_eval.py` |
| `claim_merge.jsonl` | merge engine 行为(supports/contradicts/supersedes/unresolved) | ✅ 同上 | — |
| `claim_extraction.jsonl` | extractor 抽取(fake LLM 注入响应) | ✅ 同上 | — |
| `source_update.jsonl` | 来源更新失效传播 | ⏳ Phase 5 rebuild 实现后启用 | ⏳ |
| `source_delete.jsonl` | 来源删除失效传播 | ⏳ Phase 5 rebuild 实现后启用 | ⏳ |

### 字段约定（claim_matching.jsonl）

- `new` / `candidates[]`：`s`=statement, `sub`=subject_refs, `pred`=predicate, `obj`=object_refs, `vf`/`vt`=valid_from/to, `stance`=evidence stance
- `scores`：注入的相似度（确定性，零 embedding 依赖）
- `expected_action` / `expected_codes`：C1 契约（`docs/architecture/wiki-v2-claim-merge-contract.md`）下的期望 action + reason code
- `xfail`：当前 matcher 未满足保守契约的 case（记录 gap，测试标 `pytest.xfail`，由本评测集驱动后续增强）

## 场景覆盖（纠偏方案 §C2 要求）

✅ 已覆盖：完全相同/数值冲突/单位不同(Mbps vs Gbps)/型号不同/地区不同/时间更新/补充限定/否定表达/强度词(最高可达 vs 保证达到)/低置信/同义不同文本/中等灰区。

⏳ 待补（真实模型评测阶段）：同一 Claim 多来源/一个来源含支持+限制/OCR 噪声/表格 vs 正文数值/相同文本不同作用域/不同文本语义等价。

## Phase 4 最低门槛（纠偏方案 §C2）

| 指标 | 最低要求 |
|---|---:|
| Evidence 包含有效 knowledge_id | 100% |
| 可定位 block 时 block_id 完整率 | 100% |
| Evidence source_revision 完整率 | 100% |
| `supports` precision | ≥ 95% |
| `refines` precision | ≥ 95% |
| `contradicts` precision | ≥ 98% |
| `supersedes` precision | ≥ 98% |
| duplicate Evidence 写入次数 | 0 |
| 错误自动合并率 | ≤ 1% |
| 无法确定时进入 unresolved | 100% |
| Published Page 引用非法 Claim | 0 |

> 对 `contradicts` 和 `supersedes`，宁可降低召回，也不得降低精确率。

## 确定性测试

`tests/test_wiki_v2_golden_eval.py`：加载 `claim_matching.jsonl` + `claim_merge.jsonl` + `claim_extraction.jsonl`，用注入 scores / fake LLM / 固定 clock 跑，断言 action / reason_codes / merge result。xfail case 标记当前 matcher 保守性 gap。**CI 可跑（零 LLM/embedding 依赖）。**

## 真实模型评测（非阻断）

```bash
python evals/run_wiki_v2_semantic_eval.py            # 用真实 embedding 跑 matching
python evals/run_wiki_v2_semantic_eval.py --json     # 机器可读报告
```

输出：extraction precision/recall、action confusion matrix、各 action precision、unresolved rate、false merge rate、LLM 调用次数。**不阻断 CI**，用于真实数据验证语义准确率。

## 已知 gap（xfail，待增强）

当前 matcher 无数值/单位/型号/地区/否定/强度词细粒度解析，以下场景当前判错，应回落 `unresolved`：

- m03 单位不同(1Gbps vs 1000Mbps)→现 contradicts
- m04 型号不同→现 supports
- m05 地区不同→现 supports
- m08 否定表达→现 contradicts
- m09 强度词(最高可达 vs 保证达到)→现 supports

这些由本评测集驱动，在 C2 后或 Phase 4 shadow 阶段用真实数据逐步收紧（契约 §5 保守复核）。
