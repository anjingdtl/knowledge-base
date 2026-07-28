# MCP Agent 知识命中准确率第二轮修复 SPEC

> 本 SPEC 是对第一轮修复的验收复查补充，依据：
>
> - `docs/evaluation/mcp-agent-knowledge-hit-rate-test-report-after-fix.md`
> - `artifacts/hit_rate_test_after_fix/final_scored.json`
> - `artifacts/hit_rate_test_after_fix/metrics_comparison.txt`
>
> 本轮目标不是继续“改善报告数字”，而是使真实 MCP Agent 的 `search`、`read`、`ask` 证据链一致、可验证，达到首次 SPEC 的最低放行线。

## 1. 当前状态与禁止放行条件

当前复测仍**不通过**，不得作为生产 Agent 放行依据：

| 指标 | 当前值 | 最低放行线 | 状态 |
| --- | ---: | ---: | --- |
| Top-1 Accuracy | 84.38% | >= 75% | 通过 |
| Recall@5 | 87.50% | >= 88% | 未通过 |
| Answer Groundedness | 84.38% | >= 90% | 未通过 |
| Citation Validity | 63.89% | >= 95% | 未通过 |
| Hallucination Rate | 6.25% | <= 5% | 未通过 |
| False Positive Rate | 0.00% | <= 5% | 通过 |

此外，运行时 `vector_index.coverage=0.0`（128,348 blocks、0 embeddings）。在向量索引恢复并完成同库复测前，任何“语义召回已修复”的结论均不成立。

### 本轮阻塞问题

1. KB-017、KB-021：检索仍为空，当前环境无法依赖向量语义召回。
2. KB-019：III 类账户的 20 万元仍无法回答；限额类问题的最终答案仍不合格。
3. KB-037：最新修订版问题仍混入旧版“一级/二级竞赛”表述。
4. KB-007、KB-023：`search` 已退化为错误候选，但 `ask` 自行检索到正确资料，说明两条链路没有真正共用证据。
5. 104/288 条最终引用的 `knowledge_id` 不在 Golden Set 对应的预期知识集合中；不能将其一概解释为“相邻上下文”。
6. 当前评分脚本将 search/read/ask 的文本混合用于事实判定，无法反映最终用户看到的 `ask.answer` 是否正确。

## 2. 本轮范围与非目标

### 2.1 必须实施

- 重建并验证测试库的向量索引；
- 让 `search` 与 `ask` 真正使用同一份规范候选和同一份证据判定；
- 修复最终来源引用的可追溯性；
- 将相邻 block 上下文扩展接入实际 AnswerService 生成路径；
- 对“主体 + 数值”结论进行事实锚定；
- 修复本地“最新版本”检索中的旧版污染和排序覆盖；
- 改造评分，分离检索成功与最终回答正确性；
- 运行完整 Golden Set 复测并更新报告。

### 2.2 明确禁止

- 不修改 `evals/golden_set_hit_rate.json`、基线 artifacts 或修复后 artifacts 来提高指标；
- 不把 P1/P2 重命名为“OK”掩盖最终答案错误；
- 不以降低全局门槛、关闭 evidence gate、返回无根据候选或扩大 Top-K 来换取分数；
- 不将 AnswerService 临时检索到的任意来源直接加入“已接受证据集合”；
- 不在没有重建向量索引的情况下，以单元测试假数据宣称 KB-017/021 已修复；
- 不物理删除原始知识文档或重写业务正文。

## 3. 阶段 0：保存证据、复现与环境修复

### 3.1 冻结现有证据

下列目录只读保留，不得覆盖：

- `artifacts/hit_rate_test/`
- `artifacts/hit_rate_test_after_fix/`

本轮输出必须使用新目录，例如 `artifacts/hit_rate_test_v2/`。

### 3.2 重建向量索引

1. 在执行前记录：知识条目数、block 数、向量数、Embedding 模型、索引时间和当前 `kb_capabilities.runtime_diagnostics.vector_index`。
2. 使用项目正式支持的索引命令/工具重建向量索引；不要手工修改 SQLite 向量状态。
3. 重建后必须确认：
   - `vector_index.coverage >= 0.95`；
   - 向量数与可检索 block 数处于同一数量级；
   - `semantic_search` 对 KB-017、KB-021 的正式查询至少返回预期知识条目到候选集合；
   - 若重建失败，停止后续“已修复”结论，输出失败原因、服务日志和不涉及密钥的诊断信息。
