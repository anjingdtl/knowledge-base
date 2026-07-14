# Handoff：Maintainability Phase-3 完成后

> **日期：** 2026-07-14  
> **版本：** v1.9.0  
> **来源 Spec：** `docs/superpowers/specs/03-maintainability-phase-3-application-infrastructure.md`

## 三工期核心接口（冻结消费侧）

```text
SearchExecution
RetrievalOrchestrator.search()  /  SearchService.execute()
AnswerService.execute() / AnswerExecution
src.mcp.server (protocol adapters)
AppContainer.groups.{core,verified,authoring,experimental}
```

## 后续可选工作（非阻塞）

1. 将 `src/mcp/server.py` 内工具函数按域迁入 `src/mcp/tools/*.py`
2. 生产开启 `retrieval.orchestrator=shadow` → 达标后 `unified`，再删 Legacy Search
3. Repository 按领域从 `db.py` 渐进抽取
4. 业务代码消除 `get_active_container()` / `Database._instance`（见弃用登记）

## 配置默认

| 键 | 默认 |
| --- | --- |
| `retrieval.orchestrator` | `legacy`（example） |
| `answer.orchestrator` | `unified`（example） |

## 文档入口

- [PROGRESS](../../../PROGRESS.md)
- [v1.9.0 release](../../release/v1.9.0-release-notes.md)
- [migration v1.8→v1.9](../../migration/v1.8-to-v1.9-maintainability.md)
- [deprecation register](../../migration/deprecation-register.md)
