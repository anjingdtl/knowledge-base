# Spec: Container DI 集成 + MCP/API 搜索对齐（迭代 2）

**日期**: 2026-06-02  
**项目**: ShineHeKnowledge v1.2.0  
**前置**: 迭代 1（Block-First 向量存储重写）已完成  
**范围**: Container DI 全面改造 + MCP/API 搜索能力对齐

---

## 1. 目标

1. 将 `AppContainer` 集成到 MCP 和 API 入口层，替代全局单例模式
2. 提取 `SearchService` 类封装完整搜索管线，MCP/API 共用
3. API `GET /knowledge/search` 升级到与 MCP `search` 完全一致的能力
4. MCP 回退路径从 `VectorStore` 改为 `BlockStore`

## 2. SearchService 类

### 新建文件: `src/services/search_service.py`

```python
class SearchService:
    """统一搜索服务 — MCP 和 API 共用
    
    封装完整搜索管线：查询改写 → 混合检索 → 重排序 → Wiki 优先
    """
    
    def __init__(self, config, db, block_store, embedding, llm):
        self._config = config
        self._db = db
        self._block_store = block_store
        self._embedding = embedding
        self._llm = llm
    
    def search(self, query: str, top_k: int = 5) -> list[dict]:
        """完整搜索管线"""
        output = []
        
        # 1. 查询改写
        queries = self._rewrite_query(query)
        
        # 2. 混合检索（HybridSearcher: 向量 + 关键词 blend + RRF 融合）
        candidates = self._hybrid_search(queries, top_k)
        
        # 3. 重排序（专用 reranker 模型或 LLM 打分）
        if candidates:
            candidates = self._rerank(query, candidates, top_k)
        
        # 4. Wiki 结构化知识优先
        wiki_results = self._wiki_search(query)
        output.extend(wiki_results)
        
        # 5. 组装检索+重排结果
        seen_kids = {w.get("knowledge_id") for w in wiki_results}
        for r in candidates:
            kid = (r.get("metadata") or {}).get("page_id", 
                  (r.get("metadata") or {}).get("knowledge_id", ""))
            if kid and kid not in seen_kids:
                seen_kids.add(kid)
                item = self._db.get_knowledge(kid) if kid else None
                score = r.get("rerank_score", r.get("rrf_score", 
                        r.get("score", r.get("distance", 0))))
                output.append({
                    "source": "knowledge",
                    "block_id": r.get("id", ""),
                    "knowledge_id": kid,
                    "title": item["title"] if item else "未知",
                    "text": r.get("text", ""),
                    "score": score,
                })
        
        return output
```

### 内部方法

- `_rewrite_query(query)` — 调用 `QueryRewriter`，失败回退 `[query]`
- `_hybrid_search(queries, top_k)` — 调用 `HybridSearcher`，失败回退 `BlockStore().search()`
- `_rerank(query, candidates, top_k)` — 调用 `LLMReranker`，失败保留原序
- `_wiki_search(query)` — 调用 `Database.search_wiki_fts()`，返回 Wiki 结果

## 3. Container 全面改造

### 3.1 Container 新增属性

```python
# src/core/container.py
@dataclass
class AppContainer:
    # ... 现有属性 ...
    _search_service: Optional[object] = field(default=None, repr=False)
    
    @property
    def search_service(self):
        if self._search_service is None:
            from src.services.search_service import SearchService
            self._search_service = SearchService(
                self.config, self.db, self.block_store, self.embedding, self.llm
            )
        return self._search_service
```

### 3.2 API 入口改造

```python
# src/api/__init__.py
from src.core.container import create_container, shutdown_container

@asynccontextmanager
async def lifespan(app: FastAPI):
    container = create_container()
    app.state.container = container
    yield
    shutdown_container(container)
```

### 3.3 API 依赖注入

```python
# src/api/deps.py — 新建
from fastapi import Request
from src.core.container import AppContainer

def get_container(request: Request) -> AppContainer:
    return request.app.state.container
```

### 3.4 API 路由改造

```python
# src/api/routes.py — 示例改造
from src.api.deps import get_container
from src.core.container import AppContainer

@kb_router.get("/search")
def search_knowledge(q: str, top_k: int = 10, 
                     container: AppContainer = Depends(get_container)):
    results = container.search_service.search(q, top_k=top_k)
    return {"results": results, "total": len(results)}

@kb_router.post("")
def create_knowledge(req: KnowledgeCreate,
                     container: AppContainer = Depends(get_container)):
    item = KnowledgeItem(...)
    container.db.insert_knowledge(item.to_row())
    index_knowledge_item(item)
    return item

@kb_router.delete("/{item_id}")
def delete_knowledge(item_id: str,
                     container: AppContainer = Depends(get_container)):
    container.block_store.delete_by_page(item_id)
    container.db.delete_knowledge(item_id)
    return {"deleted": True}
```

