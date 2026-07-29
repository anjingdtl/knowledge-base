# MCP 命中率 Phase 2–3：核心链路收束与质量重建实施方案

> 状态：待执行  
> 编制日期：2026-07-29  
> 输入基线：`master@5bb41f7`（执行前必须重新记录实际 HEAD）  
> 前置成果：Phase 0–1 评分合同、Golden V2 工程治理、架构债与版本一致性  
> 当前发布结论：**NO-GO**  
> 本方案范围：Phase 2 架构边界与契约收束；Phase 3 检索、证据与回答质量重建

## 1. 总体结论

Phase 0–1 的主体方向正确，以下聚焦门禁已复核通过：

- `report_closure_debt.py --strict`：通过；
- Phase 0–1 架构、版本、评分、Schema、freeze、split、sanitize、rerank、harness integrity：**57 passed**；
- attempt 20 使用 Scorer V2 后，KB-032 被正确判为 false positive；
- Golden V1 保持未改写，V2 candidates=37，reviewed=0，frozen=0；
- 正式库 hash 在 Phase 0–1 执行报告中前后一致。

但 Phase 0–1 仍有必须补充的工程与评测闭环，不能直接进入算法改写：

1. 报告写的是 HEAD `19195d4`、未提交，当前实际 HEAD 已是 `5bb41f7`；
2. 全量 pytest 仍有 9 个可稳定复现失败，另有 1 个顺序敏感用例；
3. formal Harness 的 `review_manifest_hash`、`corpus_snapshot` 仍写空字符串；
4. formal 模式只检查路径包含 `frozen`，未重新校验 frozen 行的审核与 corpus；
5. freeze gate 未强制 `evidence_checked`，也未校验 adjudicator 与两名 reviewer 独立；
6. Scorer V2 的 fact group 仍主要是 substring：
   - `numeric_unit` 与 normalized 行为相同；
   - condition/scope/version 未参与事实覆盖；
   - citation pass 只验证 Snapshot allowlist，未绑定 Golden expected passage；
   - `clarification_required` 尚无完整评分语义；
7. `artifacts/eval-summaries/` 同时提交了未脱敏版和脱敏版结果，不符合“Git 仅保留脱敏摘要”的最终目标。

因此 Phase 2 必须先执行“2.0 收口门禁”，通过后才能开始架构迁移。

## 2. 阶段目标

### Phase 2：核心边界收束

目标是把当前分散在 MCP、SearchService、RawRetriever、Snapshot、AnswerService 中的业务编排收敛为：

```text
MCP / REST / GUI
       │
       ▼
SearchUseCase / AskUseCase
       │
       ├── RetrievalPipeline
       ├── EvidenceSnapshotService
       └── AnswerPipeline
               │
               ▼
     Provider Ports / Repositories
```

Phase 2 以行为保持、契约清晰、依赖单向为主，不以提高命中率为主要目标。

### Phase 3：检索与回答质量重建

在 Phase 2 的强类型边界上，针对当前失败机制重建：

- 查询规划与候选召回；
- 排序、版本选择与 reranker profile；
- 证据门禁与 Snapshot；
- 条件化事实抽取；
- 跨文档 AnswerPlan；
- 引用绑定、拒答与最终验证。

Phase 3 可以使用当前 37 题和高风险 15 题做 development/regression，但不得将结果称为正式放行指标。

## 3. 强制不变量

### 3.1 数据与评测

- `data/kb.db` 全程只读，阶段开始和结束记录 SHA256、大小和 mtime。
- reviewed=0 / frozen=0 时，所有运行必须标记 `non_formal=true`。
- Agent 不得伪造 reviewer、审核时间或 adjudicator。
- 当前 37 题不得进入 holdout。
- 禁止删除失败题、改知识原文、弱化事实标签或降低阈值来提高分数。
- 生产代码禁止出现 `KB-009`、`KB-013` 等 case ID 或完整 Golden 问法映射。

### 3.2 公开契约

