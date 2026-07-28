# MCP Agent 知识命中率第四轮修复 SPEC

> 状态：待实施  
> 依据：`docs/evaluation/mcp-agent-knowledge-hit-rate-test-report-v3.md`、`artifacts/hit_rate_test_v3/`、`docs/evaluation/mcp-agent-knowledge-hit-rate-remediation-spec-v3.md`  
> 目标：在不改 Golden Set、不降低全局 evidence gate、不重做 passage 索引的前提下，修复 `ask` 的受控作答、拒答、事实/数值校验与端到端 passage 溯源。

## 1. 放行结论与本轮边界

v3 仍为**不通过放行**，且不得描述为“基本通过”。检索表示层已实质改善：retrieval passage 为 6,456 条、平均 646.1 字、向量及 FTS 覆盖均为 100%，`Recall@5=93.75%` 已达线。因此，第四轮**不得**把主要精力投入以下事项：

- 再次重建或替换 passage 切分策略；
- 全局降低 `rag.ask.no_answer_threshold=0.35`；
- 调整 Golden Set、required/forbidden facts、预期知识 ID、指标计算或放行线；
- 用针对单个 KB 编号的固定答案、关键词白名单、例外分支通过评测。

本轮必须把问题收敛到回答层：`Ask Fact Correctness=62.50%`、`E2E=62.50%`、`Hallucination=12.50%`、`False Positive=20%` 与 `P1=13` 均不合格。

## 2. 已确认根因（以 v3 原始 MCP 返回为准）

| 根因 | 可复现证据 | 修复方向 |
|---|---|---|
| passage 仅在检索前段存在，AnswerService 最终又把它包装成 `candidate_type=raw_block`，且 `raw_evidence_used.passage_id=null` | KB-010/017/019/037 的 `ask` artifacts | 建立不可丢失的 Passage Evidence DTO，保证最终 source / raw evidence 均带 passage ID 和版本。 |
| LLM 输出“问题拆解、推理过程、建议”等长文，而非直接回答 | KB-017 混入涉骚扰 30 元；KB-019 混入 II 类 10 万元；KB-037 复述“一级/二级” | 改为约束式、短答案的 claim 生成和后验校验；不允许展示内部推理。 |
| numeric guard 先按文本剥离数值，未按事实槽位校验 | KB-019 的 evidence 含 III 类 20 万元，答案却删除 20 万、保留 II 类 10 万 | 数值校验改为“条件—数值—单位”三元组，仅保留和问题槽位匹配的断言。 |
| 无关证据仍可触发 `raw_only` 长文本回答 | KB-032 实际未编造地址，却未返回 `no_answer`，且复述了问题/禁用措辞 | 实行硬拒答契约与无查询回显的短拒答模板。 |
| evidence gate 对弱分但明确的组合条款产生假阴性 | KB-021 最高分 0.3 被 0.35 门禁拦截 | 保持阈值，新增可解释的“直接槽位证据”接受条件。 |
| KB-037 的回答失败被误归因为旧版本污染 | v3 的 raw evidence 与 sources 均只含 2026 文档；失败文本来自生成器复述问题、并声称证据不足 | 保留版本隔离硬断言，但把主修复放在输出限制与最新版文档事实选择。 |

### 2.1 指标解释约束

`Ask Citation Validity=84.38%` 的主要失分是多个被 gate 拦截的案例没有 sources；v3 的引用完整性统计中 `rejected=0`。`Citation Validity=73.91%` 则是“source 是否为 Golden 预期文档”的诊断比例。两者不可混写，更不得据此错误放宽引用完整性要求。

## 3. 必须实施的功能修复

### A. passage evidence 的端到端不可丢失契约（P0）

1. 定义一个内部 `PassageEvidence`（名称可等价）数据契约，至少包含：

   - `passage_id`、`knowledge_id`、`document_family_id`、`version_year`、`section_path`；
   - passage 原文、检索分数/通道、`block_ids` 与实际引用的 block 范围；
   - `is_family_newest`、是否相邻扩展、是否被 canonical snapshot 接受。

