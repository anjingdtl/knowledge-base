# MCP Agent 知识命中率第六轮修复与分层测试 SPEC

> 状态：待实施  
> 依据：`docs/evaluation/mcp-agent-knowledge-hit-rate-test-report-v5.md`、`artifacts/hit_rate_test_v5/` 及其原始 MCP 交互  
> 本轮目标：在不降低门禁、不重建 passage 索引、不修改 Golden 的前提下，修复“正确证据已召回但被跨文档/跨槽位错误选择”的回答链路；同时治理 search/rerank 的超时主耗时。  
> 最终放行仍以 37 例真实 MCP Golden 全量重跑为唯一依据。

## 1. 硬结论、边界与禁止项

v5 只能判定为**不通过放行**：Top-1=90.62%、Recall@5=96.88%，但 Ask Fact/E2E 仅 40.62%，Citation=78.12%，P1=19；全量约 41.8 分钟，未达到 35 分钟性能门禁。

本轮必须解决的是**选择和覆盖链路**，不是继续扩大候选数量，也不是针对失败题目写规则。

### 1.1 已确认的深层根因

| 根因 | v5 证据 | 造成的现象 |
|---|---|---|
| Candidate 直接跨所有召回 passage 排序，没有先确定权威文档/版本族 | KB-001、KB-036 已召回正确新文档，却混入旧文档候选并被渲染 | 回答来自检索结果中的错误文档或旧版本。 |
| `answer_plan` 只检查“存在某类候选”，不检查每个问题字段都由最终输出覆盖 | KB-004 被泛化政策句误判完成；KB-027 正确数值候选存在却排在前 3 条之后 | 计划显示 complete，实际 answer 仍缺 required fact。 |
| QueryPlan 将范围/对象词误当作互斥条件 | KB-002 的“个人”过滤掉账户类别事实；KB-028 不能同时表达总额和人均限额 | 多数值、范围、对象、条件被错误地互相排斥。 |
| 政策候选定位中存在固定高价值短语列表，且只做粗糙字面窗口匹配 | `src/answering/fact_candidates.py::_policy_from_record` 的 `high_value` | 违反禁止按 Golden 事实写规则，也无法泛化谓词、否定和关系。 |
| candidate ID 使用 Python `hash()`；部分 generation evidence 丢失 passage ID | `abs(hash(frag))` 会跨进程变化；KB-007 `passage_trace_failed` | 并发/重跑不确定，正确召回仍被来源校验挡住。 |
| evidence gate 的 direct-slot 判断只认识少量固定槽位，search 和 ask 的候选视图不完全一致 | KB-011/014/015：search 可见正确候选，ask 时门禁拒绝 | 正确证据被 gate 错杀。 |
| KB-010 的预期证据未进入 Recall@5 | 本轮唯一 Recall@5 失败，结果偏向历史近义主题文档 | 回答层无法弥补检索未召回。 |
| snapshot 已压缩 ask，但 search/rerank 超时成为主要耗时 | `search_ms_sum≈2087s`，日志多次 `Rerank timed out`；reuse 仅 30/37 | 性能不达标且每例都在重复等待不可用 reranker。 |

### 1.2 严禁事项

- 不修改 `evals/golden_set_hit_rate.json`、评分器、放行阈值、required/forbidden facts、预期知识 ID。
- 不按 `case_id`、Golden 题干、固定知识 ID、固定答案短语、失败清单或 `high_value` 词表写生产分支；生产代码不得读取 `evals/` 或 `artifacts/`。
- 不降低全局 `rag.ask.no_answer_threshold=0.35`，不放宽 no-answer、来源、幻觉或 FP 合同。
- 不覆盖 v1–v5 的 artifacts/报告，不重建或回退 v3 passage 索引工程。
- 不以 Tier 0/1/2、少量 case、旧结果或不同配置/索引的 resume 结果宣称全量通过。
- 不以“关闭 rerank”作为性能修复；只有通过同配置、同 Golden 子集 A/B 证明检索不退化的确定性 fallback 才可启用。

## 2. P0：先选权威证据组，再在组内选择事实

### 2.1 EvidenceGroupResolver

在 `FactCandidate` 选择前新增内存级 `EvidenceGroup`（名称可不同，但职责不可省略）：