- MCP 工具名、参数、Envelope 和核心返回字段默认保持不变。
- Snapshot 变更必须区分内部模型与公开 payload。
- 公开 contract snapshot 不得直接刷新；必须先解释语义差异并形成 ADR。
- `search(top_k=5)` 的公开返回仍为最多 5 条；内部 `fetch_k` 可以更大，但必须显式建模。

### 3.3 架构

- MCP 不得直接调用 `SearchService._get_raw_retriever()`、`PassageStore._get_conn()` 等私有方法。
- MCP 不负责召回、Snapshot 构建、事实抽取、引用裁决和版本排序。
- Search 和 Ask 必须使用同一个 RetrievalPipeline 和同一 Snapshot 合同。
- legacy 路径只能放在 compatibility 包中，不得作为正常生产路径的静默 fallback。
- broad exception 必须转为稳定 reason code；不得吞掉契约错误。

### 3.4 Git 与交付

- 不提交、不推送、不创建 PR，除非任务发起人明确授权。
- 不重写 Git 历史。
- 不批量删除既有 artifacts；如需清理，先输出精确清单并等待授权。
- 不覆盖 Phase 0–1 原始报告；只能补充勘误或新报告。

## 4. Phase 0–1 补充项：Phase 2 强制入口门禁

## Task 2.0：Phase 0–1 收口纠偏

Phase 2 的其他任务必须等待本任务全部通过。

### 2.0.1 权威状态与 Git 事实勘误

- [ ] 记录执行时实际 HEAD、branch、status。
- [ ] 在 Phase 0–1 报告增加“执行后 Git 状态勘误”：
  - 当前实际提交 `5bb41f7`；
  - 原“未提交”声明只代表报告生成时状态，不代表当前仓库状态。
- [ ] 更新 `PROGRESS.md` 中仍指向 `19195d4` 的当前 HEAD。
- [ ] 不修改历史指标与原始执行时间。

### 2.0.2 全量 pytest 归零

已复现的 9 个确定性失败：

1. `test_search_fulltext_fallback_no_match`
2. `test_launcher_status_does_not_query_windows_service`
3. `test_ask_raw_only`
4. `test_ask_timeout_generate_failed`
5. `test_search_raw_snapshot`
6. `test_search_no_result`
7. `test_search_calls_rewrite_hybrid_rerank`
8. `test_search_fallback_to_block_store`
9. `test_wiki_001_raw_evidence_is_final_base`

另有：

- `test_end_to_end_structured_query_through_rag` 在全量运行中失败、定向运行通过，需要解决顺序污染。

处理要求：

- [ ] 将失败逐项归类为：
  - 实现回归；
  - 测试依赖内部实现；
  - 有意公开契约变更；
  - 全局状态/顺序污染。
- [ ] 实现回归必须修实现。
- [ ] 测试若错误绑定内部 `fetch_k`，改为验证公开输出与明确的 CandidatePoolPolicy。
- [ ] contract snapshot 只有在 ADR 认定为有意变更后才能更新。
- [ ] 顺序敏感测试必须定位泄漏状态：Config、singleton、cache、registry、环境变量或线程。
- [ ] 禁止跳过、xfail、删测试或仅改断言迎合当前实现。

重点设计决策：

#### Search top_k 与 fetch_k

当前内部为保持 rerank pool 使用 20 条候选，但旧测试期望内部调用 top_k=5。应引入明确模型：

```python
CandidatePoolPolicy(
    public_top_k=5,
    fetch_k=20,
    rerank_top_k=5,
    max_per_document=...,
)
```

测试应验证：

- 对外最多返回 `public_top_k`；
- 内部 pool 大小由策略决定；
- fallback 和主路径使用同一策略；
- trace 记录 pre-rerank / post-rerank 数量。

#### Raw-only Ask 契约

`raw_only` 与 `no_answer` 的分歧必须形成 ADR：

- Raw evidence 明确支持查询时：允许 `raw_only`；
- Raw evidence 与查询不相关、事实槽位未覆盖时：必须 `no_answer`；
- 测试 fixture 必须包含可判断的 query/evidence，不使用无语义占位串验证业务结论；
- 不得为了让旧 snapshot 变绿而让任意 raw 文本自动回答。

#### Heartbeat 与业务隔离

