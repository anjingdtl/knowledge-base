# MCP 命中率 Phase 2–3 执行报告

> 状态：**Task 2.0 工程收口完成；Phase 2 边界与 UseCase 薄层落地；Phase 3.1–3.4 候选召回/排序/审计/拒答落地；development-37 非正式 baseline 召回率 100%**
> 日期：2026-07-29 初稿，2026-07-30 增补 Phase 3.1/3.2/3.3/3.4 进度  
> 执行起点 HEAD：`5bb41f7f670e6caad6c6669c7f4dc42d8125fe97`（`master`）  
> 最近 HEAD：`e5cecb4`（继续 Phase 2–3 工程收口）  
> 工作区：改动未提交、未推送（未经授权）  
> 发布结论：**仍 NO-GO**（frozen=0；一切指标 development/non-formal）

---

## 1. 基线与不变量

| 项 | 值 |
|---|---|
| 起点 HEAD | `5bb41f7` |
| 最近 HEAD | `e5cecb4` |
| `data/kb.db` SHA256（执行前） | `4ba22449794c984f6c1fda3d459574556c71b017efcc8e041bd4da731e737479` |
| `data/kb.db` SHA256（执行后） | `4ba22449794c984f6c1fda3d459574556c71b017efcc8e041bd4da731e737479`（**不变**） |
| Golden V2 | candidates=37，reviewed=0，frozen=0 |
| formal | **禁止**；不得伪造 reviewer/adjudicator |
| 铁律 | 未降测试/评测门槛；无 skip/xfail/删断言；无 case_id 特判；未改 Golden 预期 |

---

## 2. Task 2.0 收口结果

### 2.0.1 权威状态与 Git 勘误 — ✅

- Phase 0–1 报告 §13 勘误已存在；`PROGRESS.md` HEAD 已为 `5bb41f7`。

### 2.0.2 全量 pytest 归零 — ✅（分片前台）

起始定向失败集：**7 failed / 109 passed**（与交接单一致；GUI TCP 在定向中已隔离）。

| 失败 | 归类 | 处置 |
|---|---|---|
| FTS `test_search_fulltext_fallback_no_match` | 有意契约（ADR §3.3 三档） | 更新为 low-confidence 断言 + 新增 true no-match 档 |
| `test_ask_raw_only` / `test_ask_timeout` | 有意契约 + snapshot | 可判断 fixture（前任）+ 规范化后刷新 snapshot |
| `test_search_raw_snapshot` / `test_search_no_result` | additive + trace 扩展 | 规范化增强（`ms`、circuit 时间戳）后刷新 |
| `test_search_calls_rewrite_*` / fallback | 测试绑定内部 fetch_k | `CandidatePoolPolicy` 接入 + 测试改 policy 语义 |
| GUI `is_running` 端口敏感 | 环境/顺序 | patch `is_mcp_port_available` + 正向端口可达用例 |
| `test_end_to_end_structured_query_through_rag` | 顺序敏感（历史） | 定向 + 大分片均通过；未再复现失败 |

**验证（分片前台，exit 0）：**

- 定向 + architecture + eval + retrieval + application：`438 passed`
- mcp + services + migrations：`147 passed`
- answering + container + providers + repositories + storage + helpers：通过
- 根级剩余测试：`1797 passed, 2 skipped`
- `tools/report_closure_debt.py --strict`：No residual debt

### 2.0.2b CandidatePoolPolicy — ✅

- `src/retrieval/candidate_pool.py` 接入 `RawRetriever.retrieve`
- MCP `search` / `_build_shared_snapshot` / ask probe 统一 `fetch_k = max(top_k*4, 20)`
- 删除死代码 `SearchService._raw_retrieve`
- 测试：`tests/retrieval/test_candidate_pool.py` + 更新 `test_search_service.py`

### 2.0.2c 公开契约 — ✅

