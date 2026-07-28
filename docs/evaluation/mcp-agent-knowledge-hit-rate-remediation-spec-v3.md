# MCP Agent 知识命中率第三轮修复 SPEC

> 状态：待实施  
> 依据：`docs/evaluation/mcp-agent-knowledge-hit-rate-test-report-v2.md`、`artifacts/hit_rate_test_v2/final_scored.json`、`artifacts/hit_rate_test_v2/metrics_comparison.txt`  
> 范围：修复 MCP `search` / `ask` 的知识命中、证据完整性和版本一致性；不得修改 Golden Set 的预期答案或通过放宽评分规则掩盖问题。

## 1. 结论与已确认根因

第二轮结论应维持为**不通过放行**。向量覆盖率已从 0 恢复至 100%，但这不是可放行证据：当前向量实际覆盖的是错误粒度的数据单元。

| 已核验事实 | 证据 | 影响 |
|---|---|---|
| `blocks` / `knowledge_chunks` 共 128,348 条，平均文本长度约 **19.3** 字，最大仅 247 字；68,264 条短于 20 字 | 只读检查当前 `data/kb.db` | 关键条件、数值、标题与结论被拆在不同原子块中，单块不能回答问题。 |
| `src/services/file_graph.py::_rebuild_page_cache()` 将每个图谱 `Block` 原样写入 `blocks` 和 `knowledge_chunks`，并直接为它嵌入 | 代码与库中数据一致 | 图谱结构原子错误地兼任了语义检索 passage。 |
| 回答上下文对原始行只取前 8 条、每条前 800 字；相邻扩展窗口只有 1 | `src/answering/assembler.py`、`src/retrieval/canonical_snapshot.py` | 相邻一块不足以复原被过度切碎的制度条款。 |
| KB-002 的实际证据仅含“支付账”“交易额度较已实”等截断片段；KB-037 同时混入 2023 与 2026 版本 | `final_scored.json` 的 MCP 原始返回 | 可直接解释数值缺失和旧版“一级/二级”幻觉。 |

**根因：检索表示层混淆了“图谱结构块”和“供 RAG 检索的语义段落”。** 第二轮只补齐了这些微块的 embedding，未重建语义 passage，因此不能通过继续调 `top_k`、相邻窗口或 evidence gate 来根治。

同时存在三个次级问题，必须随本轮处理：

1. 版本过滤按标题归类不稳定，未在真实文档族/段落谱系上保证“最新版本优先”；KB-037 因而将旧版本送入生成上下文。
2. 数值事实保护在“主语解析不完整”时会把已有依据的正常回答降级为 `no_answer`（KB-010）；保护策略应校验每个断言是否可追溯，不能要求未经证实的单一主语锚点。
3. `hit_rate_finalize.py` 的缺陷分类未覆盖“`Recall@5=false` 但答案碰巧正确”的失败（如 KB-007、KB-023）；这会漏报 P1 检索问题。该修复只提升观测准确性，**不得改变指标口径**。

## 2. 本轮目标与非目标

### 2.1 目标

建立独立、可重建、可追溯的 **retrieval passage** 索引，使混合检索和生成证据都基于完整语义段落，而图谱块只继续承担结构、阅读定位和细粒度溯源职责。

完成后，`search` 与 `ask` 必须从同一 canonical evidence snapshot 获取检索 passage；`ask.sources` 必须能够回溯至 passage、知识条目、原始块范围以及实际使用的版本。

### 2.2 非目标

- 不修改 `docs/evaluation/*golden*` 中测试问题、`required_facts`、`forbidden_facts`、预期知识 ID 或放行阈值。
- 不以降低 evidence gate 阈值、盲目增加 `top_k`、关闭数值/版本保护作为主修复。
- 不把图谱 `blocks` 重写为大段文本；这会破坏现有图谱关系、`read` 定位和引用稳定性。
- 不将仅因格式差异导致的答案视为正确，除非评分器采用对所有用例一致、可审计的机械归一化（空白、全半角、中文/阿拉伯数字等）。

## 3. 实施要求

### A. 新建独立的语义检索 passage 层（P0）