heartbeat 写入失败或 Config monkeypatch 不得阻断只读 MCP 工具：

- heartbeat 只能 best-effort；
- 不得遮蔽业务工具自身返回；
- 测试必须覆盖只读工具在 heartbeat 异常时仍按契约执行。

#### GUI MCP 状态

- PID 存在但 heartbeat 不可用时不得直接报告 running；
- 周期状态检查不得调用 `sc.exe`；
- installed/running/available 三个状态分离。

验收：

```powershell
pytest tests/stability/test_fts_no_answer_gate.py -q
pytest tests/test_mcp_gui_status.py -q
pytest tests/test_public_ask_contract.py tests/test_public_search_contract.py -q
pytest tests/test_search_service.py tests/test_wiki_serving_contract.py -q
pytest tests/test_query_revolution_phase3.py -q
pytest tests/ -q
```

要求：**0 failed**。

### 2.0.3 Formal Harness 真冻结校验

当前缺口：

```python
review_manifest_hash=""
corpus_snapshot=""
```

必须修改为：

- [ ] formal 启动时逐行运行 `validate_freeze_row()`；
- [ ] frozen 文件必须非空；
- [ ] 所有行必须 `annotation_source=human_reviewed`；
- [ ] 所有行 corpus snapshot 必须相同；
- [ ] corpus snapshot 必须与只读 `data/kb.db` 当前 hash 一致；
- [ ] review manifest 从审核元数据、数据集 hash、Schema hash 确定性生成；
- [ ] `review_manifest_hash` 和 `corpus_snapshot` 不得为空；
- [ ] split 必须显式且单一；formal 不得混跑 development/holdout；
- [ ] resume 时任一 review/corpus/schema/scorer hash 变化都拒绝复用；
- [ ] candidates/reviewed/空 frozen/伪造 frozen 路径均 fail closed。

不得只通过目录名中出现 `frozen` 判定正式性。

必须新增测试：

- fake `.../frozen/...` 路径但行未审核 → 拒绝；
- frozen 空文件 → 拒绝；
- corpus 不一致 → 拒绝；
- review manifest 为空 → 拒绝；
- frozen 行被修改后 resume → 拒绝；
- mixed split → 拒绝。

### 2.0.4 审核与裁决完整性

- [ ] `review.evidence_checked` 必填；
- [ ] 每个 expected/acceptable/forbidden source 都有审核决定；
- [ ] 每个 required fact 的 evidence passage 已检查；
- [ ] adjudicator 必须同时不同于 primary 和 secondary；
- [ ] adjudication 必须记录原分歧、最终决定和时间；
- [ ] rejected/disputed/needs_adjudication 不得冻结；
- [ ] review CLI 不允许通过后一次操作静默清掉历史 disagreement。

### 2.0.5 Scorer V2 语义补齐

当前结构化评分仍不完整，必须补齐：

#### Fact group

- `exact`：规范化后精确短语；
- `normalized`：允许显式 acceptable variants；
- `numeric_unit`：数值、单位、条件必须绑定；
- `semantic_review`：只能用于人工审核或独立 judge 轨，不得退化为 substring 自动通过。

以下字段必须参与覆盖：

- subject；
- predicate；
- object/value + unit；
- condition；
- scope；
- version。

不得只因为回答中分别出现“99%”和“实名”就认定条件事实完整。

#### Citation

回答引用通过必须同时满足：

1. passage 在 Snapshot allowlist；
2. passage 在 `raw_evidence_used`；
3. passage 与 Golden expected/supporting passage 匹配；
4. 引用 passage 支持对应 fact group，而不只是同 knowledge ID。

#### Clarification

`answerability=clarification_required` 必须独立评分：

- 正确提出 Golden 定义的澄清维度 → pass；
- 擅自选择一个解释并给确定答案 → fail；
- 纯拒答但未提出必要澄清 → fail。

#### Unsupported assertion

当 AnswerPipeline 已产生结构化 claims 后：

- 每个最终断言必须绑定 passage；
- 无支持的最终断言计入 `Unsupported Assertion Rate`；
- 在不可测阶段仍为 N/A，不得提前报 0。

### 2.0.6 Artifact 保留策略落地