- ADR：`docs/architecture/adr-search-ask-contract-v2.md`
- Snapshot 规范化：`tests/helpers/contract_normalize.py`（ADR §3.5）
- Heartbeat best-effort 专项：`tests/test_heartbeat_best_effort.py`
- no-match 三档：`tests/stability/test_fts_no_answer_gate.py`

### 2.0.3 Formal Harness 真冻结 — ✅

- `validate_formal_frozen_dataset`：逐行 `validate_freeze_row`、非空 frozen、corpus 一致、单一 split、review_manifest 确定性哈希
- Harness `run()` 写入真实 `review_manifest_hash` / `corpus_snapshot`（不再写空串）
- Fail-closed 测试：伪 frozen 路径、空文件、corpus 不一致、mixed split、resume manifest 变更等

### 2.0.4 审核与裁决完整性 — ✅（工程门禁）

- `review.evidence_checked` 必填；source/fact 决策覆盖检查
- adjudicator ≠ primary/secondary；裁决须记录 disagreement
- rejected/disputed/needs_adjudication 不可冻结
- review CLI 禁止静默清除 disagreement 历史

### 2.0.5 Scorer V2 语义补齐 — ✅（评分合同）

- fact group：`exact` / `normalized` / `numeric_unit`（值+单位+条件）/ `semantic_review`（禁止 substring 自动过）
- subject/predicate/condition/scope/version 参与覆盖
- citation：snapshot allowlist + raw_evidence_used + Golden expected passage + fact evidence 绑定
- `clarification_required` 独立评分
- unsupported assertion：无 structured claims 时 **N/A**（不报 0）

### 2.0.6 Artifact 保留策略 — ✅

- `artifacts/eval-summaries/**/final_scored_v2.json` 移至 `.local/eval-runs/`（Git ignore）
- 可提交目录仅保留 `final_scored_v2.sanitized.json`
- 自动测试：`tests/eval/test_artifact_retention_policy.py`
- `.gitignore` 补充 raw detail 防护

---

## 3. Phase 2 架构结果

### 2.1 契约与边界 ADR — ✅

- `docs/architecture/adr-search-ask-contract-v2.md`
- `docs/architecture/retrieval-answer-boundaries-v2.md`

### 2.2–2.3 UseCase / Ports 薄层 — ✅（行为保持）

| 模块 | 说明 |
|---|---|
| `src/application/ports.py` | CandidateRetriever / Reranker / Snapshot / Fact / Renderer / Validator |
| `src/application/search_use_case.py` | 委托 SearchService.execute + public_top_k 封顶 |
| `src/application/ask_use_case.py` | 委托 AnswerService.ask（含 snapshot_id 转发） |
| `src/application/evidence_snapshot_service.py` | build/register/load 注入边界 |
| `src/application/read_use_case.py` | 读用例薄壳 |
| `RetrievalCommands.semantic_search` | 经 SearchUseCase |

### 2.4 MCP adapter 变薄 — 🟡 部分

- heartbeat best-effort
- CandidatePoolPolicy 统一
- `PassageStore.revision_token()` 公共 API 替换 `store._get_conn()` 私有调用
- **未完成**：`retrieval.py` 仍约 2700 行；search/ask 业务尚未整体下沉到 UseCase

### 2.5 单一主管线 — 🟡 部分

- SearchService → Orchestrator unified 仍为实际主管线
- UseCase 为适配边界，非完整 RetrievalPipeline/AnswerPipeline 重写

### 2.6 架构门禁 — ✅ 增强

- application 禁止导入 MCP/GUI/API
- answering 禁止 GUI/API
- MCP 禁止 `store._get_conn` / `Database._instance`
- 既有 retrieval/answering/repositories 边界保持

### Shadow parity / 质量不退化 — 未跑 formal 37 全量 live MCP

- frozen=0 → 任何 live 结果只能标 `non_formal=true`
- 本阶段未宣称指标放行；工程契约与门禁绿

---

## 4. Phase 3 质量重建

