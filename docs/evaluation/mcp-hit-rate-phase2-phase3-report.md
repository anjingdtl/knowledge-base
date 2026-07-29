# MCP 命中率 Phase 2–3 执行报告

> 状态：**Task 2.0 工程收口完成；Phase 2 边界与 UseCase 薄层落地；Phase 3 质量重建未完成（仅地基）**  
> 日期：2026-07-29  
> 执行起点 HEAD：`5bb41f7f670e6caad6c6669c7f4dc42d8125fe97`（`master`）  
> 工作区：改动未提交、未推送（未经授权）  
> 发布结论：**仍 NO-GO**（frozen=0；一切指标 development/non-formal）

---

## 1. 基线与不变量

| 项 | 值 |
|---|---|
| 起点 HEAD | `5bb41f7` |
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
| 3.0 失败分层基线 | ❌ 未执行 live development-37 harness |
| 3.1 QueryPlan 与候选生成 | 🟡 CandidatePoolPolicy + query variants 已有；无完整 QueryPlan 模型接入 |
| 3.2 融合排序/版本/rerank | ❌ 未改算法 |
| 3.3 EvidenceSnapshot 门禁分层 | 🟡 共享 snapshot 边界服务已建；门禁分层未重建 |
| 3.4–3.9 事实/Plan/引用/验证 | 🟡 Scorer V2 语义已加强；生产 AnswerPipeline 未按 Phase 3 重建 |

**声明：** Phase 3 质量目标（高风险 15 Recall@5≥14 等）**未达成、未评测**。不得将本报告解读为命中率提升或正式放行。

---

## 5. 未完成与后续建议（下一 Agent）

1. **MCP 拆分**：`retrieval.py` → search/ask/read adapters + application snapshot service 真接线。  
2. **强类型模型**：`QueryPlan` / `EvidenceSnapshot` / `AnswerClaim` 贯通 pipeline（非仅 policy）。  
3. **Phase 3 算法**：query plan、version/family ranking、direct-slot gate 可解释性、跨文档 AnswerPlan。  
4. **non-formal development-37**：deterministic-baseline + Scorer V2 脱敏摘要（不得 formal）。  
5. **人工审核**：37 题 dual review → freeze；Agent **不得**伪造 reviewer。

---

## 6. 主要变更文件清单（工作区）

**新增：** ADR 两份、plan/handoff、`candidate_pool.py`、application UseCases/ports、artifact/heartbeat/candidate_pool 测试。

**修改：** raw_retriever、MCP retrieval/support、AnswerService（前任）、validation/scoring、harness、review CLI、contract snapshots、freeze/scoring 测试、`.gitignore`、PROGRESS。

**Artifact：**  
`artifacts/eval-summaries/phase0_rescore_attempt20/final_scored_v2.json` → `.local/eval-runs/...`（未脱敏移出可提交目录）。

---

## 7. 结论

```text
Task 2.0 engineering closure: PASS (pytest shards green, strict debt clean, kb.db hash unchanged)
Phase 2 architecture: PARTIAL (ADRs + UseCase/ports + gates; MCP still fat)
Phase 3 quality rebuild: NOT STARTED (scoring contract only)
Formal release: NO-GO (frozen=0, non_formal only)
```

---

## 8. 下班前收尾复核（2026-07-29）

- 修正交接单：`docs/superpowers/handoffs/2026-07-29-hit-rate-phase2-phase3-task2.0-handoff.md` 已从旧的“Task 2.0 约完成 30%”改为当前状态摘要，避免明天按过期清单执行。
- 清理格式噪音：移除 `.gitignore` 文件尾多余空行与 `PROGRESS.md` 头部尾随空格。
- `git diff --check`：通过，仅有 Windows CRLF 提示。
- `python tools/report_closure_debt.py --strict`：通过，No residual debt。
- `.venv\Scripts\python.exe -m pytest tests/architecture tests/eval tests/retrieval tests/application tests/test_heartbeat_best_effort.py -q --tb=short`：`188 passed`。
- `data/kb.db` SHA256 复核：`4ba22449794c984f6c1fda3d459574556c71b017efcc8e041bd4da731e737479`。

明天继续入口：先补跑更大 pytest 分片或全量，再进入 Phase 2 剩余的 MCP adapter 拆薄与 Phase 3 development-37 non-formal baseline。
