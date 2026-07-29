# MCP Agent 知识命中率第五轮修复与加速测试 SPEC

> 状态：待实施  
> 依据：`docs/evaluation/mcp-agent-knowledge-hit-rate-test-report-v4.md`、`artifacts/hit_rate_test_v4/`、`docs/evaluation/mcp-agent-knowledge-hit-rate-remediation-spec-v4.md`  
> 目标：一次解决 v4 的回答事实选择退化、表格条件—数值串配、错误根因覆盖，以及真实 MCP Golden 测试重复检索导致的长耗时。

## 1. 强制结论、范围与禁止项

v4 结论维持为**不通过放行**。第五轮不是“微调三元组”，而是将回答层改为统一的 **FactCandidate（可引用事实候选）** 管线，并在不牺牲最终验证完整性的前提下重构测试执行路径。

### 1.1 明确已验证的根因

| 根因 | 原始证据 | 影响 |
|---|---|---|
| 数值抽取对所有问题无条件执行，非数值问题也会先生成年份/文号等 numeric claim，进而跳过政策事实抽取 | v4 中 16 个 policy/text 类 answerable 用例事实正确数为 0；`claim_protocol.rule_extract_claims()` 先抽数值，只有无草稿时才抽政策文本 | KB-001/003/004/005/007/008 等大量正确召回后仍答错或答空。 |
| passage 的 `【文档】`、`【章节】` 元数据与正文混在一个 `text` 字段，参与数值/条件抽取 | KB-019 audit 将正文前的 III 类、标题年份等扩散到后续数值 | 文号、年份和不相关条件污染事实候选。 |
| `numeric_triples` 将整段 passage 再作为 fallback clause，段中所有条件会绑定到所有数值 | KB-017=涉诈 10000 元、KB-018=涉骚扰 2000 元、KB-019=III 类 10 万元 | 表格/多条款数值跨行、跨条件串配。 |
| passage 命中仍生成 block 邻接 allowlist；找不到 focus block 时会返回整篇文档的 blocks | KB-026 snapshot `adjacent_count=741`；`expand_adjacent_evidence()` 在 focus 不存在时返回全部 blocks | 性能急剧下降、allowlist 过宽、无关事实可能进入证据链。 |
| MCP harness 每例顺序 `search → read → ask`，而 ask 会重新检索相同 query | v4 37 例耗时约 73 分钟；KB-010 为 search 139s + ask 159s | 重复检索是主耗时，`read` 也未参与当前评分。 |
| 外层 ask 包装会把“answer validation 无 source”等结果覆盖为笼统 evidence gate 拒答 | KB-012/022/023/025 等 search snapshot 已 accept，但最终原因显示 `insufficient_relevant_evidence` | 失败归因失真，阻碍定位和优化。 |

### 1.2 禁止项

- 不修改 `evals/golden_set_hit_rate.json`、评分阈值、required/forbidden facts、预期知识 ID 或放行口径。
- 不按 `case_id`、Golden 题干、固定文档 ID 写答案、分支或白名单。
- 不降低全局 `rag.ask.no_answer_threshold=0.35`。
- 不回退、重做或覆盖 v3 已通过的 passage 索引工程；不得覆盖前四轮 artifacts 与报告。
- 不以“少跑几个 case”“复用不同代码/索引版本的旧结果”声明全量通过。
- 不在最终 answer 中输出推理过程、问题复述、长篇证据目录或自由生成的无来源解释。

## 2. P0 修复：统一 FactCandidate 回答管线

### 2.1 先分离 evidence metadata 与正文

`PassageEvidence` 必须保留 `document_title`、`section_path`、`version_year` 等 metadata，但新增/明确 `body_text`（或等价字段）作为事实提取唯一输入。

1. `【文档】`、`【章节】`、页码、文号标题、检索标签和 debug metadata 不得参与数值、条件、政策事实抽取。
2. source 对外仍可显示标题、章节和版本，但 `evidence_span` 必须指向正文坐标。
3. 旧 passage 的正文抽取必须可审计：输出 `body_char_start/end` 或 metadata prefix 的剥离规则；不允许静默按猜测删除正文。

### 2.2 逻辑事实记录（Logical Evidence Record）

在 passage 与回答之间新增内存级逻辑记录层，不要求重建检索索引：

```text
PassageEvidence
  └─ LogicalEvidenceRecord
       - record_id / passage_id / knowledge_id
       - type: paragraph | list_item | table_row | table_cell_group
       - body_text / normalized_text
       - source spans（正文精确范围）
       - table_id / row_index / column labels（表格时）
       - document family / version / section
```

规则：