```text
EvidenceGroup
  - group_id（稳定、可审计）
  - knowledge_id / document_family_id / document_revision / effective_date
  - passage_ids / retrieval ranks and scores
  - title, section, source authority metadata
  - query_anchor_coverage / predicate_coverage / freshness_score
  - group_score and rejection reasons
```

要求：

1. 先将 canonical snapshot 的 accepted passages 按**文档族 + 修订版本**分组；不能仅按标题文本或单一 `knowledge_id` 猜测版本关系。没有 family 元数据时，每个 `knowledge_id` 独立成组并记录 `family_unknown=true`。
2. 单文档问题先选择一个 `primary_group`，其评分至少纳入：检索分、query 核心实体/对象锚点、所问谓词锚点、章节匹配、来源权威性和时间有效性。不得把“最新”当作所有问题的默认偏好：只有 query 显式含现行/取消/替代/版本/年份等时间语义时才比较 revision/freshness。
3. 单文档问题的 answer candidate 默认**只能**来自 `primary_group`；若需要补充组，必须有缺失槽位、明确的同一实体锚点及独立的来源审计。不能因候选分数略高就混入另一文档。
4. 对显式多对象/多子问题的 query，planner 输出 `subqueries[]`；每个子问题独立选择一个 group 并完成覆盖。不能以一个文档的泛化句替代另一个子问题。
5. group 选择不确定（最高两组差距不足且无可验证关系/版本依据）时，保持 fail-closed：只回答单独完整覆盖的子问题，或 `answer_plan_incomplete` 拒答；不得混合拼接。
6. 在 ask 原始 trace 中写入 `evidence_groups`、`primary_group_id`、每组 score 构成、入选/淘汰原因、最终每个 candidate 归属 group。此 trace 不应暴露给最终用户回答。

### 2.2 稳定事实身份与来源闭环

1. `FactCandidate.candidate_id` 必须用 `sha256`（或等价稳定摘要）由 `passage_id + body_span + fact_kind + normalized_exact_text` 生成；禁止 Python `hash()`、随机 UUID 作为可比事实身份。
2. 每个 `LogicalEvidenceRecord`、`FactCandidate`、render bullet 都必须有非空 `passage_id`、`knowledge_id`、正文 source span。不得以 document title 或 `knowledge_id` 替代 passage provenance。
3. 将 generation/neighbor evidence 转换为 candidate 前执行 `passage_id` 完整性校验。缺少 passage ID 时：
   - 若可由 canonical snapshot 的唯一 source 映射确定，补全并记录 `trace_repaired=true`；
   - 否则排除该 evidence，记录 `missing_passage_id`，不得在后续 allowlist 中碰运气通过。
4. `passage_trace_failed` 只表示真实来源缺失；不得因内部字段丢失把已有正确的 accepted passage 变成最终 no-answer。新增 KB-007 类回归，验证修复后的 source 与 accepted passage 一致。

## 3. P0：用类型化槽位实现“计划即输出覆盖”

### 3.1 通用 QueryPlan，不得保留固定题库词表

QueryPlan 必须基于 query 解析为下列可组合的 typed slots；字段名可调整：

```text
subject / object / scope / selector / predicate / polarity /
condition / requested_attribute / value_dimension / unit / time_or_version
```

判定规则：

1. `scope`（如适用对象、个人/组织、业务类型）和 `selector`（如某类别/每人/总额）不是天然互斥的 `condition`；它们可同时限定一个数值记录。
2. `condition` 只表示触发条件、例外、上下文状态或明确的 if/when 语义。将自然语言对象词机械当过滤条件是缺陷。
3. 数值 query 可声明多个 `value_dimension`（例如总额、单项、人均、周期、比例）；planner 必须保留所有维度而不是只保留首个数值槽位。
4. 政策、禁止、职责、关系类 query 至少需要可验证的 subject/object/predicate/polarity 锚点。仅命中泛化政策句，不能满足具体禁止或职责问题。
5. 锚点由 query 自身、通用分词/同义改写和知识库 metadata 得出。允许通用领域归一化（简称、形态变化、同义谓词），但必须输出 `query_rewrite_trace`，且不得维护 Golden 专用事实或答案表。

### 3.2 candidate 匹配与覆盖矩阵