- [ ] `artifacts/eval-summaries/` 只保留 sanitizer 输出；
- [ ] 未脱敏的 `final_scored_v2.json` 移到 Git ignored 本地目录或不再生成到可提交目录；
- [ ] `artifacts/hit_rate_test_v7/.../final_scored_v2.json` 是否保留需列清单，不做历史重写；
- [ ] 添加自动测试：可提交目录出现未带 `_sanitizer` 标记的明细文件时失败；
- [ ] 文档说明 sanitizer 输出不是“匿名化”，只是最小化与脱敏摘要。

### Task 2.0 完成条件

- 全量 pytest 0 failed；
- formal Harness 真实校验 freeze/review/corpus；
- reviewer/adjudicator/evidence review 门禁完整；
- Scorer V2 支持 condition/scope/version/numeric/citation/clarification；
- 权威 HEAD 和 Git 状态描述正确；
- 可提交 artifact 目录只有脱敏摘要；
- `data/kb.db` hash 不变。

## 5. Phase 2：架构边界与单一主管线

## Task 2.1：公开契约基线与 ADR

创建：

- `docs/architecture/adr-search-ask-contract-v2.md`
- `docs/architecture/retrieval-answer-boundaries-v2.md`

冻结：

- SearchExecution 对外字段；
- AnswerExecution 对外字段；
- search/ask Snapshot 复用语义；
- raw_only/no_answer/conflict_disclosure 的定义；
- timeout/cancel/fallback reason code；
- MCP、REST、GUI 对同一 UseCase 的适配规则。

要求：

- [ ] 对现有 contract snapshot 做语义 diff；
- [ ] 区分字段新增、值语义改变和内部 trace 改变；
- [ ] ADR 通过前不得刷新 snapshot；
- [ ] 对外兼容字段只能在 adapter 中组装。

## Task 2.2：强类型核心模型

建议新建：

```text
src/retrieval/contracts.py
src/answering/contracts.py
src/application/contracts.py
```

核心模型：

- `QueryPlan`
- `CandidatePoolPolicy`
- `RetrievalCandidate`
- `RetrievalStageResult`
- `EvidenceSource`
- `EvidenceSnapshot`
- `RetrievalDecision`
- `AnswerClaim`
- `AnswerPlan`
- `AnswerValidation`

要求：

- frozen dataclass 或等价不可变模型；
- 明确字段类型，不在阶段间传任意 dict；
- knowledge/block/passage/family/version 必须有固定位置；
- score 必须区分：
  - vector score；
  - FTS score；
  - fusion score；
  - rerank score；
  - final relevance score；
- reason code 使用枚举/常量；
- 仅 transport adapter 转换为 public dict。

兼容：

- `SearchExecution` / `AnswerExecution` 暂保留；
- 增加 typed model → legacy/public payload adapter；
- 不在本阶段直接删除公共兼容类型。

## Task 2.3：Application UseCases 与 Ports

建议文件：

```text
src/application/search_use_case.py
src/application/ask_use_case.py
src/application/read_use_case.py
src/application/evidence_snapshot_service.py
src/application/ports.py
```

Ports：

- `CandidateRetriever`
- `Reranker`
- `EvidenceRepository`
- `SnapshotRepository`
- `FactExtractor`
- `AnswerRenderer`
- `AnswerValidator`

要求：

- SearchUseCase 负责检索用例，不生成 MCP Envelope；
- AskUseCase 接收 question 或 snapshot_id，不自行调用 MCP；
- Ask 复用 Search 产生的 Snapshot 时不得重新检索；
- Snapshot miss/expired/config mismatch 有稳定 reason；
- Container 负责注入实现；
- 测试使用 ports fake，不 monkeypatch 私有方法。

## Task 2.4：MCP Retrieval Adapter 变薄

当前 `src/mcp/tools/retrieval.py` 约 2700 行。拆分建议：

```text
src/mcp/tools/search.py
src/mcp/tools/ask.py
src/mcp/tools/read.py
src/mcp/tools/query.py
src/mcp/tools/capabilities.py
src/mcp/tools/retrieval_adapter.py
```

