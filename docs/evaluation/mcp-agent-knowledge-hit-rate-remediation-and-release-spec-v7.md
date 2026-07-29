# MCP Agent 知识命中率 V7 修复与收尾验收 SPEC

> 状态：待实施  
> 负责人：Codex  
> 依据：`docs/evaluation/mcp-agent-knowledge-hit-rate-test-report-v6.md`、`artifacts/hit_rate_test_v6/`、当前工作区代码  
> 目标：清除 V6 的题库适配和无效验收，在不降低安全门禁的前提下解决剩余检索、事实覆盖、引用溯源问题，并用一次可信的最终全量测试完成收尾。

## 1. 当前基线与本轮结论边界

V6 必须保持为**不通过放行**。其可采信的观测结果为：

| 指标 | V6 |
|---|---:|
| Top-1 | 87.50% |
| Recall@5 | 93.75% |
| Ask Fact | 56.25% |
| Ask Citation | 75.00% |
| E2E | 56.25% |
| Hallucination / FP | 0% / 0% |
| P1 | 14 |
| 全量耗时 | 34.57 分钟 |

V6 结果不能代表当前代码终态：全量测试后又修改了 KB-037 相关渲染逻辑；Tier 2 未通过仍执行了 Tier 3；没有真正的 rerank 双侧 A/B；没有完整 pytest 终态；生产代码仍含 Golden 特定问法和事实映射。

本轮必须先恢复工程与验收可信度，再提升指标。任何代码变更发生在最终全量之后，最终全量立即失效。

## 2. 硬性范围与禁止项

### 2.1 本轮范围

1. 清除生产代码中的 Golden/case 特定规则，建立通用、语料驱动的查询解析与扩展。
2. 精确定位 Recall@5、Top-1、answer plan、citation 和 provenance 的剩余失败阶段。
3. 统一 search/ask canonical snapshot、evidence group、FactCandidate、render 与 citation 的身份链。
4. 建立真实可比较的 rerank/fallback A/B 和固定版本测试清单。
5. 完成分层测试、37 例真实 MCP 全量和完整 pytest 收尾验收。

### 2.2 禁止项

- 不修改 `evals/golden_set_hit_rate.json`、评分器、required/forbidden facts、expected knowledge IDs、评分阈值。
- 不降低 `rag.ask.no_answer_threshold=0.35`，不降低 no-answer、引用、幻觉、FP 或 passage trace 门禁。
- 生产代码不得读取或导入 `evals/`、`artifacts/`、测试 fixture。
- 生产代码不得出现 `KB-xxx`、Golden knowledge UUID、Golden 完整题干、针对 Golden required fact 的固定分支。
- 不允许用正则替换表把 Golden 口语题干直接映射为目标文档标题、目标制度术语或答案事实。
- 不允许把“题干没明确要求的第二个值”按某个具体 Golden 题目的经验强制加入 required slots。
- 不允许通过扩大整篇文档/整页邻接、降低 gate、拼接不同条件/版本/文档的事实提高命中率。
- 不覆盖 v1–v6 artifacts/报告；V7 只写新目录。
- Tier 2 未满足进入条件时不得运行 Tier 3。最终全量之后不得再改代码后沿用原评分。

## 3. Phase 0：冻结可复现基线与修正验收工具

### 3.1 代码与运行指纹

实现统一 `RunFingerprint`，由 harness、search-only A/B 和最终报告共同使用：

```text
git_head
dirty_patch_sha256
production_source_sha256
config_sha256
golden_sha256
scorer_sha256
index_revision
database_revision
process_start_id
python_version / dependency_lock_sha256
retrieval_mode / rerank_mode / timeout settings
```

要求：

1. 不要求强制提交用户工作区；dirty 状态以 production diff/hash 固化。
2. 上述关键字段不得为空。无法取得 revision 时，使用相关文件/数据库/索引目录的稳定内容摘要，并注明计算方法。
3. 每个 Tier 和最终全量均写 fingerprint；最终评分脚本验证所有 case 的 fingerprint 一致。
4. `search` 之后、`ask` 之前若 config/index/process 指纹变化，snapshot 必须拒绝复用并将该 case 判为测试环境错误，不得静默重检后继续计入放行。

### 3.2 修复 harness 与评分完整性