1. 每个 candidate 输出 `slot_match`：命中/未命中/未知，及其 exact span。匹配必须考虑实体、谓词、否定、范围、数值维度和单位，而非只看 `fact_kind`。
2. 构建 `coverage_matrix[required_slot][candidate_id]`。`answer_plan` 必须从该矩阵选择最小且同组一致的 candidate 集合，优先完整性、精确性、来源一致性，再考虑简短。
3. 对同一表格行可由一个 record 覆盖 scope + selector + 多个 value_dimension；禁止把相邻行或不同文档的数值拼成一个答案。
4. 对非表格的同一段落，必要时可选多个精确句段；跨 record 合并必须保持相同 subject/predicate/condition，且在 trace 中标记 `cross_record_merge=true`。
5. 找不到某个 required slot 时，`answer_plan_incomplete` 必须列出 `missing_slots` 与候选拒绝理由。只有已独立闭合的多子问题可部分回答；最终 answer 要标明无法确认的子项，不能编造。

### 3.3 渲染后的第二次验证

1. 渲染顺序由覆盖计划决定，不能按候选首次出现或“最多前三条”截断后丢失关键字段。可保留最多 3 条，但一条可含同一 record 中关联的多个明确值。
2. 每个 bullet 挂 `rendered_candidate_ids`，只使用 `exact_text` 或字段受限模板；不得自由扩写实体、否定、数值、版本或条件。
3. 渲染完成后重新从最终 bullet 抽取/核验 required slots，产出 `render_validation`。任何 required slot 未在最终文本中体现，则不得返回 `structured_claim_answer`。
4. policy localizer 删除固定 `high_value` 列表，改为 query-derived anchors + predicate/polarity + 句法/邻近关系评分。需要新测试证明任意未见实体/谓词也能通过该通用路径。

## 4. P0：统一检索门禁、修复 Recall@5 缺口

### 4.1 Canonical snapshot 是 search 与 ask 的唯一证据视图

1. 不带 `evidence_snapshot_id` 的 ask 也必须使用与 search 相同的 `RetrievalOrchestrator → canonical snapshot` 构建规则；不能额外走不同 top_k、不同 query rewrite 或不同过滤器。
2. 带 fresh snapshot 的 ask 只能直接消费其 accepted passage/candidate IDs，不得二次检索或悄悄替换候选。若不能复用，返回明确的 `snapshot_reuse_reason` 并重新生成完整 canonical snapshot。
3. `direct_slot_not_satisfied` 的计算改为调用 typed QueryPlan/candidate matcher；不得保留只覆盖少数固定字段的 slot 表。门禁阈值 0.35 不变。
4. 为 search/ask 写 `snapshot_fingerprint`、候选 passage IDs、candidate IDs、gate score 构成。相同 fingerprint 下二者的 accepted evidence 必须一致；不一致即测试失败。

### 4.2 检索改写与排序只做可泛化能力

1. 在检索前生成有限、确定性的 query variants：原 query、关键实体/谓词归一化、领域简称/全称展开、条件或对象拆分。每条 variant 必须从 query 或受控通用词典得来，并在 trace 中说明来源；设置上限（建议 4 条）防止召回噪声膨胀。
2. 用 variant 的 lexical/FTS + vector 结果融合，并在 rerank 前保留“实体 + 谓词”联合命中加分，避免历史同主题文档仅靠主题相似度压过精确制度条款。
3. 对时间/版本语义，增加 revision-aware rerank feature；不得通过固定年份、固定文件名或固定知识 ID 优待某个 Golden 文档。
4. 为 KB-010 类型的“口语表述 → 制度术语”建立**通用、可审计的同义检索回归**：fixture 使用独立于 Golden 的同义问法与两篇相似主题文档，断言精确谓词/对象的 passage 可进入 Top-5。生产词典不得写入该 case 的完整问答或知识 ID。

## 5. P0：性能修复必须保留检索质量

### 5.1 先测量再实施 rerank 熔断