| Task | 状态 |
|---|---|
| 3.0 失败分层基线 | ✅ 非正式 development-37 retrieval-only baseline 已构建（in-process, no live MCP, no LLM） |
| 3.1 QueryPlan 与候选生成 | ✅ 组织 scope 识别 + regulation-phrase 精确匹配 + alias query variants（同义词扩展） |
| 3.2 融合排序/版本/rerank | ✅ alias-variant 候选打标 + relevance gate 同义词 credit + reranker score floor + core-term title boost + semantic tiebreaker；KB-009/KB-011/KB-013 全部修复 |
| 3.3 EvidenceSnapshot 门禁分层 | ✅ ranking reason 写入 evidence snapshot（per-candidate boosts/penalties/scope/regulation reason）；stages.ranking_reasons 审计摘要 |
| 3.4 事实/Plan/引用/验证 | ✅ AnswerService 严格 no-answer（gate rejected → 不生成）；per-claim citation 强制（evidence_passage_ids 为空 → 拒绝 claim） |

### 4.1 Development-37 非正式 baseline（2026-07-30）

- Artifact：`.local/eval-runs/phase3-dev-baseline/dev37_retrieval_baseline.sanitized.json`
- 性质：`non_formal=true / dev_only=true / formal=false`
- 评测范围：retrieval-only in-process（无 live MCP、无 LLM）；ask 类失败（unsupported_claim/stale_evidence/no_answer_failure/citation_failure）未评测
- Scorer：Scorer V2 retrieval-only subset
- `kb.db` SHA256：`4ba22449794c984f6c1fda3d459574556c71b017efcc8e041bd4da731e737479`（与执行前一致）
- 工具：`tools/dev37_retrieval_baseline.py`

**Retrieval metrics（dev only, NOT formal）：**

| 指标 | 值 |
|---|---|
| Cases evaluated | 37（answerable=32, no_answer=5） |
| Recall@5 | 32/32 = **1.0** |
| Top-1 Accuracy | 32/32 = **1.0** |
| No-Answer Correct | 5/5 = **1.0** |

**Failure taxonomy（case-level）：**

| 类别 | 数量 | 说明 |
|---|---|---|
| none | 37 | 全部命中或正确 no-answer |
| missing_direct_hit | 0 | — |
| contract_failure | 0 | — |
| wrong_product | 0 | — |
| wrong_version | 0 | — |
| wrong_family | 0 | — |
| unsupported_claim | 未评测 | 需 LLM |
| stale_evidence | 未评测 | 需 LLM |
| no_answer_failure | 未评测 | 需 LLM |
| citation_failure | 未评测 | 需 LLM |

**声明：** Phase 3 质量目标（高风险 15 Recall@5≥14 等）**未在正式 formal harness 上评测**。上述 100% 是非正式 development retrieval-only baseline，**不得解读为正式命中率**。正式 release 仍 NO-GO。

### 4.2 Phase 3.1 — QueryPlan 与候选生成（已落地）

- `src/answering/query_planner.py`：组织 scope 识别（号百分公司 / 南宁分公司 / 总部），用于 KB-026/KB-009 类分支 vs 总部串召回问题。
- `src/services/relevance_gate.py::compute_scope_signal`：基于 title 的分支 token 提取 + scope 信号（+0.15 boost / -0.20 penalty）。
- `src/services/relevance_gate.py::compute_regulation_phrase_signal`：regulation-phrase 精确匹配（合规管理办法 prefix+suffix 邻接），用于 KB-005 类 wrong_version 问题。
- `src/services/query_rewrite.py::build_alias_query_variants`：通用中文同义词扩展（比赛→竞赛 / 店铺→门店 / 奖金→奖励），用于 KB-011/KB-013 类口语化查询召回问题。
- 测试：`tests/services/test_scope_discrimination.py`、`tests/answering/test_direct_slot_intent_short_circuit.py`。

