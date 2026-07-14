# Legacy 弃用登记（Deprecation Register）

> **建立版本：** v1.9.0  
> **维护：** 删除任何 Legacy 前必须满足生产调用数=0、仅 compatibility 模块引用、至少经过一个正式版本、迁移文档已发布、回滚路径已验证。

| 入口 | 替代方案 | 弃用版本 | 最早删除版本 | 备注 |
| --- | --- | --- | --- | --- |
| `Database._instance` | Repository / 构造注入 | v1.9.0 | v2.0 | 兼容模块可暂留 |
| `get_active_container()` | 构造注入 `AppContainer` | v1.9.0 | v2.0 | MCP runtime 白名单 |
| `_get_container()`（业务代码） | 显式注入 | v1.9.0 | v2.0 | 仅限 `src/mcp/runtime.py` / 兼容层 |
| Legacy Retrieval 主管线 | `RetrievalOrchestrator` + Policy | v1.8.x | v2.0 | 默认仍可 `retrieval.orchestrator=legacy` |
| Legacy Answer 路径 | `AnswerService` / `AnswerExecution` | v1.9.0 | v2.0 | `answer.orchestrator` 可 shadow |
| Legacy MCP aliases | 标准工具名 | 已弃用 | v2.0 | `mcp.enable_legacy_aliases` |
| `src/mcp_server.py` 业务实现 | `src/mcp/server.py` + tools 分域 | v1.9.0 | v2.0 | 现为兼容 re-export |
| 直接调用 `SearchService` 私有 raw 方法 | `RetrievalOrchestrator.search` | v1.8.2 | v2.0 | 三期 Answer/MCP 禁止 |

## 删除门禁清单

```text
生产调用数 = 0
仅 compatibility 模块仍引用
至少经过一个正式版本
迁移文档已发布
回滚路径已验证
```