1. `snapshot_reuse_audit.json` 对每个 miss 输出：`reason`、search/ask fingerprint、snapshot ID 状态、是否重检、额外耗时；不能只记录 case 和耗时。
2. `passage_trace_completeness=true` 的条件必须是最终 answer 使用的每个 source、raw evidence 和 rendered candidate 都有非空、可在 canonical snapshot/adjacent allowlist 中验证的 `passage_id`。
3. `passage_id=null`、合成 ID 无法反查、source 与 candidate 不一致时，评分必须失败，不能靠 `knowledge_id` 相同通过。
4. block fallback 的合成 passage ID 仅为兼容标识；必须同时保留真实 `block_id` 与 lineage，并能反查原 block。不能伪装为 passage store ID。
5. 新增 `artifacts_integrity.json`，验证 37 个 case 文件、评分明细、manifest、日志和 fingerprint 是否齐全。
6. 最终 pytest 必须生成完整 `pytest_final.txt` 和退出码；`pytest_final_related.txt` 只能作为中间证据。

### 3.3 真正的 A/B 工具

重构 `scripts/hit_rate_search_ab.py`，禁止仅用 `--label` 对同一 live 模式跑一次冒充 A/B。

必须支持：

```text
--capture-raw-candidates OUT
--replay INPUT --mode normal-rerank|deterministic-fallback
--compare A.json B.json --out comparison.json
```

设计：

1. 对同一 query 只执行一次原始 FTS/vector/fusion，保存 rerank 前候选和全部分数。
2. normal-rerank 与 deterministic-fallback 消费同一份候选快照，分别输出排序结果。
3. comparison 逐例输出 Top-1/Recall@5 变化、候选升降、预期证据是否掉出 Top-5、阶段耗时。
4. MCP wall-clock 另由 Tier 2/3 测量；离线 replay 只验证算法质量，不能冒充真实端到端耗时。

## 4. Phase 1：清除题库硬编码，建立通用语义能力

### 4.1 必须删除或重构的模式

重点审计并重构：

- `src/retrieval/raw_retriever.py::build_deterministic_query_variants`
- `src/answering/query_planner.py`
- `src/answering/direct_slot_gate.py::_SLOT_DEFS`
- `src/answering/fact_candidates.py`
- `src/answering/claim_protocol.py`
- 其他 answering/retrieval 目录中的 Golden case 注释、固定事实列表和特殊分支

以下模式一律视为失败：

```text
“Golden 口语表达” -> “目标制度完整标题/术语”
“某奖金问题” -> 强制要求“总额+人均”
“某账户问题” -> 强制要求“II类+III类”
固定事实短语集合 -> direct-slot / anchor / high-value
```

允许保留的规则只能是与题库无关的语言结构，例如否定词、比较词、数值单位、时间表达、常见问句谓词；它们必须在合成未见语料测试中证明可泛化。

### 4.2 语料驱动 QueryAnalysis

将 query 解析为通用结构：

```text
QueryAnalysis
  intent
  entity_mentions[]
  predicate_mentions[]
  polarity
  scope_mentions[]
  selectors[]
  conditions[]
  requested_dimensions[]
  time_constraint
  comparison_or_multi_part[]
```

要求：

1. 字段从 query 文字、通用语言规则和当前索引语料推导，不从 Golden 专用词表推导。
2. `requested_dimensions` 只包含题干明确要求的属性；若原文同一逻辑记录含多个强关联字段，可以一并回答，但不能把它们先写成题目专属 required slots。
3. 多子问题只能由显式并列、分别、对比、历年、各类等语言结构触发。
4. 对未见实体、未见文档名、替换后的合成数值和同义问法仍应产生相同结构。

### 4.3 语料驱动查询扩展

不得维护 Golden 问法映射表。查询扩展采用以下通用流程：

1. **原始查询召回**：原 query 同时进入 FTS/BM25、vector 与标题/章节索引，先取较宽 raw pool（建议每路 20–50，之后统一去重）。
2. **短语保留**：保留原 query 的高信息名词短语和否定/数值条件，不能用改写替换掉原 query。
3. **索引语料候选扩展**：从 raw pool 的标题、章节、命中 span 中抽取与 query 向量/字符子串共同相关的术语；仅选择可指回语料 span 的术语。
4. **受控二次召回**：最多 3 个 expansion；每个记录 `derived_from_passage/title`、相似度、选择原因。原始查询结果和扩展结果使用 RRF/现有 VerifiedFusion 合并。
5. **防漂移**：扩展必须保留至少一个原始实体/对象锚点；扩展只命中主题而不命中谓词/条件时不得提升为 Top-1。
6. 如使用 LLM query rewrite，只能作为可配置的通用阶段：temperature=0、有限输出、超时 fail-open 到原始检索、输出可审计；不得在 prompt 中放 Golden 示例或答案。