2. `_retrieve_candidates`、canonical snapshot、AnswerService、generation context、`raw_evidence_used` 和 `sources` 必须传递该对象或无损等价对象。禁止通过仅含 `block_id` 的 dict 重新包装 passage。
3. 对任何 `retrieval_unit="passage"` 的 `ask` 成功回答：

   - 每条 `raw_evidence_used` 必须有非空 `passage_id`；
   - 每条 source 必须有 `passage_id`，并可映射回 `block_ids`；
   - 输出的 `candidate_type` / `retrieval_unit` 必须真实反映 passage，不能标成 `raw_block`；
   - 若映射失败，必须返回显式内部错误或 `no_answer`，不能静默降级到 block 模式。

4. 旧 block 级兼容路径仅允许在 passage 索引确实不可用时启用；必须标记 `retrieval_fallback="block"`。本次测试数据库 passage 健康度为通过时，任何 fallback 都视为 P1。

### B. 受约束的直接回答协议（P0）

回答不能再由自由格式 LLM 文本直接成为 MCP 的 `answer`。实现“生成—校验—渲染”三段式协议：

1. **生成阶段：结构化 claim 草稿**

   - LLM（或规则提取器）仅输出严格 JSON/数据结构，不输出 Markdown 推理、问题拆解、检索过程、建议、表格或免责声明；
   - 每条 claim 必须包含 `text`、`evidence_passage_ids`、`fact_type`（numeric / policy / scope / version / other）和可选 `condition`；
   - 系统提示明确禁止复述用户问题、未被 evidence 支持的替代情形、比较对象、历史版本和计算推导；禁止输出 chain-of-thought；
   - JSON 无法解析、缺少 evidence ID、或证据 ID 不在 accepted snapshot 中时，视为生成失败，进入安全拒答，而非使用自由文本原样回退。

2. **校验阶段：claim-level grounding**

   - 每条 claim 只能引用其 `evidence_passage_ids` 的原文；文本、数值、单位、限定条件须可在同一 passage 或明确关联的相邻 passage 中验证；
   - 只允许保留与查询事实槽位有关的 claim。查询槽位由通用规则/模型从问题抽取，例如主体、行为/场景、对象、时间范围、数值类型、版本意图；不得读取 Golden 数据；
   - 删除所有未被支持、仅用于“解释为什么没找到”、或只是复述问题的 claim；删除后若没有一个直接回答 claim，则返回 `no_answer`；
   - 对 version/freshness 查询，所有保留 claim 的 `document_family_id` 必须一致，并为该 family 的最新有效版本（除非用户显式请求历史）。

3. **渲染阶段：短答案合同**

   - 普通事实问答：先给出不超过 3 个直接事实 bullet，随后仅可给 1 条必要限定；
   - 禁止标题“问题拆解”“推理过程”“知识库检索”“建议”“总结”，禁止复述问题全文；
   - 不输出“若你实际想问”“可能”“按此推算”“可参照但不等于”等未请求扩写；
   - 只能渲染已校验 claim，不允许渲染生成草稿的其他字段。

### C. 数值与条件的三元组校验（P0）

以通用 `condition → value → unit` 事实三元组替换当前“先清洗字符串、再猜主语”的 numeric guard。

1. 从每个 accepted passage 提取可审计的数值事实：`condition_span`、`value`、`unit`、`evidence_passage_id`、`evidence_char_range`。表格文本需以行/列上下文合成一个事实，不能将相邻行的条件和值串配。
2. 从 query 抽取数值意图和条件槽位；只将满足条件匹配、单位匹配、作用对象匹配的三元组提供给生成器/渲染器。
3. 若问题只要求某一条件，不得在最终答案中列出同一 passage 内其他条件的数值，即使这些值真实存在。这样通用地避免“III 类问题却输出 II 类 10 万”“涉诈问题却扩写涉骚扰 30 元”。
4. 所有 numeric guard 的处理写入 `numeric_fact_audit`：query slots、候选三元组、保留/删除原因、passage ID、字符范围。不得仅写不可审计的 `stripped_unanchored_value`。
5. 如果匹配的数值事实不存在，返回 `no_answer`；绝不补算、折算或用同文档的其他阈值代替。

### D. 严格拒答与 no-answer 契约（P0）

1. 只有同时满足下列条件才允许 `answer_mode` 为 `raw_only` / `hybrid`：

   - canonical snapshot 被接受；
   - 至少有一个支持查询核心槽位的 passage evidence；
   - 至少一个已校验 claim 可以直接回答问题。