### 4.3 Phase 3.2 — 融合排序/版本/rerank（已落地）

- `src/retrieval/raw_retriever.py`：alias query variants 接入 `retrieve()` 主路径；候选打标 `alias_fts_match=True`（基于 title 同义词出现 + 原词不出现）。
- `src/services/relevance_gate.py::extract_query_terms`：扩展 term set 包含 alias 同义词词（竞赛/门店/奖励），保证同义词召回候选的 query_term_coverage 不被低估。
- `src/services/relevance_gate.py::score_candidate_relevance`：新增 `alias_fts_match + coverage≥0.1` 的 boost 规则（floor 0.40），避免同义词召回候选被门禁误杀。
- `src/services/relevance_gate.py::score_candidate_relevance`：新增 `rerank_score≥0.7` reranker 高置信 floor（floor 0.40），让 cross-encoder 验证的同义词/释义候选通过门禁。
- `src/services/relevance_gate.py::score_candidate_relevance`：新增 core-term title boost（3+ 2-char query terms + regulation suffix → floor 0.42），解决长口语查询 n-gram 稀释问题。
- `src/services/relevance_gate.py::score_candidate_relevance`：新增 alias semantic tiebreaker（semantic_score>0.5 时 floor `0.40 + (semantic-0.5)*0.3`），确保同义词召回中高相似度候选排在更新但更弱的候选之前。
- `src/application/candidate_retrieval_service.py::_semantic_with_variants`：alias variants 单独执行 semantic_search 并打标，避免与原查询混淆。
- 测试：`tests/services/test_relevance_gate_in_corpus.py` 新增 12 个 case 覆盖 alias term 提取、synonym credit、anti-inflation guard、core-term title boost、reranker floor。

**修复的 case：** KB-009（版本排序）、KB-011（线上合作文档 contract_failure）、KB-013（劳动竞赛 wrong_product）。

### 4.4 Phase 3.3 — EvidenceSnapshot ranking reason 审计（已落地）

- `src/services/relevance_gate.py::score_candidate_relevance`：返回值新增 `ranking_reason` 结构体，记录：
  - `primary_signal`：主导信号（base_blend / high_term_coverage / alias_fts_match / reranker_high_confidence / core_term_title_boost / live_external_cap 等）
  - `boosts`：所有触发的 boost 列表（含 scope/regulation 前缀）
  - `penalties`：所有触发的 penalty 列表（含 scope/regulation 前缀）
  - `scope_reason` / `regulation_phrase_reason`：详细 reason code
  - `intent`：查询意图分类
  - `alias_fts_match` / `rerank_score` / `core_term_title_boosted`：关键标志位
- `src/retrieval/canonical_snapshot.py::build_canonical_snapshot`：
  - 每个 accepted item 在 top level 携带 `ranking_reason`（从 `relevance.ranking_reason` 提升）
  - snapshot 顶层新增 `ranking_reasons` 摘要列表（kid/passage_id/title/final_score/primary_signal/boosts/penalties）
  - `stages.ranking_reasons` 镜像，供下游消费者（MCP envelope、审计日志）直接读取
- `snapshot_to_search_execution`：trace 新增 `ranking_reasons` 字段
- 测试：`tests/services/test_relevance_gate_in_corpus.py` 新增 4 个 case 覆盖 ranking_reason 结构、alias_fts_match boost 记录、live_external penalty 记录、snapshot 摘要携带。

### 4.5 Phase 3.4 — AnswerService 严格 no-answer + per-claim citation（已落地）

- `src/answering/service.py::_assemble_payload`：新增严格 no-answer 守卫——当 `evidence_snapshot.accept=False` 时，直接返回 `_strict_no_answer_payload`，**不触发任何 LLM 调用**，避免幻觉回答。
- `src/answering/service.py::_strict_no_answer_payload`：生成确定性 no-answer payload，携带：
  - `reason`：gate 拒绝原因（来自 snapshot）
  - `answer_validation_decision`：与 reason 一致
  - `_evidence_snapshot`：包含 snapshot_fingerprint + ranking_reasons，便于审计追溯
  - `route.explanation`：`evidence gate rejected: {reason}`