### 3.5 MCP 入口改造

```python
# src/mcp_server.py
from src.core.container import create_container, shutdown_container

_container: AppContainer | None = None

@asynccontextmanager
async def server_lifespan(server: FastMCP):
    global _container
    _container = create_container()
    beat()
    _heartbeat_task = asyncio.create_task(_heartbeat_loop())
    yield {}
    _heartbeat_task.cancel()
    shutdown_container(_container)

@mcp.tool(...)
def search(query: str, top_k: int = 5) -> list[dict]:
    return _container.search_service.search(query, top_k=top_k)

@mcp.tool(...)
def create(title: str, content: str, ...):
    item = KnowledgeItem(...)
    _container.db.insert_knowledge(item.to_row())
    index_knowledge_item(item)
    return item

@mcp.tool(...)
def delete(id: str):
    _container.block_store.delete_by_page(id)
    _container.db.delete_knowledge(id)
```

### 3.6 MCP 回退路径修复

```python
# 改造前
candidates = VectorStore().search(query, top_k=top_k)

# 改造后
candidates = _container.block_store.search(query, top_k=top_k)
```

## 4. API 搜索对齐

### 端点变更

| 属性 | 改造前 | 改造后 |
|------|--------|--------|
| 路径 | `GET /api/knowledge/search` | 不变 |
| 参数 | `q, limit=20, offset=0` | `q, top_k=10` |
| 能力 | 仅 FTS5 关键词搜索 | 完整管线（改写→混合→重排→Wiki） |
| 返回 | `{results, items, total}` | `{results, total}` |

**Breaking Change**: `limit`/`offset` 参数移除，改为 `top_k`。完整管线不支持传统分页。

## 5. 测试策略

### 新增测试

**`tests/test_search_service.py`**:
- `test_search_calls_rewrite_hybrid_rerank` — 验证管线各阶段被调用
- `test_search_wiki_priority` — Wiki 结果排在前面
- `test_search_fallback_to_block_store` — HybridSearcher 失败时回退 BlockStore
- `test_search_returns_correct_structure` — 返回结构包含 source, score, knowledge_id

**`tests/test_container.py`**:
- `test_create_container_returns_complete_container` — Container 包含所有服务
- `test_container_search_service_accessible` — search_service 属性可访问
- `test_container_block_store_accessible` — block_store 属性可访问

### 改造测试

**`tests/test_api.py`**:
- 搜索端点测试验证完整管线结果
- 所有端点测试适配 Container 注入

**`tests/test_mcp_server.py`**:
- 搜索工具测试验证 BlockStore 回退
- 所有工具测试适配 Container 注入

## 6. 影响范围

### 改动文件

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `src/services/search_service.py` | 新建 | SearchService 类 |
| `src/api/deps.py` | 新建 | FastAPI Depends 注入 |
| `src/core/container.py` | 修改 | 新增 search_service 属性 |
| `src/api/__init__.py` | 修改 | lifespan 创建 Container |
| `src/api/routes.py` | 修改 | 全面 Container 注入 + 搜索对齐 |
| `src/mcp_server.py` | 修改 | 全面 Container 注入 + 回退修复 |
| `tests/test_search_service.py` | 新建 | SearchService 测试 |
| `tests/test_container.py` | 新建 | Container 集成测试 |
| `tests/test_api.py` | 修改 | 适配 Container + 搜索验证 |
| `tests/test_mcp_server.py` | 修改 | 适配 Container + 回退验证 |

### 兼容性

- API 搜索端点参数变更 — **Breaking Change**（`limit/offset` → `top_k`）
- MCP 工具接口不变 — 向后兼容
- GUI 不受影响（使用全局单例，留到迭代 3）

## 7. 不在本次范围

- GUI 层 Container 集成（迭代 3）
- Agent 模拟调用验证（迭代 3）
- GUI Block 视图改造（迭代 3）

## 8. 成功标准

- 所有现有测试通过
- `SearchService` 测试覆盖完整管线各阶段
- API `GET /knowledge/search` 返回与 MCP `search` 一致的结果结构
- MCP 回退路径使用 `BlockStore`
- Container 在 API/MCP lifespan 中正确创建和销毁
- 无全局单例直接调用（API/MCP 层）
