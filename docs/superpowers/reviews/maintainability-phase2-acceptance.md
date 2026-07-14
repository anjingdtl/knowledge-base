# Maintainability Phase-2 验收报告

> **日期：** 2026-07-14  
> **Spec：** `docs/superpowers/specs/02-maintainability-phase-2-retrieval-wiki.md`  
> **Plan：** `docs/superpowers/plans/2026-07-14-maintainability-phase-2-retrieval-wiki.md`  
> **基线版本：** `1.8.1`  
> **发布版本：** `1.8.2`  
> **状态：** ✅ 二期目标完成（结构统一 + 适配器边界 + shadow/unified 开关；默认仍为 legacy）

---

## 1. 完成内容摘要

| 交付物 | 状态 | 说明 |
|---|---|---|
| `RawRetrievalResult` / `VerifiedServingResult` | ✅ | `src/retrieval/models.py` |
| `SearchExecution` re-export | ✅ | `src/retrieval/execution.py`（同 Phase-1 类型） |
| `VerifiedProvider` | ✅ | Gate 保护的 Claim 读取；异常不阻断 |
| `RawRetriever` | ✅ | 适配器委托 SearchService raw 管线 |
| `EvidenceOnlyPolicy` / `VerifiedPolicy` | ✅ | 共享入口语义，算法未改 |
| `RetrievalOrchestrator` | ✅ | legacy / shadow / unified |
| Shadow Comparator | ✅ | 仅 ID/计数/reason，无全文 |
| `SearchService` Facade | ✅ | `execute()` → Orchestrator |
| Legacy 主路径保留 | ✅ | `execute_primary_legacy` + `_search_*` |
| 默认配置 | ✅ | `retrieval.orchestrator: legacy`（example） |
| Wiki / Shadow 门禁测试 | ✅ | `tests/retrieval/` |
| AppContainer / MCP / DB | ✅ 未改架构 | 符合非目标 |

---

## 2. 验收清单（Spec §9）

| 条件 | 结果 | 证据 |
|---|---|---|
| Search 只有一个 Orchestrator 入口（对外 Facade） | ✅ | `SearchService.execute` → `RetrievalOrchestrator.search` |
| Raw 算法权威实现仍一处 | ✅ | 仍在 SearchService 私有方法；RawRetriever 适配 |
| Wiki Serving 有独立 Provider | ✅ | `VerifiedProvider` + 原 Gate |
| Evidence-only / Verified 经 Policy | ✅ | `policies/evidence_only.py` / `verified.py` |
| `SearchExecution` 契约不变化 | ✅ | re-export 同一类型；契约测试通过 |
| Search/Ask/Wiki 快照通过 | ✅ | public contract + wiki serving |
| Legacy 可回滚 | ✅ | 默认 legacy；配置切回即可 |
| Answer/MCP/Container/DB 无架构改动 | ✅ | 无 container/MCP/schema 改动 |
| 未立即删除 Legacy 主管线 | ✅ | Spec 2.8：保留一版 |

---

## 3. 测试命令与结果

```text
pytest tests/retrieval/ \
  tests/test_public_search_contract.py \
  tests/test_public_ask_contract.py \
  tests/test_wiki_serving_contract.py \
  tests/test_search_request_isolation.py \
  tests/test_search_service.py \
  tests/test_verified_hybrid_search.py \
  tests/test_verified_answer.py \
  tests/test_mcp_contract.py -q
```

**结果：** `134 passed, 1 skipped`（2026-07-14）

额外冒烟：legacy vs unified evidence-only mock 路径 Top source overlap = 1.0，cutover gates 通过。

---

## 4. 配置与回滚

```yaml
retrieval:
  orchestrator: legacy   # 默认正式
  # orchestrator: shadow   # 旧路径正式返回，新路径对比日志
  # orchestrator: unified  # 新路径正式返回
```

回滚：将 `orchestrator` 设回 `legacy`。无需数据库回滚，不影响 Canonical Wiki / MCP 工具契约。

---

## 5. 明确未做（符合 Spec 非目标 / 2.8 节奏）

- 未删除 `_search_legacy_pipeline` / `_search_verified_hybrid`
- 未默认将生产 `config.yaml` 切到 unified
- 未改 RRF / Rerank / Citation 算法
- 未统一 Ask / 未拆 MCP / 未改 Container
- Retrieval/Hybrid Eval 全量未在本机重跑（算法未改；建议合并前补跑）

---

## 6. 三期准入建议

| 条件 | 状态 |
|---|---|
| Unified 路径可运行 | ✅（配置切换） |
| Unified 作为默认正式稳定运行 | ⚠️ 尚未；默认仍 legacy |
| Shadow 门槛自动化 | ✅ 单元级；生产 shadow 需观测 |
| Wiki Serving 契约 | ✅ |
| 至少一版候选验证 | ✅ v1.8.2 候选 |

**建议：** 第三工期（Answer/应用层）可依赖 Orchestrator + Policy + Provider API 开始设计；**在默认 unified 观测稳定前**，不要删除 Legacy 主管线，也不要以 unified 为唯一生产路径。
