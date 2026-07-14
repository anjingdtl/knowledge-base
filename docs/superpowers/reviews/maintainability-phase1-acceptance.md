# Maintainability Phase-1 验收报告

> **日期：** 2026-07-14  
> **Spec：** `docs/superpowers/specs/01-maintainability-phase-1-contract-isolation.md`  
> **Plan：** `docs/superpowers/plans/2026-07-14-maintainability-phase-1-contract-isolation.md`  
> **基线版本：** `1.8.0`  
> **发布版本：** `1.8.1`  
> **状态：** ✅ 一期目标完成并 bump 至 v1.8.1（代码 + 契约 + 隔离；Eval 未在本机全量重跑，见备注）

---

## 1. 完成内容摘要

| 交付物 | 状态 | 说明 |
|---|---|---|
| `SearchExecution` | ✅ | `src/models/search_execution.py` |
| `SearchService.execute()` | ✅ | 请求局部 `_SearchRequestState`；`search()` 兼容壳 |
| 删除 `last_*` 共享状态 | ✅ | `src/` + `evals/` 无 `last_search_trace` / `last_disclose_claims` / `get_disclose_claim_rows` |
| VerifiedAnswer 迁移 | ✅ | 仅消费 `execute()`（测试 double 可仅实现 `search()`） |
| Search 契约快照 | ✅ | `tests/snapshots/search_*.json` × 4 |
| Ask 契约快照 | ✅ | `tests/snapshots/ask_*.json` × 5 |
| Wiki Serving 不变量 | ✅ | `tests/test_wiki_serving_contract.py` + `docs/architecture/wiki-invariants.md` |
| 并发隔离 | ✅ | 50 并发 × 20 轮，无串线 |

---

## 2. 验收清单（Spec §7）

| 条件 | 结果 | 证据 |
|---|---|---|
| Search 契约快照通过 | ✅ | `pytest tests/test_public_search_contract.py` |
| Ask 契约快照通过 | ✅ | `pytest tests/test_public_ask_contract.py` |
| Wiki Serving 契约通过 | ✅ | `pytest tests/test_wiki_serving_contract.py` |
| 50 并发无状态串线 | ✅ | `tests/test_search_request_isolation.py` |
| Search/Ask 用户可观察行为不变 | ✅ | 契约快照 + 既有 hybrid/answer 单测 |
| Retrieval Eval 不下降 | ⚠️ 未本机重跑 | 本期未改检索算法/排序；见备注 |
| Hybrid Eval 不下降 | ⚠️ 未本机重跑 | 同上 |
| 生产代码不读 `last_search_trace` | ✅ | `rg` 于 `src/` 无匹配 |
| 生产代码不读 `last_disclose_claims` | ✅ | 同上 |
| MCP Tool Contract 不变化 | ✅ | `tests/test_mcp_contract.py` 通过 |
| DB Schema 无变化 | ✅ | 无 alembic / schema 改动 |

---

## 3. 测试命令与结果

```text
pytest tests/test_search_service.py \
  tests/test_verified_hybrid_search.py \
  tests/test_verified_answer.py \
  tests/test_mcp_contract.py \
  tests/test_public_search_contract.py \
  tests/test_public_ask_contract.py \
  tests/test_wiki_serving_contract.py \
  tests/test_search_request_isolation.py \
  tests/test_wiki_serving_gate.py -q
```

**结果：** `124 passed, 1 skipped`（2026-07-14）

隔离专项：

```text
pytest tests/test_search_request_isolation.py -v
# 3 passed — 50 workers × 20 rounds
```

`last_*` 门禁：

```text
rg "last_search_trace|last_disclose_claims|get_disclose_claim_rows" src evals
# 无匹配
```

---

## 4. 架构变化（用户可观察行为）

```text
旧：SearchService.search() → 写 self.last_* → VerifiedAnswer 再读 last_*
新：SearchService.execute() → SearchExecution(results, trace, disclose, conflicts, fallbacks, warnings)
    SearchService.search() = list(execute().results)   # 兼容
    VerifiedAnswer.ask() → execute() 一次消费
```

- MCP `search` / `ask` 公开 envelope **未改字段契约**
- 检索 / RRF / Rerank / Citation / Wiki Gate **未改算法**
- 无数据库迁移

---

## 5. 回滚策略（保留）

1. `search()` 兼容入口保留  
2. 若 `execute` 组装异常：优先修 state 组装；契约测试不回滚  
3. 无数据恢复需求  

---

## 6. 二期准入

| 准入条件 | 状态 |
|---|---|
| 全量相关测试通过 | ✅（相关子集 124 passed） |
| 并发隔离通过 | ✅ |
| Search/Ask/Wiki 契约稳定 | ✅ |
| Retrieval/Hybrid Eval | ⚠️ 建议合并前补跑 |
| v1.8.1 发布/候选 | ✅ `src/version.py` = `1.8.1` |

**建议：** 在进入 Phase-2 前补跑：

```bash
python evals/run_retrieval_eval.py
python evals/run_hybrid_eval.py --strict
```

---

## 7. 主要变更文件

- `src/models/search_execution.py`（新）
- `src/services/search_service.py`
- `src/services/verified_answer.py`
- `evals/run_ask_e2e_eval.py`
- `tests/test_public_search_contract.py` / `test_public_ask_contract.py`
- `tests/test_wiki_serving_contract.py` / `test_search_request_isolation.py`
- `tests/helpers/contract_normalize.py`
- `tests/snapshots/{search_*,ask_*}.json`
- `docs/architecture/wiki-invariants.md`
- `docs/superpowers/plans/2026-07-14-maintainability-phase-1-contract-isolation.md`