1. 将每次 search 分解并记录 `rewrite_ms`、FTS/vector retrieval、fusion、rerank queue、rerank execution、timeout、fallback、serialization 等阶段耗时；`Rerank timed out` 必须带 query fingerprint 和超时类型。
2. 排查 timeout 后是否仍有后台 rerank 线程/请求占资源。必须真实取消、释放或受控隔离；不能只在调用方返回而让任务继续堆积。
3. 在连续超时、健康检查失败或队列饱和时，允许启用**进程内、短冷却期**的 deterministic hybrid fallback（现有 lexical/vector/fusion 分数 + 明确的锚点特征）。熔断状态、触发原因、冷却时间及每例 fallback 必须写 trace。
4. 熔断恢复后必须先做一次低风险 probe；成功才恢复 rerank。不得永久静默禁用 rerank。
5. snapshot reuse 未命中的 7 类/7 例必须逐例按 `missing/expired/fingerprint/query/config/index/process` 分类；可修复的实现缺陷必须消除。最终全量 reuse 命中应为所有可回答 search→ask 对，拒答类也必须说明为何不能复用。

### 5.2 质量护栏和性能验收

1. 在实施熔断前后，以同一服务启动、同一索引、同一 32 个 answerable cases 运行 search-only A/B；逐例比对 Top-1、Recall@5、预期 passage 是否进入候选。fallback 模式不得使基线 Top-1/Recall@5 下降，不得新引入预期证据掉出 Top-5。
2. 若 A/B 不满足，优化 timeout/资源/模型调用而非放宽 rerank 或全局关闭它。
3. 只在 workers=1 的结果完全稳定后才做 workers=2 的 8 例并发基准；结果比较至少含 snapshot fingerprint、candidate IDs、answer、sources、reason、评分。任何字段差异都不得使用 workers=2 跑放行全量。

## 6. 分层测试与执行顺序

### Tier 0：纯函数/单元，目标 <= 4 分钟

- EvidenceGroup 的 family/version 分组、单组选择、多子问题分组、歧义 fail-closed；
- candidate ID 跨进程/跨调用稳定；所有 candidate/bullet 的 passage provenance 完整；
- typed slots：scope/selector 与 condition 区分，多 value dimension，同一表格行多值覆盖；
- policy/禁止/职责/关系的 entity-predicate-polarity 匹配，且不依赖固定高价值词；
- coverage matrix、最小覆盖计划、渲染后缺槽拒答；
- search/ask identical snapshot、direct-slot 通用匹配、missing passage ID 修复/拒绝；
- rerank timeout 的取消、熔断、冷却 probe、确定性 fallback。

### Tier 1：v5 原始证据回放，目标 <= 8 分钟

只把 `artifacts/hit_rate_test_v5/` 中已保存的 query + passage/evidence 当 fixture 输入运行时；不得由生产代码读取 Golden。覆盖所有 19 个 v5 P1，并按以下风险簇断言：

| 风险簇 | 用例 | 核心断言 |
|---|---|---|
| 文档/版本串源 | KB-001、KB-009、KB-036 | primary group 正确；不从旧/异组拼接。 |
| 覆盖与政策谓词 | KB-004、KB-007、KB-012、KB-022、KB-023、KB-025、KB-027 | 最终 bullet 覆盖必要实体/谓词/否定/数值，且来源 trace 完整。 |
| 数值多维度 | KB-002、KB-006、KB-013、KB-020、KB-028 | 不误把 scope 当 condition；同 record 覆盖所需总额/人均/周期等维度。 |
| gate 与 snapshot 一致 | KB-011、KB-014、KB-015 | search/ask 同 fingerprint、正确 evidence 不被 direct-slot 错杀。 |
| 检索语义 | KB-010 | 通用改写或排序使正确类型 evidence 进入 Top-5；不以 case 规则实现。 |

测试可以引用 Golden 作为**断言**，但不得成为生产运行输入；每个回放必须保存/断言 `evidence_group`、`coverage_matrix`、`render_validation`。

### Tier 2：真实 MCP 高风险冒烟，目标 <= 15 分钟

先在干净服务进程运行：

```text
KB-001, KB-002, KB-004, KB-007, KB-010, KB-011,
KB-013, KB-015, KB-017, KB-021, KB-027, KB-028, KB-036
```

使用 `--reuse-snapshot --read-mode unique --workers 1`。必须同时检查原始 MCP envelope、source passage IDs、group/coverage trace、评分和性能分段；任一失败不得进入 Tier 3。

### Tier 3：质量 A/B、全量与回归（最后执行一次）