### 4.4 防过拟合自动检查

新增测试：

1. AST/文本扫描生产代码，禁止 `KB-\d+`、Golden UUID、读取 `evals/artifacts`。
2. 将生产代码字符串常量与 Golden query 的长 n-gram、完整 required fact 做重合检测；命中需进入小型、人工审核的“通用语言操作符”allowlist，领域答案短语不得豁免。
3. 用至少 12 个合成未见 query/evidence fixture 替换实体、部门、数值、单位、版本，验证 planner、group、coverage、render 不依赖原 Golden 词。
4. 至少 4 个反例：题干相似但目标制度、条件或数值不同，必须选不同 evidence，证明不是关键词触发固定答案。

## 5. Phase 2：检索阶段可观测与 Recall/Top-1 修复

### 5.1 每个失败必须定位到具体阶段

对 32 个 answerable case 生成 `retrieval_stage_audit.json`：

```text
raw_fts_ids
raw_vector_ids
title_or_section_ids
expanded_query_ids
fusion_ids
rerank_input_ids
rerank_output_ids
policy_filtered_ids + reason
canonical_accepted_ids
```

每个 expected ID 首次丢失在哪一阶段必须明确。禁止笼统写“检索偏差”。

### 5.2 针对阶段修复，而不是增加题目映射

- raw FTS 缺失：检查中文分词、短语/字符 n-gram、字段权重和索引覆盖。
- raw vector 缺失：检查 passage 是否有向量、embedding model/version、query normalization、top_k 和向量过滤。
- fusion 丢失：调整 RRF/多路去重和每路最低保留配额；不得让单一路只返回 1 个候选后直接结束。
- rerank 丢失：加入可解释的 entity/predicate/condition/temporal 特征，并保留 raw 高置信 exact match 的安全下限。
- policy gate 丢失：复用 QueryAnalysis 的通用匹配，不使用固定 `_SLOT_DEFS`。
- canonical snapshot 丢失：修复 search/ask 统一证据视图和 provenance，不做第二套过滤。

### 5.3 文档族、版本和组织范围

当前大量 `document_family_id/version_year=null`，不能依赖空 metadata 做版本选择。新增无 Schema 变更的运行时解析层（如需持久化必须另走 Alembic）：

1. 从标题/metadata 解析组织范围、规范化制度名、文号、年份、修订/试行/废止状态。
2. `family_key = organization_scope + normalized_policy_name`，不能把广西公司与地市/专业子公司混为同一族。
3. 版本新旧只在 query 有现行/最新/变更/历年语义时参与强排序。
4. “历年变化”类 query 进入 temporal multi-group 模式，每个版本独立引用；普通 query 默认单 primary group。

### 5.4 性能与熔断

1. rerank 调用必须有真实可取消的单次预算；timeout 后不得继续占用后台线程/连接。
2. circuit breaker 的 normal、open、half-open、probe 状态必须进入 trace。
3. 建议每次 search 总预算 <=55 秒，rerank 子预算 <=20 秒；具体值以本机 A/B 为依据，不得以牺牲 Recall 为代价。
4. fallback 排序只使用同一 raw candidate pool 的可解释分数；相同输入必须确定性输出。
5. 若 normal rerank 不可用率过高，报告为外部依赖/配置故障；不得把持续 timeout 隐藏为“性能优化成功”。

## 6. Phase 3：回答覆盖、引用与来源闭环

### 6.1 EvidenceGroup 先选组后选事实

1. `EvidenceGroup` 必须使用解析后的 family、scope、version 与 retrieval score；不能只按 `knowledge_id` 分组后声称已解决文档族问题。
2. 普通单制度问题默认只从 primary group 产生最终 candidate。
3. 显式跨文档、跨版本、多子问题才能使用多个 group；每个 bullet 标明对应 group/candidate。
4. 组选择不确定时 fail-closed，不能混合两个制度的事实凑覆盖。

### 6.2 通用覆盖规划

构建：

```text
required slots from QueryAnalysis
candidate-supported slots with exact spans
coverage matrix
selected minimal evidence-consistent candidate set
rendered bullet coverage
```

要求：

