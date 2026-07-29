# MCP 命中率 Phase 0–1 执行报告

> 状态：**Phase 0 工程完成；Phase 1 工程完成；正式数据集冻结被人工审核阻塞**  
> 执行日期：2026-07-29  
> 执行基线 HEAD：`19195d473727b657b9b563bce0029f94363ef459`（`master`）  
> 权威方案：`docs/superpowers/plans/2026-07-29-hit-rate-phase0-phase1-quality-foundation.md`  
> 结论（只能二选一）：**Phase 0–1 完成，可进入 Phase 2；仍不具备发布条件**  
> 人工阻塞声明：
>
> ```text
> Phase 1 engineering complete; formal dataset freeze blocked by human review.
> ```

**未提交、未推送、未创建 PR**（除非任务发起人另行明确授权）。

---

## 1. 执行前基线（Task 0.1）

| 项目 | 值 |
|---|---|
| Branch | `master` |
| HEAD | `19195d473727b657b9b563bce0029f94363ef459` |
| Python | 3.14.6 |
| 工作区初始 | 仅未跟踪方案文件 `docs/superpowers/plans/2026-07-29-hit-rate-phase0-phase1-quality-foundation.md` |
| `data/kb.db` size | 960905216 bytes |
| `data/kb.db` mtime | 2026-07-29T12:00:04.5579048+08:00 |
| `data/kb.db` SHA256 | `4ba22449794c984f6c1fda3d459574556c71b017efcc8e041bd4da731e737479` |
| Golden V1 hash (prefix) | `649be48fee565836…`（文件未改写） |
| attempt 20 旧指标 | Top-1 80% / R@5 86.67% / Ask Fact 46.67% / Citation 86.67% / E2E 40% / FP Rate 0% |
| 工程门禁基线 | 36 passed / **6 failed**（1 架构债 + 5 版本一致性） |
| closure debt strict | 失败：`Database._instance outside infra refs=1`（`passage_store.py`） |

数据库全程只读；未写库。

---

## 2. 变更文件与职责

### Phase 0

| 文件 | 动作 | 职责 |
|---|---|---|
| `src/services/passage_store.py` | 修改 | 移除私有 `Database._instance` 访问；仅注入 DB 或公开 `Database.get_conn()` |
| `tests/services/test_passage_store_di.py` | 新建 | DI / facade / 源码门禁测试 |
| `src/version.py` | 核对 | 权威版本 **1.11.1**（未改） |
| `README.md` / `README_zh.md` | 修改 | 版本徽章 → 1.11.1 |
| `client/package.json` / `client/package-lock.json` | 修改 | 前端版本 → 1.11.1 |
| `tests/architecture/test_version_consistency.py` | 修改 | `EXPECTED_VERSION=1.11.1` |
| `evals/hit_rate_v2/__init__.py` | 新建 | V2 评测包 |
| `evals/hit_rate_v2/models.py` | 新建 | 评分/聚合模型 |
| `evals/hit_rate_v2/scoring.py` | 新建 | **唯一评分权威**（`metric_contract_version=2.0`） |
| `evals/hit_rate_v2/rerank_profiles.py` | 新建 | deterministic-baseline / provider-enhanced 口径 |
| `evals/hit_rate_v2/sanitize.py` | 新建 | 原始产物脱敏 |
| `scripts/hit_rate_score.py` | 改写 | 薄 CLI，委托 V2 scorer |
| `scripts/hit_rate_finalize.py` | 改写 | 薄汇总 CLI，委托 V2 scorer |
| `scripts/hit_rate_artifact_sanitize.py` | 新建 | 脱敏 CLI |
| `scripts/hit_rate_test_harness.py` | 修改 | `--rerank-profile`、`--formal`、manifest 扩展字段 |
| `.gitignore` | 修改 | ignore `.local/eval-runs/` 等原始运行目录 |
| `tests/eval/test_hit_rate_v2_scoring.py` | 新建 | 答案/检索/引用分离与 hallucination 口径 |
| `tests/eval/test_hit_rate_v2_no_answer.py` | 新建 | KB-032 形态漏判修复 |
| `tests/eval/test_hit_rate_v2_rerank_profile.py` | 新建 | rerank 口径 |
| `tests/eval/test_hit_rate_artifact_sanitization.py` | 新建 | 脱敏测试 |
| `artifacts/eval-summaries/phase0_rescore_attempt20/*` | 新建 | attempt20 离线重评分脱敏摘要 |

### Phase 1

