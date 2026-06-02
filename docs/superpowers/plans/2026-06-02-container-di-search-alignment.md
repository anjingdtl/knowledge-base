# Container DI + Search Alignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrate AppContainer into MCP/API layers and align search capabilities across both interfaces.

**Architecture:** Extract SearchService class to encapsulate full search pipeline (rewrite → hybrid → rerank → wiki priority). Inject Container into API via FastAPI Depends and into MCP via module-level variable. Replace all global singleton calls with Container-based access.

**Tech Stack:** Python 3.12+, FastAPI, FastMCP, pytest

---

## Task 1: SearchService 类

**Files:**
- Create: `src/services/search_service.py`
- Test: `tests/test_search_service.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_search_service.py
"""SearchService 单元测试 — 验证完整搜索管线"""
import pytest
from unittest.mock import Mock, patch
from src.services.search_service import SearchService


class TestSearchService:
    def test_search_calls_rewrite_hybrid_rerank(self):
        """验证管线各阶段被调用"""
        config = Mock()
        config.get.side_effect = lambda key, default=None: {
            "rag.enable_query_rewriting": True,
            "rag.enable_rerank": True,
        }.get(key, default)
        
        db = Mock()
        db.get_knowledge.return_value = {"title": "测试标题"}
        db.search_wiki_fts.return_value = []
        
        block_store = Mock()
        embedding = Mock()
        llm = Mock()
        
        service = SearchService(config, db, block_store, embedding, llm)
        
        with patch.object(service, '_rewrite_query', return_value=["query", "rewrite1"]) as mock_rewrite, \
             patch.object(service, '_hybrid_search', return_value=[{"id": "b1", "text": "text", "metadata": {"page_id": "k1"}, "rrf_score": 0.9}]) as mock_hybrid, \
             patch.object(service, '_rerank', return_value=[{"id": "b1", "text": "text", "metadata": {"page_id": "k1"}, "rerank_score": 0.95}]) as mock_rerank:
            
            results = service.search("test query", top_k=5)
            
            mock_rewrite.assert_called_once_with("test query")
            mock_hybrid.assert_called_once_with(["query", "rewrite1"], 5)
            mock_rerank.assert_called_once()
            assert len(results) == 1
            assert results[0]["source"] == "knowledge"
            assert results[0]["knowledge_id"] == "k1"

    def test_search_wiki_priority(self):
        """Wiki 结果排在前面"""
        config = Mock()
        config.get.return_value = False
        
        db = Mock()
        db.search_wiki_fts.return_value = [
            {"title": "Wiki Page", "concept_summary": "summary", "content": "content", "id": "w1"}
        ]
        db.get_knowledge.return_value = {"title": "Knowledge"}
        
        block_store = Mock()
        embedding = Mock()
        llm = Mock()
        
        service = SearchService(config, db, block_store, embedding, llm)
        
        with patch.object(service, '_rewrite_query', return_value=["query"]), \
             patch.object(service, '_hybrid_search', return_value=[{"id": "b1", "text": "text", "metadata": {"page_id": "k1"}, "rrf_score": 0.9}]), \
             patch.object(service, '_rerank', return_value=[{"id": "b1", "text": "text", "metadata": {"page_id": "k1"}, "rerank_score": 0.95}]):
            
            results = service.search("test", top_k=5)
            
            assert len(results) == 2
            assert results[0]["source"] == "wiki"
            assert results[1]["source"] == "knowledge"

    def test_search_fallback_to_block_store(self):
        """HybridSearcher 失败时回退 BlockStore"""
        config = Mock()
        config.get.return_value = False
        
        db = Mock()
        db.search_wiki_fts.return_value = []
        db.get_knowledge.return_value = {"title": "Test"}
        
        block_store = Mock()
        block_store.search.return_value = [
            {"id": "b1", "text": "fallback text", "metadata": {"page_id": "k1"}, "distance": 0.2}
        ]
        
        embedding = Mock()
        llm = Mock()
        
        service = SearchService(config, db, block_store, embedding, llm)
        
        with patch.object(service, '_rewrite_query', return_value=["query"]), \
             patch.object(service, '_hybrid_search', side_effect=Exception("Hybrid failed")), \
             patch.object(service, '_rerank', return_value=[{"id": "b1", "text": "fallback text", "metadata": {"page_id": "k1"}, "distance": 0.2}]):
            
            results = service.search("test", top_k=5)
            
            block_store.search.assert_called_once_with("query", top_k=5)
            assert len(results) == 1
            assert results[0]["text"] == "fallback text"

    def test_search_returns_correct_structure(self):
        """返回结构包含 source, score, knowledge_id"""
        config = Mock()
        config.get.return_value = False
        
        db = Mock()
        db.search_wiki_fts.return_value = []
        db.get_knowledge.return_value = {"title": "Test Title"}
        
        block_store = Mock()
        embedding = Mock()
        llm = Mock()
        
        service = SearchService(config, db, block_store, embedding, llm)
        
        with patch.object(service, '_rewrite_query', return_value=["query"]), \
             patch.object(service, '_hybrid_search', return_value=[{"id": "b1", "text": "text", "metadata": {"page_id": "k1"}, "rrf_score": 0.85}]), \
             patch.object(service, '_rerank', return_value=[{"id": "b1", "text": "text", "metadata": {"page_id": "k1"}, "rerank_score": 0.9}]):
            
            results = service.search("test", top_k=5)
            
            assert len(results) == 1
            r = results[0]
            assert "source" in r
            assert "score" in r
            assert "knowledge_id" in r
            assert "title" in r
            assert "text" in r
            assert r["source"] == "knowledge"
            assert r["score"] == 0.9
            assert r["title"] == "Test Title"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_search_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.services.search_service'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/services/search_service.py
"""统一搜索服务 — MCP 和 API 共用"""
import logging
from src.services.query_rewriter import QueryRewriter
from src.services.hybrid_search import HybridSearcher
from src.services.reranker import LLMReranker

logger = logging.getLogger(__name__)


class SearchService:
    """统一搜索服务 — 封装完整搜索管线
    
    管线流程：查询改写 → 混合检索 → 重排序 → Wiki 优先
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
        try:
            candidates = self._hybrid_search(queries, top_k)
        except Exception as e:
            logger.warning("Hybrid search failed, falling back to BlockStore: %s", e)
            candidates = self._block_store.search(query, top_k=top_k)
        
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
    
    def _rewrite_query(self, query: str) -> list[str]:
        """查询改写，失败回退 [query]"""
        if not self._config.get("rag.enable_query_rewriting", False):
            return [query]
        try:
            rewriter = QueryRewriter(self._llm, self._config)
            return rewriter.rewrite(query)
        except Exception as e:
            logger.warning("Query rewrite failed: %s", e)
            return [query]
    
    def _hybrid_search(self, queries: list[str], top_k: int) -> list[dict]:
        """混合检索"""
        searcher = HybridSearcher(self._db, self._block_store, self._config)
        return searcher.search(queries, top_k=top_k)
    
    def _rerank(self, query: str, candidates: list[dict], top_k: int) -> list[dict]:
        """重排序，失败保留原序"""
        if not self._config.get("rag.enable_rerank", False):
            return candidates
        try:
            reranker = LLMReranker(self._llm, self._config)
            return reranker.rerank(query, candidates, top_n=top_k)
        except Exception as e:
            logger.warning("Rerank failed: %s", e)
            return candidates
    
    def _wiki_search(self, query: str) -> list[dict]:
        """Wiki 搜索"""
        if not self._config.get("wiki.enabled", False):
            return []
        try:
            wiki_results = self._db.search_wiki_fts(query, limit=3)
            output = []
            for wr in wiki_results:
                summary = wr.get("concept_summary", "")
                content_preview = (wr.get("content", "") or "")[:300]
                output.append({
                    "source": "wiki",
                    "knowledge_id": wr.get("id", ""),
                    "title": wr["title"],
                    "summary": summary,
                    "text": f"[Wiki] {wr['title']}: {summary}\n{content_preview}",
                    "score": wr.get("fts_rank", 0),
                })
            return output
        except Exception as e:
            logger.warning("Wiki search failed: %s", e)
            return []
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_search_service.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/services/search_service.py tests/test_search_service.py
git commit -m "feat: add SearchService — unified search pipeline for MCP and API"
```