4. 重建索引后不可复用旧服务进程；必须重启 MCP 服务或显式刷新其 Container/VectorStore，确保复测使用新索引。

## 4. 阶段 1：真实共享候选与证据判定

### 4.1 问题

当前 `_do_ask()` 虽然调用 `_retrieve_candidates()` 做前置探针，但随后调用 `ask_verified(container, question, top_k)` 时并未传入已接受候选。AnswerService 会自行检索，导致 KB-007 出现：

- `search`：只返回采购招标文件，正确安全生产文件不在 Top-5；
- `ask`：自行返回安全生产文件并回答。

这不满足“同一候选、同一评分、同一证据”的要求，也使 Agent 调用 `search` + `read` 与调用 `ask` 的行为相互矛盾。

### 4.2 实现要求

1. 建立可序列化的规范检索结果对象（可复用既有 `SearchExecution`，不重复新建平行模型），至少携带：
   - 原始 query 与扩展 query；
   - 唯一候选列表及稳定排序；
   - `knowledge_id`、`block_id`、标题、文本、通道、各阶段分数；
   - evidence gate 决定、阈值、接受候选 ID 集合；
   - 相邻 block 的允许扩展映射（如有）。
2. `search` 使用该对象输出候选；`ask` 必须将**同一个对象或等价不可变快照**传入 AnswerService/AnswerExecution。
3. AnswerService 不得针对同一问题再走另一套无约束检索；如业务架构必须内部检索，则其返回候选必须与传入快照做 ID、block、排序差异检查。存在差异时：
   - 默认只使用快照接受证据；
   - 新增证据必须重新经过同一 gate，并记录到 execution trace；
   - 不得静默混入。
4. Pre-LLM gate 失败必须不调用 LLM。Pre-LLM gate 通过后，不得再以不同输入字段重评分推翻同一证据。
5. `ask` 最终 sources 必须是“已接受候选”或“该候选的明确相邻扩展 block”的子集；不能先把 `raw_evidence_used` 加入 allowlist，再以该 allowlist 校验自身。

### 4.3 必须新增的测试

- 集成测试（非纯 fake relevance unit test）：给 `search` 和 `ask` 注入同一确定候选集，断言 AnswerService 收到的 `knowledge_id/block_id` 与 `search` 输出一致。
- 反例测试：AnswerService 返回一个不在预接受集合的来源时，最终回答必须降级 `no_answer` 或移除该来源及基于它的结论；不得把该来源加入 allowlist。
- KB-007：`search` 的 Top-5 和 `ask` 最终 sources 都必须包含 `acf5e2d6`；若 `search` 未命中，`ask` 不得以另一套未追踪检索“偶然答对”。
- KB-023：`search` 必须 Recall@5 命中 `16a152f8` 或 `940317f3`，`ask` 的“同等法律效力”引用须来自同一候选快照。

## 5. 阶段 2：引用完整性与版本证据隔离

### 5.1 实现要求

1. 删除“将 pipeline 的 `raw_evidence_used` / claims 自动加入 accepted set”的逻辑。
2. 为每个最终 source 保留：`knowledge_id`、`block_id`、来源通道、是否为相邻扩展、其父 hit block、接受时的 relevance score。
3. 引用完整性校验必须逐条执行：
   - source 的 `knowledge_id + block_id` 属于预接受证据；或
   - source 是同一 knowledge_id、且位于预声明邻接窗口内的扩展 block；
   - 否则 source 无效，相关回答不得输出。
4. 对本地版本问题，若已识别出最新有效版本：
   - 生成上下文默认只提供最新版本的证据；
   - 历史版本仅在用户明确要求对比时提供；
   - 若同时提供，必须标明“历史版本”，且不能把其金额、标准或适用范围合并为现行规则。
5. `rank_with_freshness()` 的年份排序必须在最终 relevance 排序之后生效，或引入独立 `ranking_score`；避免 2026 文档因 relevance 重算而被 2023 文档反超。