1. 先执行 rerank 质量 A/B（32 个 answerable search-only），输出逐例差异；通过后才启用熔断配置。
2. 执行 workers=1 与 workers=2 的同一 8 例基准。只有全字段一致时允许 workers=2；否则 workers=1 为唯一放行模式。
3. 用最终放行模式运行 37 例真实 MCP Golden，保存每例 search/ask/read 原始 JSON。不得 resume 旧轮结果。
4. 执行完整 `pytest tests/ -q` 一次，先记录开始基线，最终不得新增失败；本轮相关测试必须全部通过。

## 7. 验收标准

### 7.1 功能门禁（全部满足才可放行）

- Top-1 >= 75%、Recall@5 >= 88%、Ask Fact >= 90%、Ask Citation >= 95%、E2E >= 90%；
- Hallucination <= 5%、False Positive <= 5%、P1=0；
- 成功 ask 的 passage trace=100%，passage 向量/FTS 覆盖=100%；
- 仍不降低 `no_answer_threshold=0.35`，KB-030–034 仍严格 no-answer；
- 37/37 均为本轮原始 MCP 交互，Golden 和评分器哈希不变；
- 不存在 Golden/case/document/fact 固定分支，`high_value` 等题库式候选逻辑已删除。

### 7.2 特别回归门禁

- 正确新文档已被召回时，最终 answer 不得从无关旧文档取事实；
- final bullet 覆盖矩阵必须为所有 required slots 完整，不能只以 plan 内候选存在判定成功；
- 多数值问题能区分范围、选择器、维度、条件，绝不跨行/跨组拼接；
- 同一 fresh snapshot 的 search→ask 候选和来源完全一致；KB-007 类已知证据不再因内部 passage ID 丢失失败；
- 任何 rerank fallback 都保留 A/B 检索质量，且 trace 明确。

### 7.3 性能门禁

- 全量 37 例 wall-clock **<= 35 分钟**，相对 v4≈73 分钟降低 >=50%；
- 报告中必须分别给出 search、ask、read、rerank queue/execution/timeout/fallback 时长，snapshot reuse 命中/未命中原因和 retrieval_count；
- 不能以省略 read、跳过 case、改变 Golden、复用旧 artifact 或并发不确定结果换取时长。

## 8. 交付与报告

只能新建以下路径：

```text
artifacts/hit_rate_test_v6/
docs/evaluation/mcp-agent-knowledge-hit-rate-test-report-v6.md
```

`artifacts/hit_rate_test_v6/` 至少含：37 例原始 search/ask/read 交互、`final_scored.json`、`metrics_comparison.txt`、`manifest.json`、评分日志、pytest 基线/最终日志、Tier 0–3 日志、rerank A/B、workers 基准、snapshot 分类、group/coverage 审计摘要。

报告必须：

1. 如实写“通过放行”或“**不通过放行**”；任一门禁失败只能写后者；
2. 给出 v1–v6 指标和耗时对比，明确本轮与历史 artifact 的隔离；
3. 逐项报告根因修复证据：group 选择、typed slots、render validation、stable candidate IDs、trace 修复、gate 一致性、KB-010 类检索回归；
4. 给出所有 P1 的逐例归因及前后对比，而非只罗列通过列表；
5. 给出 rerank A/B 和 timeout/circuit-breaker 证据；如性能仍失败，明确是哪个阶段、多少毫秒，不能写笼统“环境慢”；
6. 给出完整命令、退出码、服务配置/索引/数据库/Golden 哈希，确保可复现。

## 9. 不得跳过的实施流程

1. **审计**：阅读 v5 原始 JSON 和当前实现；列出每个 P1 的 evidence group、slot、trace、retrieval/answer 决策，先写回放测试使问题稳定失败。
2. **收口证据**：删除 `high_value`，实现稳定 ID、passage trace 完整性和 EvidenceGroupResolver；完成 Tier 0/1。
3. **修正语义**：实现 typed slots、coverage matrix、渲染后校验；完成 Tier 0/1。
4. **统一检索**：修正 direct-slot 和 search/ask snapshot 一致性，添加通用 query variant/排序能力；完成 Tier 0/1。
5. **性能治理**：先采样定位，再实现可取消 timeout/circuit breaker；做 32 例 search-only A/B，不通过不得进入最终全量。
6. **真实协议验证**：Tier 2 全绿后，再做并发基准、37 例 Tier 3 和完整 pytest。
7. **交付**：只写 v6 artifact/report；保留前五轮；按真实结果结论。
