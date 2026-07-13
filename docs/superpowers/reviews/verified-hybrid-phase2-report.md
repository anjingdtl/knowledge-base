# Phase 2 阶段报告：Wiki Serving Gate

> 日期：2026-07-13  
> Spec：`docs/ShineHeKnowledge 融合收束开发规格说明.md` §6 / §12.3–12.4 / Phase 2  
> 前置：Phase 0 `abbfa35` · Phase 1 `737d0a9`  
> 本阶段提交：`ae62767`

---

## 1. 修改文件列表

| 文件 | 变更 |
|---|---|
| `src/services/wiki_serving_gate.py` | **新增** Gate、Reason Code、Evidence Resolution、诊断 |
| `src/services/wiki_repository.py` | 只读 Serving API 四件套 |
| `src/services/search_service.py` | `list_servable_wiki_claims` 唯一 Claim 入口 |
| `src/core/container.py` | 注入 `wiki_serving_gate` → SearchService |
| `src/services/doctor.py` | `check_serving_claims` 统计与 Stale Rate |
| `tests/test_wiki_serving_gate.py` | **新增** 单元/契约测试 |
| `docs/superpowers/reviews/verified-hybrid-phase2-report.md` | 本报告 |
| `PROGRESS.md` / `docs/README.md` | 进度索引 |

## 2. 行为变化摘要

- 任何 Claim 成为**可靠主结论**前必须通过 `WikiServingGate.evaluate`
- 条件：`active` + 非 stale supports + Block/Knowledge 可解析 + hash 一致 + validate 通过 + 非 review
- 不合格原因码（Trace/Eval）：`claim_stale` / `claim_unsupported` / `missing_evidence` / `evidence_block_missing` / `evidence_hash_mismatch` / `review_required` 等
- `DISPUTED` → `disclose_only`（不作为唯一主结论）
- Repository：`list_servable_claims` / `get_servable_claim` / `resolve_claim_evidence` / `get_claim_serving_diagnostics`
- **不读 staging、无写副作用、无 LLM**
- SearchService 仅通过 `list_servable_wiki_claims` 暴露可服务 Claim（Phase 3 再做融合）

## 3. 兼容性

| 场景 | 结果 |
|---|---|
| Authoring `get_claim` / `list_claims` | 不变，仍可见 draft 等 |
| Raw Search / Ask 路径 | **未改**融合逻辑（Phase 3） |
| 已有 `wiki_first` / authoring | 不受影响 |
| evidence_only / read_disabled | Gate 全部拒绝 |

## 4. 测试

```text
pytest tests/test_wiki_serving_gate.py tests/test_search_service.py tests/test_doctor.py -q
→ 50 passed
ruff check (改动文件) → All checks passed
```

验收对照：

- 不合格 Claim 无法经 `list_servable_*` / `SearchService.list_servable_wiki_claims` 进入
- Stale / Unsupported Serving Rate = 0（diagnostics + 单测）
- Gate 确定性，无 LLM

## 5. 指标

Phase 2 不改 Raw Retrieval 算法；Retrieval / Wiki Eval 指标应与 Phase 0 持平（未强制重跑全量）。

## 6. 风险

- 无 `excerpt_hash` 的旧 Claim：跳过 hash 比对，仅校验 Block 存在（迁移数据）
- 未注入 `get_block` 且 `require_block_evidence=true` 时 fail-closed
- Search 主路径尚未自动混入 Wiki Claim（**有意留给 Phase 3**）

## 7. 回滚

```bash
git revert <phase2-sha>
```

无 schema 迁移。

## 8. 明确未做

- Phase 3：Query Router / Fusion / Raw fallback 编排
- Phase 4：回答引用与冲突披露产品化
- Phase 5：维护中心自动化