MCP 仅负责：

- 参数别名与校验；
- write/read policy；
- deadline 调用；
- UseCase 调用；
- Envelope 与 annotations。

必须下沉：

- `_retrieve_candidates`
- `_build_shared_snapshot`
- `_select_document_passages_for_snapshot`
- `_do_ask` 的业务判断；
- citation allowlist 裁决；
- index/db revision 计算；
- PassageStore/Database 读取。

建议预算：

- 单个 MCP tool handler ≤ 100 行；
- 不允许 MCP 调用下层私有方法；
- `retrieval.py` 最终仅做兼容 re-export，≤ 300 行；
- 业务 stage 单函数建议 ≤ 150 行。

预算是架构告警，不得通过压缩格式规避。

## Task 2.5：单一 Search/Ask 主管线

生产正常路径：

```text
SearchUseCase
  -> RetrievalPipeline
  -> EvidenceSnapshotService
  -> SearchExecution adapter

AskUseCase
  -> load or build EvidenceSnapshot
  -> AnswerPipeline
  -> AnswerExecution adapter
```

要求：

- `SearchService` 退化为兼容 Facade；
- `RetrievalCommands` 退化为 adapter 或被 UseCase 替代；
- `rag_pipeline.py` 不再是正常 Ask 主管线；
- 测试 double 所需 fallback 放入 `src/compatibility/`；
- legacy fallback 必须显式 `route.mode=compatibility`；
- normal 生产运行不得静默切入 legacy。

## Task 2.6：架构门禁与 Shadow Parity

新增架构测试：

- MCP 不导入具体 DB/PassageStore/RawRetriever；
- MCP 不调用下层私有方法；
- application 不导入 MCP；
- answering 不导入 MCP/GUI/API；
- retrieval 不导入 MCP/answer renderer；
- adapters 之外禁止 typed model → dict 随意转换；
- normal Search/Ask 主管线唯一。

Shadow：

- 同一输入同时运行 old facade 与新 UseCase；
- 比较候选 ID、Snapshot fingerprint、answer mode、source lineage；
- 不比较非确定性时间戳；
- 差异必须有 reason code 与批准清单。

Phase 2 不要求质量提升，但不得退化：

- 当前 development 37 的 Top-1、Recall@5、Ask Fact、Citation、E2E 不低于 Phase 0 V2 基线；
- KB-032 仍必须 false positive；
- deterministic profile 可重复；
- provider-enhanced blocked 状态保持诚实。

## 6. Phase 3：检索、证据与回答质量重建

## Task 3.0：方向性基线与失败分层

- [ ] 使用 deterministic-baseline；
- [ ] 使用 V2 scorer；
- [ ] development 37 和高风险 15 全部 `non_formal=true`；
- [ ] 记录每题失败阶段：
  - query planning；
  - candidate generation；
  - ranking/version；
  - evidence gate；
  - fact extraction；
  - answer planning；
  - citation validation；
  - no-answer validation。
- [ ] 不从题目 ID生成规则。

基线产物只保留脱敏摘要。

## Task 3.1：QueryPlan 与候选生成

QueryPlan 至少表达：

- entity/subject；
- predicate；
- conditions；
- scope/organization；
- version/freshness；
- polarity；
- numeric dimension/unit；
- expected answer shape；
- cross-document requirement。

候选生成：

- 原问题始终保留；
- canonical terms 与有限 query variants；
- 多 variant embedding 批量执行；
- vector、passage FTS、title FTS 独立产出；
- 每个 channel 记录原始分数；
- 失败 channel 不清空其他 channel；
- internal fetch_k 与 public top_k 分离；
- 保留多 passage/多 document 必要多样性。

禁止：

- Golden 完整问法表；
- 知识 ID 特判；
- 固定答案短语映射；
- 无界 query expansion。

## Task 3.2：融合排序、版本与 Reranker

排序步骤固定：

1. channel normalization；
2. deterministic fusion；
3. entity/predicate/condition feature；
4. optional provider rerank；
5. version/family resolution；
6. diversity 与 public top_k。

要求：