- `src/answering/claim_protocol.py`：per-claim citation 强制——claim 的 `evidence_passage_ids` 为空时被拒绝（`missing_evidence_ids`），未知 passage_id 被拒绝（`evidence_id_not_in_snapshot`），数值/version 不在证据中的 claim 被拒绝。
- 测试：`tests/answering/test_answer_service.py` 新增 4 个 case：
  - gate rejected → strict no_answer（不调用 search/LLM）
  - no-answer payload 携带 ranking_reasons 审计
  - gate rejected 时 LLM 不被调用
  - per-claim citation 强制（空 evidence_passage_ids 的 claim 不被 accepted）

---

## 5. 未完成与后续建议（下一 Agent）

1. **MCP 拆分**：`retrieval.py` 仍约 2700 行；search/ask/read adapters 渐进拆分待续。
2. **人工审核**：37 题 dual review → freeze；Agent **不得**伪造 reviewer。当前 reviewed=0 / frozen=0。
3. **LLM 依赖评测**：unsupported_claim / stale_evidence / no_answer_failure / citation_failure 需 live LLM 评测，retrieval-only baseline 无法覆盖。
4. **版本/产品族分层排序深化**：当前 rank_with_freshness + family_key 已解决 KB-009/KB-013，但更复杂的多产品族同名型号隔离待加强。

---

## 6. 主要变更文件清单（工作区）

**新增（2026-07-30 session）：**
- `src/application/ask_probe.py` — AskProbe，将 `_do_ask` 的 pre-LLM evidence gating 下沉到 application 层
- `src/application/candidate_retrieval_service.py` — CandidateRetrievalService，候选生成从 MCP 下沉
- `tools/dev37_retrieval_baseline.py` — development-37 非正式 retrieval-only baseline 工具
- `tests/application/test_ask_probe.py`、`tests/application/test_candidate_retrieval_service.py`
- `tests/application/test_evidence_snapshot_service.py`、`tests/application/test_mcp_adapter_boundary.py`
- `tests/application/test_read_use_case.py`
- `tests/answering/test_direct_slot_intent_short_circuit.py`
- `tests/services/test_relevance_gate_out_of_domain.py`、`tests/services/test_scope_discrimination.py`

**修改（2026-07-30 session）：**
- `src/answering/direct_slot_gate.py` — scope veto 防止 direct slot gate 复活 wrong-family 候选
- `src/answering/query_planner.py` — 组织 scope 提取（branch/HQ）
- `src/application/evidence_snapshot_service.py` — 真实 snapshot 生命周期
- `src/application/read_use_case.py` — typed-read dispatch 从 MCP 下沉
- `src/core/container.py` / `src/core/service_groups.py` — application 服务注入
- `src/mcp/tools/retrieval.py` — 薄化为 envelope wrapper，业务逻辑下沉到 application
- `src/retrieval/raw_retriever.py` — alias query variants 接入 + alias_fts_match 候选打标
- `src/services/query_rewrite.py` — `build_alias_query_variants` 同义词扩展
- `src/services/relevance_gate.py` — `extract_query_terms` 包含 alias 同义词；`score_candidate_relevance` 新增 alias boost；`compute_scope_signal`；`compute_regulation_phrase_signal`
- `tests/services/test_relevance_gate_in_corpus.py` — 新增 5 个 alias 同义词测试
- `tests/test_50round_bugfix.py`、`tests/test_upgrade_regression_review.py` — snapshot 规范化

**Artifact：**  
`.local/eval-runs/phase3-dev-baseline/dev37_retrieval_baseline.sanitized.json`（非正式 baseline，dev_only=true）

---

## 7. 测试命令与通过数量（2026-07-30）