### 5.2 必须新增的测试

- 一条不在 allowlist 的 raw evidence 不能通过 citation integrity。
- KB-009：最终 answer 必须回答 Golden Set 的必需事实；不得声称“未检索到伙食补助”，不得使用旧版 80 元作为现行标准。
- KB-037：最新修订版本的 2026 文档必须排在 2023 文档之前；最终答案不得出现 `一级竞赛`、`二级竞赛` 两个禁止事实。
- 统计最终 ask sources：对 Golden Set，逐条输出预接受、相邻扩展、拒绝三类来源数量；不允许用“分母变大”解释未知来源比例下降。

## 6. 阶段 3：KB-019 的条款完整性与主体—数值锚定

### 6.1 问题

现有 `expand_adjacent_evidence()` 只是工具函数，未接入生产生成上下文；现有 `strip_unanchored_numeric_assertions()` 仅检查数值是否出现在任意 evidence 中，不能区分“II 类 10 万元”和“III 类 20 万元”。

### 6.2 实现要求

1. 在 AnswerService 的真实检索后、构建 LLM context 前，针对每个命中 block：
   - 按同一 `knowledge_id` 查询前后相邻 block；
   - 仅使用连续且可确认顺序（`order_idx` 或等价稳定顺序）的 block；
   - 追加到生成 context，并保留 parent hit 与 block 顺序；
   - 不跨 knowledge item 扩展。
2. 对包含明确主体的数值问题（如 II 类、III 类、区内、区外、代理商、自然月），使用 `select_numeric_fact_for_subject()` / `answer_value_is_anchored()` 的主体级校验，而非“数值在任意证据中出现即可”。
3. 若 answer 声称一个带金额、比例、时限等单位的值：
   - 该值必须存在于包含问题主体的同一条款/相邻扩展条款中；
   - 不满足时删除该结论；若核心问题仅剩无证据内容，则返回 `no_answer`；
   - 不允许输出“超过 10 万元”等由相邻类别数值推断出的未锚定结论。
4. 如相邻 block 在库中确实不存在，必须调查切分/索引路径；必要时调整分号并列、表格、编号条款的切分逻辑并重建索引。不能仅添加一个未调用的 helper 函数。

### 6.3 必须新增的测试

- 生产 context builder / AnswerService 集成测试：输入 II 类 10 万元与 III 类 20 万元分块数据，最终 LLM context 同时包含 III 类和 20 万元。
- 主体锚定反例：证据含 II 类 10 万元、III 类无数值时，回答不得返回 10 万元或“超过 10 万元”。
- KB-019 端到端：`ask.answer` 必须包含 `20万元`，必须不把 `10万元` 作为 III 类答案，最终 source 需可定位到 `27922ca4` 的正确 block。

## 7. 阶段 4：修复语义检索依赖与 KB-017 / KB-021

1. 先完成阶段 0 的向量索引重建，再判断查询改写和 gate 是否仍需调整。
2. 对 KB-017、KB-021，记录并输出以下 trace：原始 query、扩展 query、vector/FTS/rerank 候选、分数、gate 判定、最终传给 AnswerService 的 evidence。
3. KB-017 的正式查询必须命中 `51b17abe`，并由 `ask` 给出 `2000元/个`。
4. KB-021 的正式查询必须命中 `b40b8949`，并由 `ask` 给出 `1个工作日`、`5个工作日`。
5. 若向量覆盖率已达标但仍失败，才能继续调整 query rewrite、候选融合或 relevance 特征；每项调整须同时复测 KB-030--034，防止 False Positive 上升。

## 8. 阶段 5：评分与报告可信度修复

### 8.1 重构评分口径

评分脚本必须拆分以下字段，禁止将 `search`、`read`、`ask` 文本混合后判断“最终回答正确”：

| 指标 | 唯一数据来源 | 通过条件 |
| --- | --- | --- |
| Search Top-1 / Recall@5 | `search.data` | expected knowledge ID 的排名正确 |
| Read verification | 对 Agent 实际选择的候选调用 `read` | 原文包含对应必需事实 |
| Ask fact correctness | `ask.data.answer` | answer 本身覆盖所有 required facts，且不含 forbidden facts |
| Ask citation validity | `ask.data.sources` | 每条引用可回溯到预接受 evidence，且支撑相应结论 |
| E2E Pass | 上述四项 | 全部通过 |