1. `policy_fact` 之类粗粒度槽位不能单独满足具体职责、禁止、关系、数值或版本问题。
2. subject/object/predicate/polarity/condition/value/unit/time 必须在 candidate span 和最终文本中分别验证。
3. 数值必须绑定同一 LogicalEvidenceRecord 或可验证的同表头/同行结构；禁止跨行、跨 passage、跨 group 猜配。
4. 最多 3 条 bullet 的限制不能截掉 required slot；先覆盖，再压缩表达。
5. 最终渲染后二次校验只验证 query 真正要求的语义，不为某个 Golden case增加隐藏必答事实。

### 6.3 Citation 与 passage provenance

1. 每个最终 bullet 至少绑定一个 `candidate_id` 和一个真实 `passage_id/block lineage`。
2. sources 由已渲染 candidate 反向生成，不能把所有邻接证据都列为来源。
3. source 必须属于 canonical accepted 或带明确理由的受限 adjacent extension；同 knowledge_id 的任意 passage 不能自动入 allowlist。
4. successful ask 的 answer、raw_evidence_used、sources、claims_used 四者必须形成一一可审计关系。
5. no-answer 仍保持 `answer=""`、`sources=[]`、`raw_evidence_used=[]`，不得回显用户问题或模型过程散文。

## 7. 剩余 P1 的处理矩阵

先使用 Phase 2 的 stage audit 自动重新归类 V6 的 14 个 P1，以下为初始假设，不得直接当作编码分支：

| 初始类别 | V6 cases | V7 必须证明 |
|---|---|---|
| Recall@5 缺失 | KB-010、KB-011 | expected evidence 在哪一检索阶段丢失；通用修复后进入 Top-5。 |
| Top-1/组选择/版本范围 | KB-009、KB-024、KB-026 | 正确 scope/family/temporal 模式；不靠固定知识 ID。 |
| 正确召回但 candidate/coverage 失败 | KB-012、KB-013、KB-014、KB-016、KB-020、KB-022、KB-023、KB-025 | selected group、coverage matrix、render validation 与 citation 均闭合。 |
| 全量后补丁未验证 | KB-037 | 当前代码通过定向与真实 MCP；最终全量使用同一代码指纹。 |

所有 P1 必须产出 before/after dossier：stage audit、primary group、selected candidates、missing slots、render result、sources 和评分；不能只写“已修复”。

## 8. 节省时间的分层测试流程

### Tier 0：静态与纯函数，目标 <=5 分钟

- anti-Golden 静态扫描；
- QueryAnalysis 合成未见语料；
- corpus-derived expansion 与防漂移；
- family/scope/version 解析；
- evidence group、coverage、numeric binding、render validation；
- stable IDs、provenance、snapshot fingerprint；
- A/B capture/replay/compare；
- rerank timeout/circuit state。

门禁：全部通过才进入 Tier 1。

### Tier 1：离线 artifact 回放，目标 <=10 分钟

使用 V6 原始交互作为只读 fixture，不启动 MCP、不重新检索：

1. 回放 14 个 V6 P1，验证 answer/group/coverage/provenance。
2. 回放所有 V6 E2E 通过 case，保证旧通过项不退化。
3. 表格/数值硬回归：KB-017/018/019/021/028。
4. no-answer：KB-030–034。
5. 版本/旧规则：KB-035/036/037。

门禁：回放层可回答且 evidence 足够的 case 必须全部通过；原始 evidence 本身缺失的 case 必须明确标成 retrieval-blocked，不得伪造通过。

### Tier 2A：一次 raw retrieval capture + 离线双侧 A/B，目标 <=12 分钟回放时间

1. 对 32 个 answerable case 捕获一次 rerank 前 raw candidate pool；真实检索捕获耗时单独报告。
2. normal-rerank 与 fallback 对同一快照离线 replay。
3. fallback 相对 normal 不得新增 expected evidence 掉出 Top-5；整体 Top-1、Recall@5 不得下降。
4. 若 normal 本身未达到 Recall 门禁，先修检索，不进入 MCP 冒烟。

### Tier 2B：真实 MCP 高风险冒烟，目标 <=15 分钟

固定 16 例：

```text
KB-009, KB-010, KB-011, KB-012, KB-013, KB-014,
KB-016, KB-020, KB-022, KB-023, KB-024, KB-025,
KB-026, KB-032, KB-036, KB-037
```

执行 `search → snapshot reuse ask → unique read`，workers=1。门禁：

- 14 个 V6 P1 必须全部 E2E 通过；
- KB-032 严格 no-answer；
- snapshot 可复用 case 100% reuse；
- successful ask provenance 100%；
- 无幻觉、无 FP；
- 无单 case 超过 90 秒，超时必须先处理。

