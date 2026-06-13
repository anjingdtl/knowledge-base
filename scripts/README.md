# 脚本索引

## 日常支持脚本

| 脚本 | 用途 |
| --- | --- |
| `build_docker.py` | 构建 Docker 镜像 |
| `build_docs.py` | 生成版本化 DOCX 用户手册 |
| `build_windows.py` | PyInstaller + Inno Setup Windows 构建 |
| `check_mcp.py` | MCP HTTP 连接与工具发现诊断 |
| `mcp_service.py` | Windows 下管理常驻 MCP HTTP 进程 |
| `setup_mcp.py` | 写入常见 Agent 的 MCP 配置 |
| `stress_test_mcp.py` | MCP 并发压力测试 |
| `stress_test_ask_fts.py` | ask/FTS 混合压力测试 |

## 兼容迁移脚本

以下脚本只用于旧数据库或旧图结构升级。运行前必须备份 `data/`：

| 脚本 | 用途 |
| --- | --- |
| `migrate_to_block_graph.py` | 旧知识数据迁移到 Block Graph；仍被 `tests/test_migration.py` 引用 |
| `migrate_to_block_store.py` | 旧 chunk 向量迁移到 Block Store |
| `migrate_phase123.py` | 历史 Phase 1-3 数据重建 |
| `export_to_file_graph.py` | 数据库条目导出到 Markdown File Graph |
| `fast_migrate.py` / `fast_migrate_edges.py` | 历史 SQLite -> Neo4j 快速迁移 |

新 schema 变更应使用 Alembic，不应再新增同类一次性迁移脚本。

## 数据恢复脚本

`check_db.py`、`verify_db.py`、`migration_snapshot.py`、`fix_schema.py`、`repair_db.py`、`repair_db2.py`、`rescue_data.py`、`rebuild_from_md.py` 来自一次数据库损坏救援。

这些脚本：

- 不是正常升级流程的一部分。
- 部分会直接修改 SQLite schema 或替换数据库文件。
- 只应针对副本运行。
- 执行前必须停止 GUI、API 和 MCP 服务，并完整备份 `data/`。

它们暂时保留是为了旧用户数据救援，不代表受支持的日常接口。