不得因为 `read` 的全文、候选片段或 `raw_evidence_used` 含有必需事实，就将 `ask.answer` 标为事实正确。

### 8.2 报告要求

1. 报告必须准确写出 pytest 的实际 collected/passed/failed 数量；不能将参数化数或计划数写成实际通过数。
2. 报告需分别列出：
   - `search Citation Validity`；
   - `ask Citation Validity`；
   - 无效引用总数、按用例汇总、抽样明细；
   - `E2E Pass Rate`。
3. 报告不得将未达到指定最低线的指标描述为“有条件放行”。
4. 报告必须标出测试配置档、`wiki_serving_status`、向量覆盖率及索引重建时间；结果只适用于该配置。

## 9. 验收与复测

### 9.1 关键用例硬验收

| 用例 | 必须结果 |
| --- | --- |
| KB-007 | `search` Recall@5 命中 `acf5e2d6`；ask 依据同一候选回答“不少于5人” |
| KB-009 | 2025 版优先；ask 覆盖住宿与伙食补助必需事实，不混入旧版 80 元 |
| KB-017 | 命中 `51b17abe`；ask 给出 `2000元/个` |
| KB-019 | ask 给出 III 类 `20万元`，不把 II 类 `10万元` 写为答案 |
| KB-021 | 命中 `b40b8949`；ask 给出 `1个工作日`、`5个工作日` |
| KB-023 | `search` Recall@5 命中合同专用章文档；ask 只引用已接受证据 |
| KB-037 | 2026 版 Top-1；ask 不得出现旧版一级/二级分级表述 |
| KB-030--034 | 仍正确拒答，False Positive Rate 不得上升 |

### 9.2 指标放行线

所有指标必须同时达标：

| 指标 | 最低通过线 | 推荐目标 |
| --- | ---: | ---: |
| Top-1 Accuracy | >= 75% | >= 85% |
| Recall@5 | >= 88% | >= 95% |
| Ask Fact Correctness | >= 90% | >= 96% |
| Ask Citation Validity | >= 95% | >= 98% |
| E2E Pass Rate | >= 90% | >= 96% |
| Hallucination Rate | <= 5% | <= 2% |
| False Positive Rate | <= 5% | <= 5% |

任何资费、处罚、限额、合规、流程规则中的确定性错误答案，或关键事实缺失却返回确定性结论，均视为阻塞问题，即使汇总比例达标也不能放行。

### 9.3 执行命令与交付

修复 Agent 至少执行并在报告中粘贴真实摘要：

```powershell
pytest tests/mcp/test_hit_rate_regressions.py -v
pytest tests/stability/test_current_information_no_answer.py -v
pytest tests/stability/test_pre_llm_evidence_gate.py -v
pytest tests/services/test_relevance_gate_in_corpus.py -v
pytest tests/ -v
```

随后使用未改动的 Golden Set 对新索引执行真实 MCP 复测。输出：

1. 新 artifacts：`artifacts/hit_rate_test_v2/`；
2. 新评分明细与基线/第一轮/第二轮三方指标对比；
3. 新报告：`docs/evaluation/mcp-agent-knowledge-hit-rate-test-report-v2.md`；
4. 向量覆盖率和索引重建证明；
5. 每个未通过用例的原始 MCP 返回、根因和不放行说明；
6. 变更文件清单、测试实际结果及剩余风险。

## 10. Agent 执行纪律

1. 先完成阶段 0，未恢复向量索引不得进入“已完成修复”结论。
2. 每一处改动应映射到具体失败用例和自动化测试；不接受只修改报告或只添加 helper 的提交。
3. 所有最终回答、来源和评分都必须以用户实际看到的 MCP envelope 为准。
4. 若发现当前报告的归因或 Golden Set 有错误，只能在单独说明中提出证据；未经人工确认不得修改基线。
5. 最终结论只能是“通过放行”或“不通过放行”；如果有任一硬验收或指标最低线失败，必须写“不通过放行”。