- relevance 先于 freshness，防止最新但不相关文档置顶；
- 同 family 最新有效版本优先；
- 用户明确指定旧版本时不得强推最新；
-跨版本比较保留多个版本；
- document family/version metadata 全程传播；
- provider rerank 不可用时回到 deterministic，trace 如实标记；
- provider-enhanced 轨不得影响 deterministic 基线可用性。

针对机制：

- KB-009：组织范围 + 最新版本 + 多事实问题；
- KB-013：表格奖金上下限与口语表达；
- KB-026：号百分公司与广西公司范围区分；
- KB-036：新版本修订项。

这些 case 仅作回归证据，生产代码不得引用 case ID。

## Task 3.3：EvidenceSnapshot 与门禁

Snapshot 必须包含：

- query plan；
- candidate pool policy；
- accepted/rejected candidates；
- accepted passage IDs；
- adjacent passage IDs；
- generation passage IDs；
- family/version metadata；
- channel/fusion/rerank/final scores；
- threshold profile；
- rejection reasons；
- config/index/db/scorer fingerprints。

门禁分层：

- retrieval sufficiency；
- direct-slot evidence；
- cross-document coverage；
- version sufficiency；
- no-answer/out-of-domain；
- generation readiness。

要求：

- 不全局降低 threshold；
- title/entity/predicate/condition 强匹配可以形成显式 feature；
- gate 拒绝必须说明缺少哪个 slot；
- search accepted 而 ask no_answer 时必须能解释阶段差异；
- Search/Ask 共用 Snapshot 时 fingerprint 必须一致。

针对机制：

- KB-011：正确文档已命中但 evidence gate 误拒。

## Task 3.4：结构化事实与 Claim 抽取

事实模型：

```text
subject
predicate
object/value
unit
condition
scope
version
polarity
passage_id
exact_span_hash
confidence
extractor
```

抽取策略：

1. 确定性结构化抽取；
2. 表格/条款专用抽取；
3. 受约束 LLM JSON fallback；
4. provenance validator；
5. 未通过 validator 的 claim 不进入 AnswerPlan。

要求：

- 数值与单位绑定；
- 数值与条件绑定；
- 部门与职责绑定；
- 新旧版本事实分开；
- 不从相邻 passage 无条件拼接；
- LLM 只输出 schema，不允许自由 prose 直接成为最终答案。

## Task 3.5：AnswerPlan、跨文档与渲染

AnswerPlan 必须明确：

- required slots；
- selected claims；
- 每个 slot 的 supporting passage；
- missing slots；
- conflict/version handling；
- answer shape；
- clarification question。

支持：

- 单事实；
- 多条件组合；
- 多部门映射；
- 历年变化；
- 新旧规则冲突；
- 适用范围；
- 表格上下限。

渲染：

- 不截断句子；
- 不输出重复 bullet；
- 不混入无关 passage；
- 历年变化按版本列出；
- 部门映射使用明确标签；
- 缺 slot 时拒答或澄清，不输出半答案冒充完整答案。

针对机制：

- KB-020：阈值与实名登记率绑定；
- KB-022：涉诈/涉骚扰分别绑定部门；
- KB-024：多版本变化；
- KB-025：归口部门；
- KB-026：适用组织范围；
- KB-036：修订取消项。

## Task 3.6：Citation、Unsupported Assertion 与 No-answer

每个最终 claim 必须：

- 绑定 passage；
- passage 在 Snapshot generation allowlist；
- exact span 或 span hash 可复核；
- source knowledge/version 与 claim 一致。

最终验证顺序：

1. AnswerPlan coverage；
2. claim provenance；
3. citation allowlist；
4. version consistency；
5. forbidden assertion；
6. unsupported assertion；
7. no-answer/clarification contract。

任一强校验失败：

- 不静默删除关键句后继续返回；
- 返回结构化 `no_answer` 或 `clarification_required`；
- trace 中记录 validator 与 reason code。

针对机制：

- KB-032：无答案问题不得返回无关企业微信内容。

## Task 3.7：通用回归与 Anti-overfit

每个失败修复必须有：

1. 原失败题的 replay；
2. 同机制改写题；
3. 相邻反例；
4. 不同文档/实体的泛化样本；
5. anti-Golden 源码扫描。