1. 设计 `retrieval_passages`（名称可等价）及其 FTS、向量映射。新增表或字段必须通过 **Alembic migration** 创建；不得在运行时临时建表。
2. 一条 passage 必须至少包含：

   - 稳定 ID、`knowledge_id`、`document_family_id`、`source_version`（若可获得）、`passage_index`；
   - `text`：带文档标题和层级标题前缀的可独立理解语义文本；
   - 原始 `block_id` 列表及每个块的字符/顺序范围，供 `read`、引用和审计回溯；
   - 生效/废止/发布时间等现有 freshness 元数据的投影。

3. Passage 构建规则：

   - 先按 Markdown 标题、段落、列表/表格行等自然边界合并；不能简单按图谱块逐条嵌入。
   - 目标正文长度 400–1,000 个中文字符；跨段时保留 100–180 字重叠。短标题、表头、附件名不可单独成为可检索 passage，须与其正文或上下文合并。
   - 对表格、金额、期限、处罚标准等密集事实，确保“适用条件 + 数值 + 单位 + 结论”位于至少一个同一 passage 中。
   - 无法达到最小长度的末尾内容可与前文合并；确实独立且有意义的短条目可保留，但必须标记 `short_passage=true`，并在质量统计中单列。
   - 构建算法必须是确定性的：同一源文件无变更时，重复 `reindex_all` 生成的 passage ID、数量及文本哈希一致。

4. 保留 `blocks`、已有图谱关系及其 ID；不得改变 `kb://knowledge/{id}`、`read` 和现有历史引用的兼容行为。新增 `read` 或来源序列化字段时，保持旧字段可用。
5. 为 passage 提供专用的删除、软删、更新和重建路径。文档更新、删除、重建时，passage 的 FTS 与向量记录必须与来源文档原子性同步清理，避免孤儿命中。

### B. 将混合检索迁移到 passage（P0）

1. 向量检索、关键词/FTS 检索、RRF/融合、重排和 canonical snapshot 的候选对象统一改为 passage，而不是 `blocks` 或当前按 block 镜像的 `knowledge_chunks`。
2. 检索结果中必须包含：`passage_id`、`knowledge_id`、标题/章节、文本、分数、版本信息、来源 block 范围。对外 MCP 可保持既有字段，同时新增可选追踪字段；不得让客户端失去现有 `knowledge_id` 引用。
3. 结果去重采用“同文档同章节优先保留最佳 passage + 多样性补位”而非在文档级过早折叠。默认候选池应足以覆盖同一制度的相邻条件和例外条款，随后再生成 Top-5 展示结果。
4. `SearchService`、`AnswerService` 与 `canonical_snapshot` 使用同一 passage 选择和相同 freshness/version policy。不得出现 `search` 的正确 passage 未被 `ask` 看到的双轨检索。
5. 不得为了通过 KB-017、KB-021 等无候选用例直接降低 gate。先确认查询改写、关键词召回和 passage 候选池中存在相应语义 passage；gate 仅对完整候选池作判断。

### C. 证据打包、答案生成与数值保护（P0）

1. 生成上下文改为由 canonical snapshot 的 passage 构成的 **evidence packet**，而不是对前 8 个原始微块截断拼接。每个 packet 保留标题、版本、段落文本和可引用来源。
2. 证据包选择必须覆盖问题中的关键槽位：主体、行为/条件、数值/期限、结论以及版本。不得只因某段总体分高就丢掉同文档内包含数值或例外条件的 passage。
3. 回答提示和后处理必须遵循：

   - 仅陈述 evidence packet 明示的事实；每个关键事实可对应至少一个 returned source；
   - 证据不足时明确说明缺失的事实，而非以相邻政策、历史版本或常识补写；
   - 先直接回答问题，再给必要限定，避免用冗长“未找到”掩盖已命中的直接答案。

