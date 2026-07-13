# Verified Hybrid 收束纠偏基线

> 日期：2026-07-13
> 基线提交：`21737ff36c06b8f838ff83dac0e19e5948b9c694`
> 分支：`master`，与 `origin/master` 同步
> 状态：纠偏进行中；以下结果是事实基线，不是发布通过结论。

## 工作树保护

本轮开始时存在用户未跟踪的真实评测产物、`raw/` 与本地 schema 覆盖文件；它们保持只读，不会被加入提交、移动或删除。

## 当次执行结果

| 门禁 | 命令 | 实际结果 | 判定 |
|---|---|---|---|
| Python 全量回归 | `python -m pytest tests -q --basetemp .codex-pytest-tmp/baseline` | **1646 passed, 2 skipped, 7 warnings**，348.60s | 通过（警告待清理） |
| Ruff | `python -m ruff check src tests evals tools scripts` | **8 errors**，均为未使用 import 或 import 排序 | **未通过** |
| mypy | `python -m mypy src tools --ignore-missing-imports` | **14 errors**，涉及 `wiki_repository`、`verified_hybrid_fusion`、`file_graph`、`search_service`、`verified_answer` | **未通过** |
| 前端构建 | `npm run build`（`client/`） | 通过，Vite 8.0.16 | 通过 |
| Hybrid 契约评测 | `python evals/run_hybrid_eval.py --strict --json` | 175 例、overall PASS；明确是无 embedding/LLM 的 deterministic 评测 | 通过（不能替代真实 A/B） |
| Retrieval 契约评测 | `python evals/run_retrieval_eval.py --all --fake-embedding --baseline evals/baselines/local.json --max-regression 0.05 --report json` | 通过；存在与既有基线相同的三项 warning | 通过（带 warning） |
| Knowledge Evolution | `python evals/run_knowledge_evolution_eval.py --json` | overall PASS；projection parity 为 skipped as pass | 通过（不能替代真实 projection 验证） |
| 当前最终评审 | `verified-hybrid-final-review.md` | 仅有确定性 175 例 Hybrid Eval，缺 Ruff=0、mypy=0、Python Matrix、Docker、Windows 与真实 Hybrid A/B | **未通过** |

## 已确认的收束缺口

1. `wiki_first` 兼容解析为 authoring，但缺失 `rag.verified_knowledge.enabled` 时实际 Ask 不进入 Verified Hybrid；
2. `config.example.yaml` 的 Canonical/Claim/Rebuild/Projection/Validation/Site 层级错置，且 `mode: off` 会被 YAML 解析为布尔值；
3. Serving Gate 尚未将 Validation、Review、Published Revision 与可复核 Evidence 作为 fail-closed 前置条件；
4. Maintenance Job/Review 是进程内状态，尚未具备重启恢复、唯一幂等、lease/retry、唯一事件入口与周期调度；
5. 当前 Hybrid 175 题是确定性契约评测，不能证明真实 Raw/Hybrid A/B 有效提升。

## 纠偏结论

当前版本不满足“Verified Hybrid 融合收束完成”的发布条件。后续每个 Phase 仅记录当次实际命令结果；只有 v1.8.0 PLAN 定义的所有发布门禁通过后，才能创建新的最终验收结论。

## 回滚

本 Phase 仅增加纠偏文档和文档一致性测试；回滚可仅撤销对应文档/测试变更，不涉及数据库或用户数据。
