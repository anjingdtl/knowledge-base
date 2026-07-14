# Maintainability Phase-3 验收报告

> **日期：** 2026-07-14  
> **Spec：** `docs/superpowers/specs/03-maintainability-phase-3-application-infrastructure.md`  
> **Plan：** `docs/superpowers/plans/2026-07-14-maintainability-phase-3-application-infrastructure.md`  
> **基线：** v1.8.2  
> **发布版本：** v1.9.0  
> **状态：** ✅ 三期目标完成（3A/3B/3C/3D 骨架与门禁落地；MCP 工具实现体迁至 `src.mcp.server`，分域模块先登记边界）

---

## 1. 子阶段完成摘要

### 3A Answer

| 交付物 | 状态 |
|---|---|
| `AnswerExecution` | ✅ `src/answering/models.py` |
| `AnswerService` | ✅ 经 `SearchService.execute` 取证 |
| ContextBuilder / Generator | ✅ 适配层 |
| Shadow 对比 | ✅ `src/answering/shadow.py` |
| `answer.orchestrator` | ✅ legacy / shadow / unified（默认 unified） |
| `VerifiedAnswerService` 兼容壳 | ✅ 委托 AnswerService |
| Ask 契约 | ✅ |

### 3B MCP

| 交付物 | 状态 |
|---|---|
| `runtime` / `auth` / `envelopes` / `policies` | ✅ |
| `src/mcp/server.py` 实现体 | ✅（自原 mcp_server 迁入） |
| `src/mcp_server.py` 兼容入口 | ✅ 模块别名 + `main` |
| `tools/*` 分域登记 | ✅ 域边界与工具名清单（实现仍在 server 过渡） |
| MCP Contract | ✅ |

### 3C Container

| 交付物 | 状态 |
|---|---|
| Core/Verified/Authoring/Experimental 视图 | ✅ `service_groups.py` + `container.groups` |
| 旧属性代理保留 | ✅ 扁平 lazy 属性未删 |
| 架构边界测试 | ✅ `tests/architecture/` |

### 3D DB / Legacy

| 交付物 | 状态 |
|---|---|
| `_migrate()` 冻结策略与测试 | ✅ |
| Alembic versions 非空 + upgrade 冒烟 | ✅（环境不可用时 skip） |
| 弃用登记 | ✅ `docs/migration/deprecation-register.md` |
| 未一次性拆空 db.py | ✅ |

---

## 2. 测试证据

```text
pytest tests/answering/ tests/architecture/ tests/retrieval/ \
  tests/test_public_ask_contract.py tests/test_public_search_contract.py \
  tests/test_wiki_serving_contract.py tests/test_search_request_isolation.py \
  tests/test_search_service.py tests/test_verified_hybrid_search.py \
  tests/test_verified_answer.py tests/test_mcp_contract.py \
  tests/test_database_migration_policy.py tests/test_alembic_baseline.py -q
```

**结果：** `152 passed, 1 skipped`（2026-07-14）

---

## 3. 回滚

| 子阶段 | 回滚 |
|---|---|
| 3A | `answer.orchestrator: legacy`（同 assemble 路径） |
| 3B | 兼容入口保留；可恢复直接编辑 `src/mcp/server.py` |
| 3C | 继续使用扁平 `container.search_service` 等属性 |
| 3D | `_migrate` 未删除；Alembic 不删旧 schema |

---

## 4. 后续建议（不阻塞 v1.9.0）

1. 将 `src/mcp/server.py` 内工具函数按域迁入 `tools/retrieval.py` 等（独立 PR）  
2. 生产观察 `answer.orchestrator=shadow` 结构一致率  
3. Retrieval 默认切 unified 后删除 Legacy Search 主管线  
4. Repository 按领域继续从 `db.py` 抽离  

---

## 5. 非目标确认

- 未改 Retrieval 排序 / Wiki Gate / Claim 语义  
- 未新增 MCP 工具、未换 FastMCP/SQLite  
- 未一次性重写 `db.py`  
- 未开启 Auto Publish  
