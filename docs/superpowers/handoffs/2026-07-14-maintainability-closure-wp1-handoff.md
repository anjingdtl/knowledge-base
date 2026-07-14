# Task 完成报告 — Maintainability Closure WP1-T2…T6

## Task
- ID：WP1-T2 / T3 / T4 / T5 / T6
- 基线 SHA：`27a28fc`（WP1-T1 后）
- 分支：`feat/maintainability-closure-wp0-wp1`
- Spec：`docs/superpowers/specs/04-maintainability-closure-spec.md`

## 修改范围
### 生产
- `src/retrieval/fusion.py` — VerifiedFusion
- `src/retrieval/packaging.py` — SearchRequestState / to_execution / evidence-only 组装
- `src/retrieval/policies/evidence_only.py` — 直接 RawRetriever
- `src/retrieval/policies/verified.py` — 直接 VerifiedFusion
- `src/retrieval/orchestrator.py` — 默认 **unified**；unified 路径组合 Policy
- `src/compatibility/legacy_retrieval.py` — Legacy 主路径
- `src/services/search_service.py` — Facade + 委托 fusion/raw
- `config.example.yaml` — `retrieval.orchestrator: unified`

### 工具 / 测试 / 文档
- `tools/run_retrieval_shadow_eval.py`
- `evals/reports/retrieval-shadow-2026-07-14.json`
- `tests/retrieval/test_fusion_packaging.py` + orchestrator/shadow 默认断言更新
- `docs/migration/v1.9-to-v1.9.1-unified-retrieval.md`
- `PROGRESS.md` / `Claude.md` / deprecation-register

## 明确未修改
- RRF / Gate / Claim 评分公式（仍在 verified_hybrid_fusion）
- MCP 工具实现 / Answer assemble 归位
- DB Schema / Alembic
- **未删除** Legacy（仅降为回滚）

## 验证
- pytest retrieval + contracts + architecture：**77 passed**
- Shadow aggregate：**6/6 pass**，min overlap=1.0，exceptions=[]
- Hybrid Eval strict：**PASS** 175 cases
- debt：`raw_retriever_calls_search_service=False`；strict residual 仍 9（MCP/Alembic/Answer 属 WP2+）

## 是否允许进入下一 Task
- **YES** — 进入 **WP2**（Answer 归位 + MCP 工具实拆）
- 原因：Unified 默认 + Shadow 门槛达标；Legacy 保留回滚
- 仍禁止：删除 Legacy 主管线（须正式版本观察后 WP5）