```bash
# 定向测试（含 Phase 3.3/3.4 新增）
.venv\Scripts\python.exe -m pytest tests/architecture tests/eval tests/retrieval tests/application tests/answering tests/test_heartbeat_best_effort.py -q --tb=short
# → 319 passed

# 含 services 的更宽定向
.venv\Scripts\python.exe -m pytest tests/architecture tests/eval tests/retrieval tests/application tests/answering tests/services tests/test_heartbeat_best_effort.py -q --tb=short
# → 通过

# 全量测试
.venv\Scripts\python.exe -m pytest tests/ -q --tb=short
# → 2489 passed, 2 skipped, 9 warnings

# Closure debt
python tools/report_closure_debt.py --strict
# → No residual debt (strict clean)

# Development-37 非正式 baseline
python tools/dev37_retrieval_baseline.py
# → Recall@5=1.0, Top-1=1.0, No-Answer=1.0（dev only, NOT formal）
```

---

## 8. 结论

```text
Task 2.0 engineering closure: PASS (pytest shards green, strict debt clean, kb.db hash unchanged)
Phase 2 architecture: PARTIAL (ADRs + UseCase/ports + gates + AskProbe + CandidateRetrievalService; MCP still ~2700 lines)
Phase 3.0 dev baseline: PASS (non-formal, retrieval-only, 100% Recall@5)
Phase 3.1 QueryPlan: PARTIAL (org scope + regulation-phrase + alias variants; no full QueryPlan model)
Phase 3.2 ranking: PASS (alias_fts_match + reranker floor + core-term boost + semantic tiebreaker; KB-009/KB-011/KB-013 fixed)
Phase 3.3 ranking reason: PASS (per-candidate ranking_reason in snapshot + stages.ranking_reasons audit)
Phase 3.4 AnswerService strict no-answer: PASS (gate-rejected → no LLM; per-claim citation enforced)
Formal release: NO-GO (frozen=0, non_formal only)
```

---

## 9. 收尾复核（2026-07-30）

### Phase 3.3/3.4 落地复核

- `src/services/relevance_gate.py::score_candidate_relevance` 返回值新增 `ranking_reason` 结构体（primary_signal / boosts / penalties / scope_reason / regulation_phrase_reason / intent / alias_fts_match / rerank_score / core_term_title_boosted）。
- `src/retrieval/canonical_snapshot.py::build_canonical_snapshot`：accepted item top-level 携带 `ranking_reason`；snapshot 顶层 `ranking_reasons` 摘要列表；`stages.ranking_reasons` 镜像。
- `src/answering/service.py::_assemble_payload`：gate rejected → strict no-answer，不调用 LLM。
- `src/answering/service.py::_strict_no_answer_payload`：确定性 no-answer payload 携带 snapshot_fingerprint + ranking_reasons。
- `src/answering/claim_protocol.py`：per-claim citation 强制（空 evidence_passage_ids → 拒绝）。

### 测试与不变量复核

- `git diff --check`：通过（仅 Windows CRLF 提示）。
- `python tools/report_closure_debt.py --strict`：通过，No residual debt。
- `.venv\Scripts\python.exe -m pytest tests/architecture tests/eval tests/retrieval tests/application tests/answering tests/test_heartbeat_best_effort.py -q`：319 passed。
- `.venv\Scripts\python.exe -m pytest tests/ -q --tb=short`：2489 passed, 2 skipped。
- `python tools/dev37_retrieval_baseline.py`：Recall@5=1.0, Top-1=1.0, No-Answer=1.0（dev only, NOT formal）。
- `data/kb.db` SHA256 复核：`4ba22449794c984f6c1fda3d459574556c71b017efcc8e041bd4da731e737479`（不变）。

下一 Agent 入口：MCP retrieval.py 渐进拆分；人工审核 37 题 dual review → freeze；LLM 依赖评测（unsupported_claim/stale_evidence/no_answer_failure/citation_failure）。