2. 否则必须返回：`answer_mode="no_answer"`、`answer=""`、`sources=[]`、`raw_evidence_used=[]`、明确 machine-readable `reason`。不得返回“知识库找不到……”的长文本充当答案。
3. 若需要给 UI/调用方展示人类提示，新增与 `answer` 分离的 `user_notice`（不含用户原问题原文）；该字段不能进入事实评分，也不能含无关候选的实体或数值。
4. 对 no-answer 样例，非相关候选即便得分高也不得成为 sources。KB-032 这类“无知识库答案”的问题应是结构化拒答，不应在回答中列举品牌、差旅等无关文档。

### E. 不降全局阈值的 KB-021 类 gate 假阴性处理（P0）

1. 保持全局 `no_answer_threshold=0.35` 不变。
2. 在 gate 前增加通用 `direct_slot_evidence` 评估（不是 case 白名单）：

   - 候选 passage 对至少两个高信息 query 槽位有明确词面/同义词匹配，且包含用户所问事实类型（如“工作日”“时限”“处罚”“限额”）；
   - 或经可信 query rewrite 后，rewrite 的核心槽位在同一 passage 得到支持；
   - 输出匹配的槽位、同义词来源、passage ID、原文 span 和判定分值。

3. `direct_slot_evidence=true` 时可作为**额外接受条件**；不得修改原始相似度分数、不得让单一泛词或标题命中通过。
4. 对 `direct_slot_evidence=false` 的低分候选保持拒答，特别是五个 Golden no-answer 用例必须无误答。
5. KB-021 必须在日志中展示“产品问需、初审、产品评估、工作日/时限”中实际命中的槽位及 passage 原文，而不是只显示 `top_score=0.3`。

### F. 最新版本与禁用历史事实的输出硬断言（P1，本轮必须交付）

1. 保留 v3 的 document family 过滤；在生成前和渲染前分别断言 evidence/claim/source 的 family 与版本一致。
2. 对“最新/修订版”问题，若已检索到最新文档但该 passage 不足以支持用户提出的具体变更，输出只能陈述已验证的版本/文号事实，或结构化拒答；不能复述历史规则、比较假设或用户问题中未经支持的术语。
3. 不以“包含 forbidden 字符串”自动判定真实旧版污染。增加 `version_leakage` 审计字段，必须同时报告：旧版 `knowledge_id` 是否进入 candidate、snapshot、raw evidence、claim 与 source。只有旧版进入任一实际证据链时才为版本泄漏。
4. KB-037 回归必须证明：返回的 evidence/source 仅来自 2026-158 的最新 family；最终 `answer` 中不含 Golden 禁用短语；不得通过删除版本/文号信息规避 required facts。

### G. 评分、诊断与报告修正（P1）

1. 保持 v2/v3 放行指标、Golden 数据和基础评分口径不变。
2. 在 `final_scored.json` 将以下诊断分开输出，报告不得混淆：

   - `source_trace_validity`：source 是否在 snapshot / 相邻 allowlist；
   - `expected_document_support_rate`：source 是否为 expected 文档（仅诊断）；
   - `no_source_due_to_gate_count` 与 `no_source_due_to_answer_validation_count`；
   - passage trace completeness：成功 `ask` 中含 passage ID 的 source/raw evidence 比例；
   - `version_leakage` 实际证据链判定。

3. 失败归因必须优先引用原始 MCP 返回。不得把“生成器复述 query 中禁用词”写成“旧版本 source 污染”。

## 4. 必须新增的自动化测试

### 4.1 单元/集成

- Passage evidence DTO 从检索到 `ask.sources`、`raw_evidence_used` 的字段不丢失；passage 正常时不允许 `raw_block` fallback。
- 结构化 claim 生成：合法 JSON、非法 JSON、未引用 evidence ID、超出 evidence 的 claim、包含“问题拆解/推理过程”的草稿均被拒绝或清洗。
- 渲染器：不回显 query；不输出未校验 claim；输出长度与 bullet 上限；no-answer 的 `answer` 必为空。
- 数值三元组：同段多类别值（II/III 类）、多场景值（涉诈/涉骚扰）、表格行列和单位不串配；覆盖 KB-017/018/019 形态但使用可复用 fixture。
- Gate：低分无关候选仍拒答；`direct_slot_evidence` 对多槽位条款可接受，且记录 span/理由。
- 版本：最新 family 的 source/claim/raw evidence 不能带旧版 ID；query 中含历史术语但 evidence 不支持时，最终答案不得回显该术语。
- 评分诊断：四类 citation/no-source 指标互不混用，version leakage 以实际 evidence ID 判断。