---

## Task 2: Container search_service 属性

**Files:**
- Modify: `src/core/container.py`
- Test: `tests/test_container.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_container.py (追加到现有文件)
class TestSearchServiceIntegration:
    def test_container_search_service_accessible(self):
        """search_service 属性可访问"""
        from src.core.container import create_container
        container = create_container()
        assert container.search_service is not None
        assert hasattr(container.search_service, 'search')
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_container.py::TestSearchServiceIntegration -v`
Expected: FAIL with `AttributeError: 'AppContainer' object has no attribute 'search_service'`

- [ ] **Step 3: Write minimal implementation**

在 `src/core/container.py` 的 `AppContainer` dataclass 中添加：

```python
@dataclass
class AppContainer:
    # ... 现有属性 ...
    _search_service: Optional[object] = field(default=None, repr=False)
    
    # ... 现有 properties ...
    
    @property
    def search_service(self):
        if self._search_service is None:
            from src.services.search_service import SearchService
            self._search_service = SearchService(
                self.config, self.db, self.block_store, self.embedding, self.llm
            )
        return self._search_service
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_container.py::TestSearchServiceIntegration -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/core/container.py tests/test_container.py
git commit -m "feat: add search_service property to AppContainer"
```

---

## Task 3: API deps.py

**Files:**
- Create: `src/api/deps.py`