1. 普通正文按标题下段落、句号/分号、列表项切分，保留足以表达条件与结论的完整句段。
2. 表格必须按**行**（必要时连同表头）形成记录；同一行的条件、对象、数值、单位可绑定。禁止以整篇 passage 作为表格 fallback。
3. 若 PDF/OCR 的表格已经扁平化且无法确定行/列关系：

   - 允许返回该段为 `unstructured_table=true`；
   - 不允许生成条件—数值事实；
   - 需要时返回 no_answer，而不是跨行猜配。

4. `focus_block_id` 无法定位时，任何 block 邻接函数必须返回空集合和 `focus_not_found` 审计；**绝不能返回整页 blocks**。

### 2.3 FactCandidate 的统一结构与抽取

从 Logical Evidence Record 产生统一候选，而不是“数值先行 + 政策兜底”：

```text
FactCandidate
  - candidate_id / record_id / passage_id / knowledge_id
  - fact_kind: policy | prohibition | responsibility | scope | relationship |
               numeric | deadline | version
  - subject / predicate / object / qualifiers
  - condition / value / unit（仅 numeric 或 deadline）
  - exact_text（原文可抽取片段，不含 metadata）
  - evidence_spans / table_row_ref
  - family/version freshness metadata
```

要求：

1. query planner 先识别意图和槽位，再决定抽取哪些 candidate。意图至少覆盖：制度规定/禁止、职责部门、适用范围、法律效力、数值限额/处罚/比例、时限、版本。
2. 非数值 query **不得**调用 numeric candidate 作为默认回答；数值 query 也不应排除同一问题所需的文字条件。
3. 对政策、职责、关系、范围类问题，优先选择含 query 核心实体与关系词的原文句段；允许轻量语序整理，但不得改变实体、否定词、数值或适用条件。
4. 对 numeric/deadline 问题，候选必须从同一 Logical Evidence Record（表格时同一行/同一表头上下文）生成，并附 `condition → value → unit`。
5. 同一问题存在多个 query 条件时，每个条件分别选一个最高质量 candidate；不得让一个条件的数值服务另一个条件。
6. Candidate 排序必须考虑：query 槽位覆盖、条件精确匹配、同一行/句段完整性、单位/谓词匹配、版本有效性、检索分数。不得只按“首次出现”。

### 2.4 覆盖规划、校验与渲染

1. 在渲染前执行 `answer_plan`：列出 query 需要的核心槽位与将用于回答的 FactCandidate ID。该规划仅依赖 query 和 evidence，不读取 Golden。
2. 计划至少覆盖 query 明示的主体、对象/条件、所问谓词和数值/期限（若有）。问题明确存在多个条件时，必须逐一覆盖；无法覆盖时结构化拒答或只回答已可确认的独立子问题并标明缺失，不得补写。
3. 每一个最终 bullet 必须来自一个或多个 candidate 的 `exact_text` / 受限模板，且可回溯至 passage 正文 span。禁止自由 LLM prose 直出。
4. 普通回答最多 3 个 bullets；但先完成 answer_plan 覆盖再渲染，不能因为先取三条而丢掉必要条件。
5. 版本问题只输出最新 family 的版本/文号 candidate；若具体“取消/变更”事实无直接候选，不能复述用户提出的旧规则术语。
6. 保留 `no_answer` 严格合同：没有已校验 candidate 时，`answer=""`、`sources=[]`、`raw_evidence_used=[]`；`user_notice` 与 answer 分离，且不回显 query。

### 2.5 失败原因不可覆盖

将失败状态拆分为可机读枚举，并从 AnswerService 原样保留至 MCP envelope：

- `retrieval_gate_rejected`
- `direct_slot_not_satisfied`
- `no_fact_candidate`
- `table_structure_ambiguous`
- `answer_plan_incomplete`
- `claim_grounding_failed`
- `passage_trace_failed`
- `citation_allowlist_failed`

外层 MCP 包装不得用 `insufficient_relevant_evidence` 覆盖已有具体原因。报告和评分 artifacts 必须同时显示 `retrieval_decision` 与 `answer_validation_decision`。

## 3. P0 修复：passage 路径的邻接与引用收口

1. `retrieval_unit="passage"` 时，canonical snapshot 不调用 `build_adjacent_allowlist(... list_blocks_fn=...)`，也不加载整页 block 列表。
2. 如业务确需添加上下文，只能通过 passage store 查询同一文档、相邻 `passage_index` 的至多前/后 1 个 passage，并单独标记 `passage_adjacent_extension=true`。
3. block fallback 仅在 passage 索引不可用时允许；必须有 `retrieval_fallback="block"`、错误原因和健康度记录。passage 健康度为 100% 时触发 block fallback 视为 P1。
4. `focus_block_id` 不存在的 block 邻接不得扩展全页，必须失败关闭。新增回归：一个无效 focus block 的返回长度为 0，且不能扩大 allowlist。
5. Source allowlist 对 passage 以 `accepted_passage_ids` 为主验证；block ID 仅用于 lineage 回溯，不能让同文档任意 block 因 `knowledge_id` 相同而成为允许来源。
6. 每个 snapshot 输出 `adjacent_unit`、`adjacent_count`、`adjacent_fallback_reason`。passage 模式下默认 `adjacent_count=0`；若非 0 必须可解释且不超过 `accepted_passage_count × 2`。