### 4.2 定向 Golden 回归

必须在真实 MCP 流程中保留原始 `search` / `ask` 返回，至少覆盖：

| 类别 | 必测 case | 验收重点 |
|---|---|---|
| gate 假阴性 | KB-009、KB-011、KB-014、KB-015、KB-021 | 不降全局阈值；有直接槽位证据时可作答；KB-021 输出两个工作日时限。 |
| 数值/条件 | KB-010、KB-017、KB-018、KB-019、KB-022 | 只输出问题对应条件的事实；没有交叉数值、推算或不相关部门。 |
| 事实完整性 | KB-013、KB-016、KB-024、KB-028、KB-035、KB-036 | 直接答案覆盖 required facts，不以来源或长解释替代。 |
| 严格拒答 | KB-030–KB-034，尤其 KB-032 | `answer_mode=no_answer`、answer 为空、sources/raw evidence 为空。 |
| 最新版本 | KB-037 | 2026-158 最新文档证据完整；无旧版 evidence，也不回显禁用旧规则短语。 |

## 5. 验收与放行要求

新 artifacts 只能写入以下位置：

```text
artifacts/hit_rate_test_v4/
docs/evaluation/mcp-agent-knowledge-hit-rate-test-report-v4.md
```

不得覆盖前三轮 artifacts 或报告。必须全量重跑 37 个 Golden case，禁止复用 v3 的 `final_scored.json`，并同时执行：

```bash
pytest tests/ -q
pytest tests/services/test_passage_builder.py \
       tests/services/test_passage_store_and_search.py \
       tests/mcp/test_hit_rate_v3_regressions.py \
       tests/mcp/test_hit_rate_v4_answer_contract.py -q
```

如果全量测试耗时长，可分段执行，但必须在报告中给出完整命令、开始/结束时间、退出码和失败清单。仅有“定向测试通过”不能满足验收。

| 放行门槛 | 要求 |
|---|---:|
| Top-1 Accuracy | >= 75% |
| Recall@5 | >= 88% |
| Ask Fact Correctness | >= 90% |
| Ask Citation Validity | >= 95% |
| E2E Pass Rate | >= 90% |
| Hallucination Rate | <= 5% |
| False Positive Rate | <= 5% |
| P1 缺陷 | 0 |
| 成功 `ask` 的 source/raw evidence passage trace completeness | 100% |
| retrieval passage 向量及 FTS 覆盖率 | 100%（不得回退 block） |

其他硬性条件：

1. KB-017、KB-018、KB-019、KB-032、KB-037 必须逐条 E2E 通过；任何一例失败均不放行。
2. v4 不得出现 `raw_only` 成功答案携带 `passage_id=null` 或 `candidate_type=raw_block` 的情况。
3. v4 报告必须把“检索召回失败、gate 假阴性、生成/事实校验失败、结构化拒答失败、版本实际泄漏、评分诊断”分开统计。
4. 任何一项未达到要求，报告结论只能写**不通过放行**，并给出下一个可复现根因；不得用“局部提升”替代放行。

## 6. 交付物

1. 本 SPEC 涉及的实现、迁移（若确有 schema 变化则仅用 Alembic）和完整测试。
2. `artifacts/hit_rate_test_v4/`：37 例原始交互、MCP capabilities、passage/answer contract 诊断、评分明细、四轮对比、全量与定向测试日志。
3. `docs/evaluation/mcp-agent-knowledge-hit-rate-test-report-v4.md`：本轮根因验证、实现映射、前四轮指标、失败归因、version leakage 审计、测试退出码、放行结论与复现命令。
4. 实施说明必须明确列出：passage evidence DTO 的字段、结构化 claim schema、拒答合同、direct-slot gate 的通用规则及反误放行测试。

## 7. 实施顺序

1. 先补 evidence DTO 与 source/raw evidence 端到端契约测试，确认 v3 的 `passage_id=null` 问题被捕获。
2. 实现结构化 claim 生成、校验和短答案渲染；先以 KB-017/018/019/037 验证没有错误扩写。
3. 重写 numeric guard 为三元组校验，并加入审计日志。
4. 实现严格 no-answer 合同与 direct-slot gate；用 KB-021 与全部 no-answer 用例双向验证。
5. 完成版本泄漏审计与评分诊断拆分。
6. 运行全量项目测试与 Golden v4；全部门槛通过才可放行。
