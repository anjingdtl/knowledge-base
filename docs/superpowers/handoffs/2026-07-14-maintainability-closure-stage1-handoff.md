# Task 完成报告 — Maintainability Closure 首批（WP0 + WP1-T1）

## Task
- ID：WP0-T1 / WP0-T2 / WP1-T1
- 基线 SHA：`b635ca70132f1ee0304492a7c8cc16a58a831856`
- 完成分支：`feat/maintainability-closure-wp0-wp1`
- Spec：`docs/superpowers/specs/04-maintainability-closure-spec.md`
- Plan：`docs/superpowers/plans/2026-07-14-maintainability-closure.md`

## 修改范围
### 生产文件
- `src/retrieval/raw_retriever.py` — Raw 算法权威实现（显式依赖构造）
- `src/services/search_service.py` — `_get_raw_retriever`；`run_raw_retrieval_adapter` / `_search_legacy_pipeline` 委托
- `PROGRESS.md` — Verified Hybrid 完成标记措辞（基线测试）

### 测试文件
- `tests/architecture/test_closure_debt_baseline.py` — 欠账快照
- `tests/retrieval/test_raw_retriever.py` — 独立构造测试
- `tests/retrieval/test_raw_retriever_parity.py` — 委托等价
- `tests/test_mcp_first_completion_gaps.py` — lifespan patch 目标修正
- `tests/test_verified_hybrid_release_evidence.py` — 间接受益于 PROGRESS 措辞

### 工具 / 文档
- `tools/report_closure_debt.py`
- `docs/superpowers/plans/2026-07-14-maintainability-closure.md`
- `docs/superpowers/reviews/maintainability-closure-baseline.md`
- `docs/superpowers/specs/04-maintainability-closure-spec.md`（入库）

## 明确未修改
- Retrieval 算法评分 / RRF / Gate：**未改**
- Wiki Gate / Claim 语义：**未改**
- MCP Contract / 工具注册：**未改**
- DB Schema / Alembic：**未改**
- Unified 默认 / Legacy 删除：**未做**（按 Spec §17 停止）
- Fusion / Packaging 抽取（WP1-T2）：**未做**

## 验证
- Targeted pytest（retrieval + architecture debt + search/ask/wiki contracts）：**66 passed**
- 预存失败修复：lifespan + release evidence **4 passed**
- Full pytest（改造前）：1772 passed / 2 failed→已修 / 2 skipped
- Ruff：基线有预存问题；本轮工具无新增 sys 未用
- MyPy：3 个预存 error（未在本批范围）
- Retrieval Eval：PASS
- Hybrid Eval：PASS strict 175 cases
- Migration Gate：N/A（WP4）
- GitHub Actions：未推送

## 行为对比
- Search：Evidence-only 仍经 Legacy 主路径；内部算法委托 `RawRetriever.retrieve`
- Ask：契约测试通过；未改 Answer 路径
- Wiki：Serving 契约通过
- MCP：仅 lifespan 测试补丁；工具实现未搬迁

## 风险与回滚
- 已知风险：SearchService 仍保留 helper 副本供 verified hybrid 与测试 patch 使用；双份 helper 在 WP1-T2 再收敛
- 回滚：`git revert` 本批提交；配置无需变更（仍默认 legacy）

## 架构欠账变化
- 修复前：`raw_retriever_calls_search_service=True`（适配器）
- 修复后：`raw_retriever_calls_search_service=False`；strict residual 10→9

## 是否允许进入下一 Task
- **YES** — 进入 **WP1-T2**（VerifiedFusion + Packaging 抽取）
- 原因：Raw 编排已独立；契约与 Hybrid/Retrieval Eval 未退化；Spec §17 首批范围完成
- **仍禁止：** Unified 默认切换、Legacy 删除（需 Shadow 聚合门槛）