## 4. P0 修复：真实 MCP 测试加速且不降可信度

### 4.1 同一 query 的 snapshot 安全复用

新增可选的 MCP 兼容机制：

1. `search` 在响应 meta 中返回短时、不可猜测的 `evidence_snapshot_id`，关联不可变 canonical snapshot。
2. snapshot 绑定以下指纹：规范化 query、top_k、retrieval 配置哈希、模型/重排配置、索引 revision、数据库/知识库 revision、服务进程启动 ID。
3. `ask(question, evidence_snapshot_id=...)` 在所有指纹匹配时直接使用该 snapshot，**不得二次检索**；返回 `snapshot_reused=true`。
4. ID 缺失、过期、query 不一致、配置/索引变化或进程重启时，默认 ask 可安全重新检索，但必须返回 `snapshot_reused=false` 与明确原因；harness 的 final mode 要将这种情况记录为性能异常。
5. 保持没有该参数的既有 MCP 客户端完全兼容。
6. Snapshot 仅保留内存、会话/服务进程边界内的短 TTL（建议 10 分钟）和有限容量；不得将证据写入跨版本持久缓存。

### 4.2 Harness 执行重构

更新 `scripts/hit_rate_test_harness.py`，提供下列参数并保持旧调用可用：

- `--reuse-snapshot`：最终 Golden 默认启用；先 `search` 后以返回 ID 调用 `ask`。
- `--read-mode none|unique|each`：默认 `unique`。`read` 不再逐 case 执行；在全部 case 后对去重的实际 source/Top-1 知识 ID 做一次验证并记录映射。`each` 仅用于 read 工具专项回归。
- `--workers N`：每 worker 使用独立 MCP session、独立 message ID 和临时 artifact 文件，完成后原子写入目标文件；默认 1，release benchmark 可使用 2。
- `--manifest`：记录 git revision、配置哈希、索引/数据库 revision、服务启动 ID、Golden 文件哈希、参数、开始/结束时间。
- `--resume`：仅当 manifest 所有上述指纹完全一致才允许跳过已有 case；否则必须拒绝 resume，而不是静默复用旧交互。
- 每 case 记录 `search_ms`、`ask_ms`、`read_ms`、`snapshot_reused`、`retrieval_count`、错误原因，以支持性能归因。

### 4.3 并发与一致性约束

1. 先运行 workers=1 的 8 例基准，再运行 workers=2 的同一 8 例；只有不存在 SQLite 锁、超时、结果差异或服务资源异常时，才用 workers=2 跑全量。
2. 不能为了并发而共享 `MCPClient` / session。每 worker 独立初始化。
3. 全量并发前后必须比较每例的 search candidate ID、ask answer_mode、sources、评分结果；发现不一致则回退 workers=1 并报告，不能挑更好的一次。
4. workers=3 或更高只有在 workers=2 资源利用率和稳定性证明充分时才可启用；本轮不默认使用。

## 5. 分层测试策略

### Tier 0：快速确定性单元（每次改动）

目标：<= 3 分钟。

- metadata/body 分离；标题年份/文号不生成事实候选；
- 普通段落、列表、表格行、扁平化歧义表格的 Logical Evidence Record；
- 同行条件—数值—单位绑定；跨行拒绝；
- policy/responsibility/scope/relationship/numeric/deadline/version 的 query planner；
- answer_plan 覆盖与短答案渲染；
- no-answer 原因保留；
- passage 邻接无效 focus 返回空、passage 路径不访问 blocks；
- snapshot token 指纹/过期/配置改变/索引改变/会话隔离。

### Tier 1：真实 v4 证据回放（每次功能完成）

目标：<= 5 分钟，不调用 MCP 服务，不读取 Golden 作为运行输入。

使用 `artifacts/hit_rate_test_v4/` 的原始 passage 文本作为 fixture，覆盖：

- 政策/文本：KB-001、003、004、005、007、008、012、022、023、025、026、029、036；
- 数值/表格：KB-002、006、013、017、018、019、020、021、028；
- 版本/拒答：KB-032、035、037。

测试只向运行时提供 query 与 evidence，断言 candidate 的 source span、选择条件、禁止跨条件/跨行数值和结构化拒答合同。可使用 Golden 期望作**测试断言**，但不得进入生产代码。