4. 重构 `numeric_fact_guard`：

   - 继续拒绝 evidence 中不存在的数值，或数值与适用条件错配的断言；
   - 对问题中的每个明确条件（例如“涉诈”“涉骚扰”）分别做条件—数值锚定，而非强制推导一个“主语”；
   - 当回答的数值和限定语可直接在 passage 中验证时，即使启发式主语未解析，也不得降级为 `no_answer`（回归 KB-010）；
   - guard 的每次剥离/拒答必须记录命中的 passage ID、断言、规则名和原因，便于 artifacts 审计。

### D. 版本族与新旧版本隔离（P0）

1. 引入可审计的 `document_family_id`。其生成应优先使用文档的稳定元数据、文号/业务主题，标题归一化只能作为回退；记录归类依据和置信度。
2. 对标记为“本地最新版”的问法，candidate、evidence packet、最终 `ask.sources` 必须只来自该族的最新有效版本；除非用户明确要求历史版本或来源标记为“版本不可判定”。
3. 在 passage 层执行版本过滤，并在最终生成前再断言：`raw_evidence_used`、answer 引用和 sources 都不含被排除版本的 passage/block。
4. 为 KB-037 所属制度建立精确回归：2026 版本答案不得出现 2023 版本的“一级/二级”等遗留术语；追踪日志应能显示两个文档被归入同一 family 及旧版本被排除的原因。

### E. 索引重建与可观测性（P0）

1. 提供受支持的 migration / `reindex_all` 路径，按以下顺序执行：创建 schema → 构建 passage → 生成 FTS / embedding → 校验计数与哈希 → 原子切换读取端。失败时应继续读取旧索引或明确失败，不能留下半新半旧索引。
2. 为批量重建提供 checkpoint、幂等恢复和进度/错误统计；原有 `artifacts/hit_rate_test*` 不得覆盖。若要清理旧 passage，只清理已确认属于该独立索引的记录，并保留可恢复备份/事务边界。
3. `kb_capabilities`、`kb://stats` 或诊断命令中增加最少以下字段：

   - `retrieval_index_unit: "passage"`；
   - passage 总数、已嵌入数量、FTS 数量、向量覆盖率；
   - `avg/p50/p95` passage 字符长度、`short_passage` 数量；
   - 与 `blocks` 的数量分开呈现，禁止再以 block 向量覆盖率代表检索索引健康度。

4. 索引质量硬校验（针对可检索的非短 passage）：平均长度 >= 300 字、p50 >= 250 字、p95 <= 1,300 字，向量覆盖率和 FTS 覆盖率均为 100%。如源数据确有例外，必须输出按文档的异常清单并得到测试报告说明，不能静默放行。

### F. 评测脚本与缺陷归因修复（P1，但本轮必须交付）

1. 维持 v2 指标定义与 Gate 不变，尤其是事实正确性仍只评分 `ask.answer`，不得从 `sources` 推断答案正确。
2. 修正 `scripts/hit_rate_finalize.py::classify_defect`：每个 `recall5=false` 的可回答用例都必须至少列为 P1 `retrieval_recall`，包括答案文本碰巧覆盖 required facts 的情况（KB-007、KB-023）。可同时记录次级问题，但不得漏记。
3. 在评分输出增加、但不取代既有指标的诊断字段：

   - `expected_doc_recalled`、`answer_fact_correct`、`source_trace_valid`、`e2e_pass` 的组合计数；
   - passage ID / document family / version 的命中轨迹；
   - “评分器无法判定”的明确状态。不得将无法判定默认为通过。

4. 仅允许通用、无人工偏向的文本归一化；所有归一化规则和命中前后文本均须写入 `final_scored.json`。不可为某一 Golden case 添加专属同义词白名单。

## 4. 必须新增或更新的测试

### 4.1 单元与集成测试

- Passage builder：标题继承、段落合并、重叠、表格/数值完整性、稳定 ID、更新/删除清理、空文档和超长文档。
- 检索后端：向量、FTS、融合、重排均返回 passage；同文档多段多样性；`search` 与 `ask` 使用同一 snapshot。
- 溯源：每个 returned passage 可映射回准确的 block 范围和知识条目；旧 MCP 字段兼容。
- 版本：同制度 2023/2026 版本必须归到同一 family；“最新版”请求中旧版本不能进入 evidence packet、`raw_evidence_used` 或 sources。
- 数值保护：有完整证据时正常作答、数值不存在时拒答、多条件数值不串值、主语启发式缺失时不误拒答（覆盖 KB-010 形态）。
- 评测分类：`recall5=false + ask_fact_correct=true` 仍输出 P1；所有 answerable case 都有明确归因或明确“无缺陷”。