| 文件 | 动作 | 职责 |
|---|---|---|
| `schema/hit-rate-golden-v2.schema.json` | 新建 | Golden V2 JSON Schema |
| `evals/hit_rate_v2/validation.py` | 新建 | Schema / review / freeze / split / formal path 门禁 |
| `scripts/migrate_hit_rate_golden_v2.py` | 新建 | V1→V2 candidates 确定性迁移 |
| `scripts/review_hit_rate_ground_truth.py` | 新建 | 双人审核 CLI（不伪造 reviewer） |
| `scripts/freeze_hit_rate_ground_truth.py` | 新建 | 严格冻结门禁 |
| `tests/eval/datasets/hit_rate/candidates/golden_v1_migrated.jsonl` | 新建 | 37 条 candidates |
| `tests/eval/datasets/hit_rate/reviewed/.gitkeep` | 新建 | 空 reviewed |
| `tests/eval/datasets/hit_rate/frozen/.gitkeep` | 新建 | 空 frozen（正式入口待人工） |
| `tests/eval/test_hit_rate_v2_schema.py` | 新建 | Schema 测试 |
| `tests/eval/test_hit_rate_v2_freeze_gate.py` | 新建 | freeze + formal 路径测试 |
| `tests/eval/test_hit_rate_v2_split_isolation.py` | 新建 | split 隔离测试 |
| `docs/evaluation/mcp-hit-rate-phase0-phase1-report.md` | 新建 | 本报告 |
| `PROGRESS.md` / `docs/README.md` | 修改 | 权威状态与入口 |

**未改写：** `evals/golden_set_hit_rate.json`（legacy regression 保留）。  
**未改：** 生产检索/回答算法、MCP 工具名/参数/Envelope、评分阈值门槛、知识库内容。

---

## 3. 关键设计决定

1. **单一 scorer 权威**：核心逻辑只在 `evals/hit_rate_v2/scoring.py`；旧脚本退化为 CLI。
2. **Hallucination Rate**：V2 输出 `null` + `not_fully_measurable`；禁止用 forbidden substring 代理伪装完整幻觉率。代理指标显式命名 **Forbidden Assertion Rate**。
3. **No-answer 合同**：`raw_only`/`verified` 等回答模式 + 非空实质性断言 + sources 支持确定性答案 → `false_positive=true`（即使未命中 forbidden 字面量）。
4. **Rerank profile**：`deterministic-baseline` 与 `provider-enhanced` 分轨；后者不可用时 `blocked`，不得改名为 normal。
5. **治理复用**：审核/冻结规则对齐 production-pilot（双人不同 reviewer、ISO8601、corpus 一致、争议需裁决）；hit-rate 独立数据根 `tests/eval/datasets/hit_rate/`。
6. **V1 暴露集**：KB-001…KB-037 一律 `development`（不得 holdout）。
7. **人工审核不可伪造**：Agent 不写 reviewer / reviewed_at / adjudicator；冻结保持空。

---

## 4. Phase 0 工程门禁前后

| 检查 | 执行前 | 执行后 |
|---|---|---|
| `report_closure_debt.py --strict` | fail（outside refs=1） | **pass（strict clean）** |
| `test_version_consistency` | 5 failed | **pass** |
| PassageStore DI 测试 | n/a | **pass** |
| 版本元数据 | src=1.11.1，其余 1.11.0 | **全部 1.11.1** |

---

## 5. Scorer V1 vs V2（attempt 20 离线重评分）

输入：`artifacts/hit_rate_test_v7/tier2_mcp_attempt_20/` 原始 case JSON（不覆盖 `final_scored.json`）。  
输出：`final_scored_v2.json` + 脱敏摘要 `artifacts/eval-summaries/phase0_rescore_attempt20/`。

| 指标 | V1 (`final_scored.json`) | V2 (`final_scored_v2.json`) |
|---|---:|---:|
| Top-1 Accuracy | 0.8000 | 0.8000 |
| Recall@5 | 0.8667 | 0.8667 |
| Ask Fact Correctness | 0.4667 | 0.4667 |
| Ask Citation Validity | 0.8667 | 0.8667 |
| E2E Pass Rate | 0.4000 | 0.4000 |
| Hallucination Rate | 0.0（误导） | **null**（`not_fully_measurable`） |
| Forbidden Assertion Rate | n/a | 0.0 |
| False Positive Rate | **0.0（漏判）** | **1.0（KB-032 检出）** |
| metric_contract_version | n/a | **2.0** |
| release_verdict | 不通过放行 | 不通过放行 |

分数下降（FP Rate 0→1）是正确结果；未回退评分逻辑。

---

## 6. KB-032 漏判修复证据

| 字段 | V1 | V2 |
|---|---|---|
| answer_mode | raw_only | raw_only |
| ask_has_answer | true | true |
| false_positive | **false** | **true** |
| reason_codes | n/a | `unexpected_answer_mode`, `substantive_answer_on_no_answer`, `sources_present_on_no_answer` |
| defect_category | null | `false_positive` |

回答为非拒答的 `raw_only` 确定性内容并带 sources，即使未包含字面 `具体办公地址`，也必须判误答。

---

## 7. Golden V2 数量

| 层级 | 文件/行数 | 说明 |
|---|---:|---|
| candidates | **37** | `golden_v1_migrated.jsonl`；全部 `annotation_source=candidate`，`split=development` |
| reviewed | **0** | 等待两名真实审核人 |
| frozen | **0** | 正式 harness 入口为空；formal 模式 fail closed |