扫描范围：

```powershell
rg -n "KB-0[0-9][0-9]|golden_set_hit_rate|完整 Golden 问法片段" src
```

`src/` 中不得命中 case ID、题库路径和完整问法。

## Task 3.8：性能、超时与资源恢复

- query variants 单次批量 embedding；
- Snapshot 复用不得重复检索；
- reranker circuit breaker 按 provider/profile 隔离；
- timeout 后线程/进程 slot 可恢复；
- provider enhanced 失败不影响 deterministic baseline；
- 每阶段记录 latency；
- P50/P95 与 Phase 3 基线对比，不允许无解释显著回退；
- 不通过提高全局 timeout 掩盖死锁或重复冷启动。

## Task 3.9：Phase 3 方向性验收

由于 frozen=0，下列只作为 engineering milestone，不是发布结论。

### Development milestone

| 指标 | 目标 |
|---|---:|
| 高风险 15 Recall@5 | ≥14/15 |
| 高风险 15 Ask Fact | ≥12/15 |
| 高风险 15 E2E | ≥12/15 |
| 当前 no-answer 5 False Positive | 0 |
| Citation lineage | 不低于 Phase 0 基线 |
| Forbidden Assertion | 0 |
| Snapshot reuse mismatch | 0 |
| P0/P1 机制回归 | 全绿 |

如未达到，报告 NO-GO 并保留失败，不得调数据。

### 正式放行门槛

仍沿用：

- frozen answerable ≥120；
- frozen no-answer ≥20；
- Ask Fact ≥90%；
- E2E ≥90%；
- Citation ≥95%；
- Recall@5 ≥95%；
- P0/P1 失败 0；
- False Positive 0；
- Unsupported Assertion 0。

正式数据未冻结前不得运行或声称通过。

## 7. 建议文件地图

| 文件/目录 | 责任 |
|---|---|
| `src/application/search_use_case.py` | Search 用例 |
| `src/application/ask_use_case.py` | Ask 用例 |
| `src/application/evidence_snapshot_service.py` | Snapshot 构建/加载/校验 |
| `src/application/ports.py` | 核心 ports |
| `src/retrieval/contracts.py` | typed retrieval contracts |
| `src/retrieval/pipeline.py` | 单一检索主管线 |
| `src/retrieval/candidate_generation.py` | 多通道候选 |
| `src/retrieval/ranking.py` | deterministic fusion/ranking |
| `src/retrieval/version_resolution.py` | family/version |
| `src/retrieval/evidence_gate.py` | 检索/生成准备门禁 |
| `src/answering/contracts.py` | Claim/Plan/Validation |
| `src/answering/pipeline.py` | 单一回答主管线 |
| `src/answering/fact_extraction.py` | 结构化事实 |
| `src/answering/answer_planner.py` | slots 与跨文档计划 |
| `src/answering/rendering.py` | 最终渲染 |
| `src/answering/validation.py` | citation/unsupported/no-answer |
| `src/mcp/tools/search.py` | Search adapter |
| `src/mcp/tools/ask.py` | Ask adapter |
| `src/mcp/tools/read.py` | Read adapter |
| `src/compatibility/legacy_rag.py` | 显式兼容入口 |
| `tests/application/` | UseCase 与 ports 测试 |
| `tests/retrieval/` | pipeline/ranking/version/gate |
| `tests/answering/` | fact/plan/render/validation |
| `tests/architecture/` | 依赖方向与唯一主管线 |

允许复用已有实现。不得为了符合文件地图机械复制代码；迁移后旧实现应成为 adapter、compatibility 或被安全删除。

## 8. 执行顺序

必须按顺序：

1. Task 2.0 全部收口；
2. Task 2.1 契约 ADR；
3. Task 2.2 强类型模型；
4. Task 2.3 UseCases/Ports；
5. Task 2.4 MCP adapter；
6. Task 2.5 单一主管线；
7. Task 2.6 Shadow/架构验收；
8. Task 3.0 方向性基线；
9. Task 3.1 QueryPlan/候选；
10. Task 3.2 排序/版本/reranker；
11. Task 3.3 Snapshot/gate；
12. Task 3.4 Fact/Claim；
13. Task 3.5 AnswerPlan/render；
14. Task 3.6 Citation/no-answer；
15. Task 3.7 泛化回归；
16. Task 3.8 性能恢复；
17. Task 3.9 方向性验收与报告。