Tier 2B 未全绿，不得运行 Tier 3。

### Tier 2C：并发与确定性（可选）

只有 workers=2 计划用于最终全量时才执行。同一 8 例 workers=1/2 比较 fingerprint、candidate IDs、answer、sources、reason、评分；任何差异即最终使用 workers=1。若最终使用 workers=1，可省略本层。

### Tier 3：最终收尾验收，只在代码冻结后执行

1. 生成 release candidate fingerprint，冻结 production source/config/index/DB/scorer/Golden。
2. 运行完整 `pytest tests/ -q`，保存 `pytest_final.txt`、退出码和相对基线差异。本轮相关失败必须为 0，不得新增失败。
3. 启动一个干净 MCP 进程，确认 process_start_id 后运行 37/37 全量；禁止 resume V6 或旧 V7 运行。
4. 评分、完整性校验、报告生成必须在不改代码的情况下完成。
5. 若 Tier 3 失败后修改任何生产代码，必须建立新的 `run_id` 并重新执行完整 Tier 3；不能拼接两次结果。

## 9. 最终放行门禁

全部满足才可写“通过放行”：

| 门禁 | 阈值 |
|---|---:|
| Top-1 | >=75% |
| Recall@5 | >=88% |
| Ask Fact | >=90%（至少 29/32） |
| Ask Citation | >=95%（至少 31/32） |
| E2E | >=90%（至少 29/32） |
| Hallucination | <=5% |
| False Positive | <=5% |
| P1 | 0 |
| successful ask provenance | 100% |
| passage vector/FTS coverage | 100% |
| 37 例完整性 | 37/37，本轮同一 fingerprint |
| 全量时间 | <=35 分钟，且相对 V4 降低 >=50% |

额外工程门禁：

- anti-Golden 扫描通过；无题库映射、UUID/case 分支、eval/artifact 生产依赖。
- Tier 0/1/2 全部通过后才运行 Tier 3。
- 真正双侧 A/B 完整，fallback 不降低检索质量。
- manifest 关键字段完整，artifact integrity 通过。
- 完整 pytest 终态已执行；不能只提交 related tests。
- 当前代码与全量 fingerprint 一致，无测试后补丁。

任一门禁失败，只能写“**不通过放行**”。

## 10. V7 交付物

只新增：

```text
artifacts/hit_rate_test_v7/
  run_manifest.json
  artifacts_integrity.json
  retrieval_stage_audit.json
  anti_overfit_audit.json
  p1_dossiers/
  tier0/
  tier1/
  tier2_ab/
  tier2_mcp/
  final_run/
    KB-001.json ... KB-037.json
    final_scored.json
    metrics_comparison.txt
    snapshot_reuse_audit.json
    group_coverage_audit.json
    provenance_audit.json
    performance_breakdown.json
    pytest_final.txt
    logs/

docs/evaluation/mcp-agent-knowledge-hit-rate-test-report-v7.md
```

报告必须包含：

1. V1–V7 指标与耗时对比；
2. 14 个 V6 P1 的 before/after dossier 摘要；
3. 题库硬编码删除证据和 anti-overfit 测试结果；
4. 32 例 retrieval stage audit 与真实 A/B；
5. snapshot、group、coverage、citation、provenance 审计；
6. Tier 0–3 命令、时间、退出码、fingerprint；
7. pytest 基线/最终差异；
8. 最终放行门禁逐项判定。

## 11. 实施顺序（不得跳步）

1. 冻结当前代码/配置/索引/数据库指纹；修复 harness、provenance scorer、manifest 和 A/B 工具。
2. 建立 anti-Golden 扫描与合成未见语料测试，使现有硬编码稳定失败。
3. 删除 Golden 特定规则，落地 QueryAnalysis、语料驱动 expansion、通用 direct-slot。
4. 生成 retrieval stage audit，按首次丢失阶段修复 Recall/Top-1；完成真实双侧 A/B。
5. 修复 family/scope/version group、coverage/render/citation/provenance，完成 14 个 P1 dossier。
6. Tier 0、Tier 1 全绿后执行 Tier 2A；A/B 通过后执行 Tier 2B。
7. Tier 2B 全绿后冻结 release candidate；执行完整 pytest 和唯一一次最终 37 例 Tier 3。
8. 不改代码地完成评分、artifact integrity 和 V7 报告；按真实门禁给出结论。