迁移摘要：success=37，missing_passage=32（proposal），ambiguity=2（KB-009、KB-024 标 needs_clarification），pending_human_review=37。

dataset_hash：`2a84b269345e15e05557ff67ce2847102549eed266fd608f7afea9eaab54158c`

---

## 8. 尚需人工审核 / 裁决

1. 全部 37 条 candidates 的 dual review（primary ≠ secondary，真实 ISO8601 时间）。  
2. 为 answerable 题补齐 `passage_id` + evidence hash（当前多为 `v1_migration_passage_not_resolved`）。  
3. KB-009、KB-024 歧义裁决。  
4. 审核时写入与只读 `data/kb.db` 一致的 `corpus_snapshot.sha`。  
5. 冻结后才可跑 formal harness；在此之前仅允许 `--dev-candidates` / non_formal。

---

## 9. Reranker profile 状态

- 默认 profile：`deterministic-baseline`  
- `provider-enhanced` 不可用 → `track_status=blocked`，不得记为 normal  
- manifest 记录 `rerank_profile`、`rerank_status`、`scorer_contract_version=2.0`

Phase 0 **不**修复外部 provider。

---

## 10. 数据库 hash 复核

| 时刻 | SHA256 |
|---|---|
| 执行前 | `4ba22449794c984f6c1fda3d459574556c71b017efcc8e041bd4da731e737479` |
| 执行后 | `4ba22449794c984f6c1fda3d459574556c71b017efcc8e041bd4da731e737479` |

**一致。** 全程只读，未写库。

## 10.1 测试与静态检查结果

| 命令 | 结果 |
|---|---|
| `python tools/report_closure_debt.py --strict` | exit 0, strict clean |
| Phase 0–1 聚焦 pytest（架构/评分/Schema/freeze/split/sanitize/rerank/harness integrity） | **57 passed** |
| `pytest tests/mcp/test_hit_rate_regressions.py tests/mcp/test_hit_rate_v3_regressions.py` | **48 passed**（含 classify_defect 兼容） |
| `ruff check`（Phase 0–1 自有路径） | **All checks passed** |
| `mypy evals/hit_rate_v2` | **Success: no issues found in 6 source files** |
| `cd client && npm run build` | **success**（exit 0） |
| `pytest tests/ -q` | **2311 passed, 2 skipped, 10 failed** |

### 全量 10 个失败：判定为当前 HEAD 基线问题（非 Phase 0–1 引入）

失败集中在生产检索/回答契约与 mock 期望（`raw_only` vs `no_answer`、`top_k=5` vs `20`、contract snapshot 键序、GUI 服务状态），**不在** scorer/Golden V2/架构债修复路径内。Phase 0–1 明确禁止重构生产检索/回答算法，故**不在本阶段“修复”这些失败**，也不跳过/xfail/放宽。

失败列表：

1. `tests/stability/test_fts_no_answer_gate.py::test_search_fulltext_fallback_no_match`
2. `tests/test_mcp_gui_status.py::test_launcher_status_does_not_query_windows_service`
3. `tests/test_public_ask_contract.py::…::test_ask_raw_only`
4. `tests/test_public_ask_contract.py::…::test_ask_timeout_generate_failed`
5. `tests/test_public_search_contract.py::…::test_search_raw_snapshot`
6. `tests/test_public_search_contract.py::…::test_search_no_result`
7. `tests/test_query_revolution_phase3.py::test_end_to_end_structured_query_through_rag`（全量中失败；单跑时曾 pass，存在环境/顺序敏感性）
8. `tests/test_search_service.py::…::test_search_calls_rewrite_hybrid_rerank`
9. `tests/test_search_service.py::…::test_search_fallback_to_block_store`
10. `tests/test_wiki_serving_contract.py::…::test_wiki_001_raw_evidence_is_final_base`

**未新增失败于**架构债、版本一致性、hit-rate V2 评分、Golden 治理门禁。

---

## 11. 是否可进入 Phase 2

| 条件 | 状态 |
|---|---|
| 架构债 strict gate | ✅ |
| 版本一致性 | ✅ |
| 唯一 scorer + no-answer 修复 | ✅ |
| rerank requested/effective 可分 | ✅ |
| 原始 artifacts Git ignore / 脱敏 | ✅ |
| Golden V2 schema + candidates 迁移 | ✅ |
| review/freeze/split/formal 工具与测试 | ✅ |
| 人工 dual review + frozen 规模 | ❌ 阻塞 |
| 质量分数 ≥ 放行门槛 | ❌ 不在 Phase 0–1 目标 |

**放行到 Phase 2（检索/回答核心链路重构）的工程条件：满足。**  
**发布/正式 90% 质量门：不满足。**  
正式评测分母仍不可用（frozen=0）。

---

## 12. Git 操作声明

- **未** `git commit`  
- **未** `git push`  
- **未** 创建 PR  
- **未** 重写 Git 历史 / 批量删除既有 artifacts  