### 4.2 Golden 回归

对 v2 中所有未 E2E 通过的 22 个可回答用例逐个执行 `search` 与 `ask` 原始调用并保留 artifacts，至少包括：

- 召回/空候选：KB-007、KB-017、KB-021、KB-023；
- 事实完整性/截断：KB-001、KB-002、KB-006、KB-009、KB-010、KB-012、KB-013、KB-014、KB-015、KB-016、KB-018、KB-022、KB-024、KB-028、KB-035、KB-036；
- 版本隔离：KB-037。

其中 KB-002 必须在一个 evidence packet 内展示账户类别与对应额度的完整原文；KB-037 必须展示版本族决策；KB-010 必须展示 numeric guard 未误拒答的审计记录。

## 5. 验收与放行门槛

实施 agent 必须执行一次全量、不可复用旧评分结果的 v3 测试。新 artifacts 只能写入：

```text
artifacts/hit_rate_test_v3/
docs/evaluation/mcp-agent-knowledge-hit-rate-test-report-v3.md
```

报告必须附带 v1、v2、v3 三轮对比，以及 passage 健康度快照。下表全部满足才可写“通过放行”：

| 指标 | 最低要求 |
|---|---:|
| Top-1 Accuracy | >= 75% |
| Recall@5 | >= 88% |
| Ask Fact Correctness | >= 90% |
| Ask Citation Validity | >= 95% |
| E2E Pass Rate | >= 90% |
| Hallucination Rate | <= 5% |
| False Positive Rate | <= 5% |
| retrieval passage 向量覆盖率 | 100% |
| retrieval passage FTS 覆盖率 | 100% |
| P1 缺陷 | 0 |

附加验收：

1. 不得仅报告 `blocks` 向量覆盖率；必须报告独立 passage 指标并满足第 3.E.4 节长度校验。
2. 37 个 Golden case 逐条可追溯；所有失败都有原始 MCP 输入输出、passage trace、缺陷归因和复现命令。
3. 既有测试不得新增失败。若全量测试存在既有失败，报告须给出修复前基线、修复后结果和与本改动的相关性；不得以“22 个无关失败”替代新增测试的通过证明。
4. 禁止删除或改写 `artifacts/hit_rate_test/`、`artifacts/hit_rate_test_after_fix/`、`artifacts/hit_rate_test_v2/` 及前两轮报告。

## 6. 交付清单

1. Alembic migration、passage 建模/构建/清理/检索实现及兼容性调整。
2. 完整自动化测试与针对上述 Golden 失败用例的回归测试。
3. `artifacts/hit_rate_test_v3/`：原始 MCP 交互、索引健康度、重建日志、`final_scored.json`、`metrics_comparison.txt`、失败分类明细。
4. `docs/evaluation/mcp-agent-knowledge-hit-rate-test-report-v3.md`：环境、数据版本、实施内容、根因验证、指标对比、未通过项（如有）、放行结论和复现命令。
5. 提交说明须列出 migration revision、数据重建命令、受影响的 MCP/REST 兼容字段和回滚方式。

## 7. 实施顺序（不可跳过）

1. 先补 passage schema 与构建器测试，并输出当前/新索引粒度对比；此时不要切换线上读取路径。
2. 完成增量与全量 passage 构建、FTS/向量写入、删除同步和健康度校验。
3. 切换 hybrid retrieval 与 canonical snapshot 到 passage，完成 search/ask 一致性和溯源测试。
4. 修复版本族、evidence packet 与 numeric guard，完成 KB-010、KB-037 等定向回归。
5. 修复评分分类和诊断输出；运行全量项目测试。
6. 在干净、可复现的索引上重新跑 Golden v3；未满足全部 Gate 时，明确写“不通过放行”，不得以局部改善宣称完成。