### Tier 2：MCP 冒烟（每个阶段完成）

目标：<= 12 分钟。使用 `--reuse-snapshot --read-mode unique --workers 1` 运行以下高风险集：

`KB-001, KB-003, KB-005, KB-011, KB-012, KB-014, KB-015, KB-017, KB-018, KB-019, KB-021, KB-022, KB-023, KB-025, KB-026, KB-032, KB-037`。

该集用于验证协议、真实 source trace、统一候选、gate、版本和拒答；不得用其替代最终全量放行。

### Tier 3：全量放行（仅所有前置层通过后）

1. workers=1 完成 8 例基准，随后在 workers=2 稳定时跑全量 37 个 Golden case；保存全部原始 MCP 返回。
2. 目标同机环境总耗时 **<= 35 分钟**，相对 v4 的约 73 分钟至少降低 50%。未达到时仍可评分，但报告须单独写“性能验收未通过”及阶段耗时。
3. 跑完整 `pytest tests/ -q` 一次；先记录改动开始时基线，最终不得新增失败。所有本轮相关失败（answering、MCP ask/search contract、canonical snapshot、harness）必须清零。
4. 全量结果必须满足所有命中率 Gate 后才可放行；加速不能改变 37 例数量、评分脚本、Golden 文件或有效证据口径。

## 6. 定向验收

| 类别 | 必过验证 |
|---|---|
| 非数值政策问答 | KB-001/003/004/005/007/008 能返回正文支持的制度事实，不返回年份、文号或标题碎片。 |
| 跨文档关系/职责 | KB-012/022/023/025/026/029 能区分“检索已接受”与“answer plan 无事实”，不再伪报 gate 拒绝。 |
| 表格/数值 | KB-017=涉诈 2000 元且不含 30 元；KB-018=涉骚扰 30 元且不含 2000 元；KB-019=III 类 20 万且不含 10 万；KB-028 同时覆盖 15000 与 1200。 |
| 门禁与拒答 | KB-021 通过 direct-slot 但不降全局阈值；KB-030–034 均严格 no-answer、answer 为空。 |
| 版本 | KB-035、037 使用最新 family；无实际旧版 evidence 时不得标 version leakage；KB-037 answer 不含禁用旧规则短语。 |
| 邻接安全 | passage 查询不加载整页 blocks；无效 focus 不扩大 allowlist；任何单 case `adjacent_count` 不得异常膨胀。 |
| snapshot 复用 | 同一 query 的 fresh 与 reused 模式，在同一索引/配置下候选、evidence snapshot、answer/source 和评分结果一致；reused 模式 retrieval_count 少 1。 |

## 7. 放行与交付

新交付只能写入：

```text
artifacts/hit_rate_test_v5/
docs/evaluation/mcp-agent-knowledge-hit-rate-test-report-v5.md
```

报告必须包含：

1. v1–v5 指标对比、全部 37 例原始交互、`final_scored.json`、`metrics_comparison.txt`；
2. FactCandidate / Logical Evidence Record 的字段说明与每类 query 的数量统计；
3. KB-017/018/019 表格行审计（原文 span、row/cell、condition/value/unit）；
4. 被保留的 answer validation 原因和 retrieval decision 对照；
5. workers=1/2 基准、search/ask/read 耗时、snapshot reuse 命中率、retrieval_count、总耗时；
6. Tier 0–3 命令、开始结束时间、退出码、全量 pytest 基线与最终失败差异；
7. 未达任何要求时只能写**不通过放行**。

放行阈值维持不变：Top-1 >=75%、Recall@5 >=88%、Ask Fact >=90%、Ask Citation >=95%、E2E >=90%、Hallucination <=5%、False Positive <=5%、P1=0、成功 ask passage trace=100%、passage 向量/FTS 覆盖=100%。

## 8. 实施顺序（不得跳步）

1. 先为 v4 的 26 个失败建立按 query intent 分组的真实 evidence 回放测试，证明现有缺陷可以稳定复现。
2. 修正 passage 邻接 fail-closed 行为和 metadata/body 分离；先跑 Tier 0/1。
3. 实现 Logical Evidence Record、FactCandidate、query planner 与 answer plan；移除“numeric 先行、无草稿才政策”的控制流。
4. 完成表格行/列绑定；对歧义表格实施拒答而非全段 fallback。
5. 保留具体失败原因，修复 MCP wrapper 的原因覆盖。
6. 实现 snapshot token、harness reuse/unique-read/manifest；先以 workers=1 验证等价，再做 workers=2 基准。
7. Tier 0、1、2 全部通过后，执行 Tier 3 全量 Golden 和完整 pytest；未达到所有 Gate 时如实报告，不得放行。
