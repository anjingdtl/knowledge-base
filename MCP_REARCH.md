# MCP Container 重构 — 迭代 2 完成 & 交接手册

## 概述

将知识库系统的 MCP Server 和 API Server 全面改造为依赖注入（AppContainer）架构，统一搜索管线。

## 分支信息

- 分支：`update0602`
- 基于 master `996a0ec`，追加 1 个 commit: `8ff64b7`
- GitHub：`https://github.com/anjingdtl/knowledge-base/tree/update0602`

## 当前状态

### 已改造完成

| 模块 | 状态 | 说明 |
|------|------|------|
| `src/core/container.py` | ✅ | AppContainer + create_container()，含 db/block_store/vectorstore/embedding/llm/search_service/rag_pipeline |
| `src/api/__init__.py` | ✅ | lifespan 调用 create_container() |
| `src/api/deps.py` | ✅ | `get_container()` FastAPI DI |
| `src/api/routes.py` | ✅ | 全部 20 个 endpoint 注入 Container，搜索用 SearchService 完整管线 |
| `src/services/search_service.py` | ✅ (新建) | 统一搜索：改写→混合检索→重排→Wiki优先 |
| `src/mcp_server.py` | ✅ | 所有 Database.xxx() → c.db.xxx()，VectorStore() → c.block_store，搜索用 search_service |
| `tests/test_mcp_server.py` | ✅ | Mock 适配：VectorStore/BlockStore 改在源头模块 mock |
| `tests/test_api.py` | ✅ | 回归修复 test_blocks_endpoint_returns_page_blocks |

### 仍未 Container 化的遗留代码

这些文件内部仍直接使用单例，与 Container 架构共存。应逐步迁移：

| 文件 | 遗留问题 |
|------|----------|
| `src/services/indexer.py:15` | `index_knowledge_item()` 内部直接 `Database.insert_blocks()` / `BlockStore()` / `VectorStore()` 单例 |
| `src/services/indexer.py:161` | `IndexerService` 类已接受 container-style 构造（db, vectorstore, embedding, config），但未在 Container 中注册 |
| `src/mcp_server.py:154` | `_do_ask()` 直接 `RAGService()` — 未走 Container |
| `src/mcp_server.py:275` | `reindex_all()` 直接调模块级 `indexer.reindex_all()` |

### 关键决策记录

1. **`_get_container()` 策略**：MCP 使用 lazy init 包装（lifespan 未触发时延迟创建，主要用于测试），而非 API 的 `Depends` 模式
2. **`create_container()` 保留连接**：`Database._conn` 判空保护，测试 fixture 的临时数据库连接不会被覆盖
3. **Mock 位置迁移**：`test_mcp_server.py` 的 mock 从 `mcp_mod.VectorStore` 改为 `src.services.vectorstore.VectorStore` 和 `src.services.block_store.BlockStore`，因为 mcp_server 不再直接引用 VectorStore

## 推荐迭代 3 计划

1. **IndexerService 容器化**：将 `IndexerService` 注册到 Container，`index_knowledge_item` 改为通过 Container 调用，消除对单例的直接依赖
2. **`_do_ask` 接入 Container**：通过 `Container.rag_pipeline` 替代直接 `RAGService()`
3. **`reindex_all` 容器化**：同上
4. **清理 import**：移除 `indexer.py` 顶部 `from src.services.block_store import BlockStore` 等（改为通过参数或 Container 注入）

## 测试验证

```bash
# 全量测试（当前 133 通过）
python -m pytest tests/ -v

# 按模块
python -m pytest tests/test_mcp_server.py -v   # 19 tests
python -m pytest tests/test_api.py -v           # 20 tests
python -m pytest tests/test_search_service.py -v # 4 tests
```

## 新环境快速启动

```bash
git clone https://github.com/anjingdtl/knowledge-base.git
cd knowledge-base
git checkout update0602
pip install -e ".[all]"
# 确保 config.yaml 中有 Embedding API 配置

# 跑全量测试
python -m pytest tests/ -v
```