- [ ] **Step 1: Write the implementation**

```python
# src/api/deps.py
"""FastAPI 依赖注入"""
from fastapi import Request
from src.core.container import AppContainer


def get_container(request: Request) -> AppContainer:
    """从 FastAPI app.state 获取 Container"""
    return request.app.state.container
```

- [ ] **Step 2: Commit**

```bash
git add src/api/deps.py
git commit -m "feat: add FastAPI dependency injection for Container"
```

---

## Task 4: API lifespan 改造

**Files:**
- Modify: `src/api/__init__.py`

- [ ] **Step 1: Write the implementation**

替换 `src/api/__init__.py` 中的 `lifespan` 函数：

```python
from src.core.container import create_container, shutdown_container

@asynccontextmanager
async def lifespan(app: FastAPI):
    container = create_container()
    app.state.container = container
    yield
    shutdown_container(container)
```

移除原有的 `Config.load()` 和 `Database.connect()` 调用（已在 `create_container()` 中处理）。

- [ ] **Step 2: Run all API tests**

Run: `pytest tests/test_api.py -v`
Expected: PASS (Container 在 lifespan 中正确创建)

- [ ] **Step 3: Commit**

```bash
git add src/api/__init__.py
git commit -m "refactor: use Container in API lifespan"
```

---

## Task 5: API routes 改造

**Files:**
- Modify: `src/api/routes.py`

- [ ] **Step 1: Update imports**

在 `src/api/routes.py` 顶部添加：

```python
from src.api.deps import get_container
from src.core.container import AppContainer
```

- [ ] **Step 2: Update search endpoint**

替换 `search_knowledge` 函数（约 line 114-117）：

```python
@kb_router.get("/search")
def search_knowledge(q: str, top_k: int = 10, 
                     container: AppContainer = Depends(get_container)):
    results = container.search_service.search(q, top_k=top_k)
    return {"results": results, "total": len(results)}
```

- [ ] **Step 3: Update create endpoint**

在 `create_knowledge` 函数中添加 `container` 参数，替换 `Database.insert_knowledge` 为 `container.db.insert_knowledge`，替换 `VectorStore().delete_by_knowledge` 为 `container.block_store.delete_by_page`。

- [ ] **Step 4: Update delete endpoint**

在 `delete_knowledge` 函数中添加 `container` 参数，替换 `VectorStore().delete_by_knowledge` 为 `container.block_store.delete_by_page`，替换 `Database.delete_knowledge` 为 `container.db.delete_knowledge`。

- [ ] **Step 5: Update all other endpoints**

遍历 `routes.py` 中所有使用 `Database.xxx()`、`VectorStore()`、`RAGService()` 的端点，添加 `container` 参数并替换为 `container.xxx`。

主要端点：
- `list_knowledge` → `container.db.list_knowledge`
- `get_knowledge` → `container.db.get_knowledge`
- `update_knowledge` → `container.db.update_knowledge`
- `ask_question` → `container.rag_pipeline.query`

- [ ] **Step 6: Run all API tests**

Run: `pytest tests/test_api.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/api/routes.py
git commit -m "refactor: inject Container into all API routes"
```

---

## Task 6: MCP lifespan 改造

**Files:**
- Modify: `src/mcp_server.py`

- [ ] **Step 1: Update imports and add module-level container**

在 `src/mcp_server.py` 顶部添加：

```python
from src.core.container import create_container, shutdown_container, AppContainer
```