Phase 2 未全绿不得进入 Phase 3。

## 9. 测试矩阵

### 9.1 Phase 2 入口

```powershell
python tools/report_closure_debt.py --strict
pytest tests/architecture tests/eval tests/services/test_passage_store_di.py -q
pytest tests/test_public_search_contract.py tests/test_public_ask_contract.py -q
pytest tests/test_search_service.py tests/test_wiki_serving_contract.py -q
pytest tests/stability/test_fts_no_answer_gate.py tests/test_mcp_gui_status.py -q
pytest tests/test_query_revolution_phase3.py -q
pytest tests/ -q
```

### 9.2 Phase 2 新架构

```powershell
pytest tests/application tests/retrieval tests/answering tests/architecture -q
pytest tests/mcp/test_hit_rate_regressions.py tests/mcp/test_hit_rate_v3_regressions.py -q
pytest tests/test_public_search_contract.py tests/test_public_ask_contract.py -q
```

### 9.3 Phase 3 质量

```powershell
pytest tests/retrieval tests/answering tests/mcp -q
pytest tests/answering/test_hit_rate_v7_anti_overfit.py -q
python scripts/hit_rate_test_harness.py --golden evals/golden_set_hit_rate.json --out .local/eval-runs/phase3-dev --rerank-profile deterministic-baseline
python scripts/hit_rate_finalize.py --golden evals/golden_set_hit_rate.json --out .local/eval-runs/phase3-dev
```

运行 V1 legacy development 数据时必须在报告中标记 non-formal，不得冒充 frozen V2。

### 9.4 最终工程门禁

```powershell
ruff check .
mypy src evals/hit_rate_v2
pytest tests/ -q
cd client
npm run build
cd ..
python tools/report_closure_debt.py --strict
```

## 10. Phase 2 完成条件

- Task 2.0 全部完成；
- 全量 pytest 0 failed；
- formal freeze/review/corpus 校验真实有效；
- public contract 有 ADR，snapshot 无未解释漂移；
- typed contracts 已进入正常主管线；
- Search/Ask 使用同一 RetrievalPipeline/Snapshot；
- MCP 不再承载业务编排或私有调用；
- legacy 正常路径已隔离；
- Shadow parity 无未解释差异；
- development 指标不低于 Phase 0；
- 数据库 hash 不变。

## 11. Phase 3 完成条件

- query/retrieval/ranking/version/gate/fact/plan/render/validation 各阶段职责明确；
- 失败修复均有通用正反例，无 Golden 特判；
- development milestone 达到或诚实报告未达；
- no-answer false positive 为 0；
- Snapshot reuse mismatch 为 0；
- deterministic baseline 可重复；
- provider enhanced 状态诚实；
- 全量 pytest、ruff、mypy、前端构建、架构 strict 全绿；
- frozen=0 时结论仍为 NO-GO，不宣称发布。

## 12. 交付报告

创建：

```text
docs/evaluation/mcp-hit-rate-phase2-phase3-report.md
```

至少包含：

1. 实际起止 HEAD、branch、工作区状态；
2. Phase 0–1 补充项处理结果；
3. 10 个全量失败的根因与处置；
4. ADR 与公开契约决定；
5. 新旧调用链对比；
6. 架构门禁和文件规模；
7. development 指标前后对比；
8. 每个失败 case 的阶段归因；
9. provider profile 与性能；
10. candidates/reviewed/frozen 实际数量；
11. 数据库前后 SHA256；
12. 全部测试命令与精确结果；
13. 未完成项和人工阻塞；
14. 是否满足进入后续正式验收阶段。

最终结论只能是：

```text
Phase 2–3 工程完成；仍需 frozen V2 正式验收，保持 NO-GO 发布
```

或：

```text
Phase 2–3 未完成，不得进入正式验收
```