在模块级添加：

```python
_container: AppContainer | None = None
```

- [ ] **Step 2: Update server_lifespan**

替换 `server_lifespan` 函数（约 line 49-58）：

```python
@asynccontextmanager
async def server_lifespan(server: FastMCP):
    global _container
    _container = create_container()
    beat()
    _heartbeat_task = asyncio.create_task(_heartbeat_loop())
    yield {}
    _heartbeat_task.cancel()
    shutdown_container(_container)
```

移除原有的 `Config.load()` 和 `Database.connect()` 调用。

- [ ] **Step 3: Run MCP tests**

Run: `pytest tests/test_mcp_server.py -v`
Expected: PASS (Container 在 lifespan 中正确创建)

- [ ] **Step 4: Commit**

```bash
git add src/mcp_server.py
git commit -m "refactor: use Container in MCP lifespan"
```

---

## Task 7: MCP tools 改造

**Files:**
- Modify: `src/mcp_server.py`

- [ ] **Step 1: Update search tool**

替换 `search` 函数（约 line 88-95）：

```python
@mcp.tool(
    description="基于语义相似度搜索知识库。使用向量嵌入查找与查询含义最相关的知识条目。Wiki 结构化知识优先返回。",
    annotations={"readOnlyHint": True, "openWorldHint": False},
)
@_heartbeat
def search(query: str, top_k: int = 5) -> list[dict]:
    """基于语义的向量搜索"""
    return _container.search_service.search(query, top_k=top_k)
```

移除原有的 `_do_search` 函数（约 line 98-170）。

- [ ] **Step 2: Update create tool**

在 `create` 函数中替换 `Database.insert_knowledge` 为 `_container.db.insert_knowledge`。

- [ ] **Step 3: Update delete tool**

在 `delete` 函数中替换 `VectorStore().delete_by_knowledge` 为 `_container.block_store.delete_by_page`，替换 `Database.delete_knowledge` 为 `_container.db.delete_knowledge`。

- [ ] **Step 4: Update all other tools**

遍历 `mcp_server.py` 中所有使用 `Database.xxx()`、`VectorStore()`、`HybridSearcher()`、`RAGService()` 的工具，替换为 `_container.xxx`。

主要工具：
- `read` → `_container.db.get_knowledge`
- `update` → `_container.db.update_knowledge`
- `list_knowledge` → `_container.db.list_knowledge`
- `ask` → `_container.rag_pipeline.query`
- `search_fulltext` → `_container.db.search_knowledge`

- [ ] **Step 5: Run all MCP tests**

Run: `pytest tests/test_mcp_server.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/mcp_server.py
git commit -m "refactor: inject Container into all MCP tools"
```

---

## Task 8: 全量测试验证

**Files:**
- Modify: `tests/test_api.py` (适配 Container)
- Modify: `tests/test_mcp_server.py` (适配 Container)

- [ ] **Step 1: Update test_api.py fixtures**

在 `tests/test_api.py` 的 `api_client` fixture 中，确保 Container 正确注入。可能需要 mock `create_container` 或调整测试设置。

- [ ] **Step 2: Update test_mcp_server.py fixtures**

在 `tests/test_mcp_server.py` 中，确保 `_container` 在测试中正确设置。可能需要在测试 setup 中调用 `create_container()`。

- [ ] **Step 3: Run full test suite**

Run: `pytest tests/ -v`
Expected: PASS (all tests)

- [ ] **Step 4: Commit**

```bash
git add tests/test_api.py tests/test_mcp_server.py
git commit -m "test: adapt API and MCP tests for Container integration"
```

---

## Self-Review

**1. Spec coverage:**
- [x] SearchService class → Task 1
- [x] Container search_service property → Task 2
- [x] API deps.py → Task 3
- [x] API lifespan → Task 4
- [x] API routes → Task 5
- [x] MCP lifespan → Task 6
- [x] MCP tools → Task 7
- [x] Tests → Task 8

All spec requirements covered.

**2. Placeholder scan:** No TBD/TODO found. All code blocks complete.

**3. Type consistency:**
- `SearchService(config, db, block_store, embedding, llm)` — consistent across Tasks 1, 2
- `container.search_service.search(query, top_k)` — consistent across Tasks 2, 5, 7
- `get_container(request)` — consistent across Tasks 3, 5
- `_container` module variable — consistent across Tasks 6, 7

All checks pass.
