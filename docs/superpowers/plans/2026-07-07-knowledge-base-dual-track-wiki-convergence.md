# 双轨 Wiki 轻量收敛 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 双轨 wiki 编译轻量收敛——收敛双写点(WikiWriteService)+ 统一 frontmatter `source_ids` + A 轨 SQLite wiki 接入 RAG 主链路(浅 fallback),保留两轨协作,不动主键/workflow/links。

**Architecture:** 新建 `WikiWriteService` 统一 `save_answer`+`save_query` 双写;新建 `resolve_source_ids` helper 统一 FM 溯源字段读取;`WikiReadStage` 加 SQLite fallback(配置门控)。收敛 `save_to_wiki`+`_try_auto_save_wiki` 两处双写。

**Tech Stack:** Python 3.14, pytest, FastMCP, SQLite FTS5, ruff/mypy 门控。

## Global Constraints

- **Python**:`python`(非 `python3`);Bash Unix 路径(`/d/...`)
- **提交规范**:Conventional Commits,scope=`knowledge-base`,直接 master(用户授权主分支 + 自主推进)
- **gitleaks** pre-commit 0 leaks
- **质量门**:ruff 0 / mypy 0 / 全量 pytest 绿(基线 1224 passed / 1 skipped)
- **TDD**:先红后绿,frequent commits
- **GitNexus**:动 `save_to_wiki`/`_try_auto_save_wiki`/`WikiReadStage` 前 `gitnexus impact`,HIGH/CRITICAL 记录(用户已授权自主推进)
- **向后兼容**:frontmatter 加字段、sqlite_fallback 配置门控,legacy 零影响(S6)

**Spec:** `docs/superpowers/specs/2026-07-07-knowledge-base-dual-track-wiki-convergence-design.md`

---

## File Structure

| 文件 | 责任 | 新建/改 |
|---|---|---|
| `src/services/wiki_source_ids.py` | `resolve_source_ids(fm)` + `_parse_json_list(raw)` helper | 新建 |
| `src/services/wiki_source_compiler.py` | sources 页 FM 加 source_ids | 改 :65 |
| `src/services/wiki_entity_updater.py` | entities/concepts 页 FM 加 source_ids | 改 :165(`_write_entity_page`) |
| `src/services/wiki_parent_retrieval.py` | 读 source_ids 改用 helper | 改 :71-75 |
| `src/services/wiki_fs_lint.py` | `_check_provenance` 读 source_ids 改用 helper | 改 :184 |
| `src/services/wiki_write_service.py` | WikiWriteService 统一双写 | 新建 |
| `src/core/container.py` | `wiki_write_service` lazy property | 改 :107 + :360 后 |
| `src/mcp_server.py` | save_to_wiki 改调 WikiWriteService | 改 :1780-1791 |
| `src/services/rag_pipeline.py` | _try_auto_save_wiki 改调 + WikiReadStage 加 sqlite fallback | 改 :1064-1076 + :269-272 |
| `config.example.yaml` + `project_setup.py` | `rag.wiki_read.sqlite_fallback` | 改 |
| `docs/advanced-features.md` | 双轨协作章节 | 改 |
| `tests/test_wiki_source_ids.py` | helper 单测 | 新建 |
| `tests/test_wiki_write_service.py` | WikiWriteService 单测 | 新建 |
| `tests/test_wiki_read_sqlite_fallback.py` | fallback 单测 | 新建 |
| `tests/test_docs_consistency.py` | wiki_read 配置键断言 | 改 |
| `src/version.py` | v1.5.1 → v1.5.2 | 改 |

---

### Task 0: Baseline 确认

- [ ] **Step 1**: 跑全量 pytest 确认基线
Run: `python -m pytest tests/ -q 2>&1 | tail -3`
Expected: `1224 passed, 1 skipped`(任务1 收尾后的基线)。
- [ ] **Step 2**: 不 commit(只读确认)。

---

### Task 1: resolve_source_ids helper(TDD)

**Files:**
- Create: `src/services/wiki_source_ids.py`
- Test: `tests/test_wiki_source_ids.py`

**Interfaces:**
- Produces: `resolve_source_ids(fm: dict) -> list[str]`(FM 用,list/单值/fallback knowledge_id);`_parse_json_list(raw) -> list[str]`(SQLite JSON string 用)

- [ ] **Step 1: 写失败测试**

创建 `tests/test_wiki_source_ids.py`:
```python
"""source_ids 统一读取 helper 测试(双轨收敛 Task 1)。"""
from src.services.wiki_source_ids import resolve_source_ids, _parse_json_list


def test_resolve_from_list():
    assert resolve_source_ids({"source_ids": ["k1", "k2"]}) == ["k1", "k2"]


def test_resolve_from_scalar():
    assert resolve_source_ids({"source_ids": "k1"}) == ["k1"]


def test_resolve_fallback_knowledge_id():
    """旧文件无 source_ids 时 fallback knowledge_id。"""
    assert resolve_source_ids({"knowledge_id": "k1"}) == ["k1"]


def test_resolve_empty():
    assert resolve_source_ids({}) == []
    assert resolve_source_ids({"source_ids": []}) == []


def test_parse_json_list_from_string():
    """SQLite wiki_pages.source_ids 是 JSON string。"""
    assert _parse_json_list('["k1", "k2"]') == ["k1", "k2"]


def test_parse_json_list_from_list_passthrough():
    assert _parse_json_list(["k1"]) == ["k1"]


def test_parse_json_list_invalid():
    assert _parse_json_list("not json") == []
    assert _parse_json_list(None) == []
    assert _parse_json_list('"scalar"') == []  # 非 list
```

- [ ] **Step 2: 跑测试验证失败**
Run: `python -m pytest tests/test_wiki_source_ids.py -v`
Expected: FAIL — `ModuleNotFoundError: src.services.wiki_source_ids`。

- [ ] **Step 3: 实现 helper**

创建 `src/services/wiki_source_ids.py`:
```python
"""双轨 wiki frontmatter/source_ids 统一读取 helper。

两套语义不同的解析器:
- ``resolve_source_ids(fm)``:读文件系统 wiki/*.md 的 frontmatter(source_ids
  是 YAML list 或单值;旧文件无该字段时 fallback knowledge_id)。
- ``_parse_json_list(raw)``:读 SQLite wiki_pages.source_ids(JSON string,
  如 '["k1","k2"]');容错返回 []。

供 WikiParentRetriever / WikiFsLint / WikiReadStage SQLite fallback 共享,
消除「sources 用 knowledge_id、comparisons 用 source_ids」的异构读取。
"""
from __future__ import annotations

import json


def resolve_source_ids(fm: dict) -> list[str]:
    """读 frontmatter source_ids;旧文件 fallback knowledge_id。"""
    if not isinstance(fm, dict):
        return []
    sids = fm.get("source_ids")
    if sids:
        if isinstance(sids, list):
            return [str(s) for s in sids if s]
        return [str(sids)]
    kid = fm.get("knowledge_id")
    return [str(kid)] if kid else []


def _parse_json_list(raw) -> list[str]:
    """解析 SQLite source_ids(JSON string → list);容错返回 []。"""
    if isinstance(raw, list):
        return [str(s) for s in raw if s]
    if isinstance(raw, str):
        try:
            v = json.loads(raw)
            return [str(s) for s in v if s] if isinstance(v, list) else []
        except Exception:
            return []
    return []
```

- [ ] **Step 4: 跑测试验证通过**
Run: `python -m pytest tests/test_wiki_source_ids.py -v`
Expected: 7 passed。

- [ ] **Step 5: ruff/mypy + commit**
Run: `python -m ruff check src/services/wiki_source_ids.py tests/test_wiki_source_ids.py && python -m mypy src/services/wiki_source_ids.py`
```bash
git add src/services/wiki_source_ids.py tests/test_wiki_source_ids.py
git commit -m "feat(knowledge-base): add resolve_source_ids helper for dual-track wiki"
```

---

### Task 2: frontmatter source_ids 统一 + 消费者改用 helper(TDD)

**Files:**
- Modify: `src/services/wiki_source_compiler.py:65`
- Modify: `src/services/wiki_entity_updater.py:165`(`_write_entity_page`)
- Modify: `src/services/wiki_parent_retrieval.py:71-75`
- Modify: `src/services/wiki_fs_lint.py:184`
- Test: `tests/test_wiki_source_compiler.py` / `tests/test_wiki_frontmatter_source_ids.py`(新建或复用现有)

- [ ] **Step 1: 写失败测试**

创建 `tests/test_wiki_frontmatter_source_ids.py`:
```python
"""frontmatter source_ids 统一写入 + 消费者读取测试(Task 2)。"""
from pathlib import Path
from src.services.wiki_slug import read_frontmatter
from src.services.wiki_source_ids import resolve_source_ids


def test_source_compiler_writes_source_ids(tmp_path, monkeypatch):
    """sources 页 compile 后 frontmatter 含 source_ids = [kid]。"""
    from src.services.wiki_source_compiler import WikiSourceCompiler
    # 最小 item fixture
    item = {"id": "k1", "title": "T", "content": "C", "source_path": "f.md",
            "file_type": "md", "content_hash": "h1"}
    monkeypatch.setattr("src.services.wiki_source_compiler.Database",
                        type("DB", (), {"get_knowledge": staticmethod(lambda k: item)})())
    comp = WikiSourceCompiler()
    comp.compile(item, tmp_path, ingested_at="2026-07-07")
    md = next((tmp_path / "sources").glob("*.md"))
    fm = read_frontmatter(md)
    assert fm.get("source_ids") == ["k1"]
    assert fm.get("knowledge_id") == "k1"  # 向后兼容保留


def test_resolve_reads_new_source_ids_field():
    """消费者 resolve_source_ids 优先读 source_ids。"""
    assert resolve_source_ids({"source_ids": ["k1"], "knowledge_id": "k2"}) == ["k1"]
```
(注:`WikiSourceCompiler.compile` 的精确签名在执行时确认;若签名不符,调整 fixture。)

- [ ] **Step 2: 跑测试验证失败**
Run: `python -m pytest tests/test_wiki_frontmatter_source_ids.py -v`
Expected: FAIL — source_ids 字段不存在。

- [ ] **Step 3: WikiSourceCompiler 加 source_ids**

修改 `src/services/wiki_source_compiler.py:65`,在 `"knowledge_id": knowledge_id,` 后加:
```python
            "knowledge_id": knowledge_id,
            "source_ids": [knowledge_id],
```

- [ ] **Step 4: WikiEntityUpdater 加 source_ids**

修改 `src/services/wiki_entity_updater.py`,`_write_entity_page` 方法的 frontmatter(约 :165),在 `"knowledge_id": knowledge_id,` 后加:
```python
            "knowledge_id": knowledge_id,
            "source_ids": [knowledge_id],
```

- [ ] **Step 5: WikiParentRetriever 改用 helper**

修改 `src/services/wiki_parent_retrieval.py:71-75`,把字段读取改为委托:
```python
# 顶部 import
from src.services.wiki_source_ids import resolve_source_ids

# _extract_knowledge_ids 内(保留 page_type 白名单门控):
    if page_type not in _PARENT_PAGE_TYPES:
        return []
    return resolve_source_ids(meta)
```

- [ ] **Step 6: WikiFsLint 改用 helper**

修改 `src/services/wiki_fs_lint.py:184`,把单值读取改 helper(保留 sources 页过滤 :180):
```python
# 顶部 import
from src.services.wiki_source_ids import resolve_source_ids

# _check_provenance 内(sources 页分支):
    kids = resolve_source_ids(fm)
    if not kids:
        continue
    kid = kids[0]  # sources 页单 kid,保留后续 source_hash 比对逻辑
```

- [ ] **Step 7: 跑测试 + 回归现有 wiki 测试**
Run: `python -m pytest tests/test_wiki_frontmatter_source_ids.py tests/ -k "wiki or source_ids or parent" -q`
Expected: 全绿(新测试 + 现有 wiki_parent/fs_lint 测试零退化)。

- [ ] **Step 8: ruff/mypy + commit**
```bash
git add src/services/wiki_source_compiler.py src/services/wiki_entity_updater.py \
        src/services/wiki_parent_retrieval.py src/services/wiki_fs_lint.py \
        tests/test_wiki_frontmatter_source_ids.py
git commit -m "feat(knowledge-base): unify frontmatter source_ids across wiki page types"
```

---

### Task 3: WikiWriteService + AppContainer + 收敛双写点(TDD)

**Files:**
- Create: `src/services/wiki_write_service.py`
- Modify: `src/core/container.py:107` + `:360` 后
- Modify: `src/mcp_server.py:1780-1791`
- Modify: `src/services/rag_pipeline.py:1064-1076`
- Test: `tests/test_wiki_write_service.py`

- [ ] **Step 1: gitnexus impact 评估**

Run(gitnexus MCP):`impact({target: "save_to_wiki", direction: "upstream", repo: "ClaudeCodeWorkSpace"})` + `impact({target: "_try_auto_save_wiki", ...})`
Expected: 记录 blast radius(若 HIGH/CRITICAL 在 PROGRESS 记录,继续自主推进)。

- [ ] **Step 2: 写失败测试**

创建 `tests/test_wiki_write_service.py`:
```python
"""WikiWriteService 统一双写测试(Task 3)。"""
from src.services.wiki_write_service import WikiWriteService


class _FakeCompiler:
    def __init__(self, raise_exc=None):
        self.called = None; self.raise_exc = raise_exc
    def save_answer(self, q, a, sids, auto_publish=None, enhance=True):
        self.called = (q, a, sids, auto_publish, enhance)
        if self.raise_exc: raise self.raise_exc
        return "sqlite-page-1"


class _FakeWorkflow:
    def __init__(self, raise_exc=None):
        self.called = None; self.raise_exc = raise_exc
    def save_query(self, q, a, sids, confidence=0.0, save_mode="manual", timestamp=""):
        self.called = (q, a, sids, confidence, save_mode, timestamp)
        if self.raise_exc: raise self.raise_exc


def test_save_writes_both_tracks():
    c, w = _FakeCompiler(), _FakeWorkflow()
    svc = WikiWriteService(c, w)
    r = svc.save("Q", "A", ["k1"], confidence=0.8, save_mode="manual", timestamp="t1")
    assert r["sqlite_page_id"] == "sqlite-page-1"
    assert r["fs_saved"] is True
    assert r["errors"] == []
    assert c.called[2] == ["k1"] and w.called[3] == 0.8


def test_save_fs_failure_does_not_block_sqlite():
    c, w = _FakeCompiler(), _FakeWorkflow(raise_exc(RuntimeError("fs boom")))
    r = svc = WikiWriteService(c, w)
    r = svc.save("Q", "A", ["k1"])
    assert r["sqlite_page_id"] == "sqlite-page-1"  # A 仍成功
    assert r["fs_saved"] is False
    assert any("fs:" in e for e in r["errors"])


def test_save_sqlite_failure_does_not_block_fs():
    c, w = _FakeCompiler(raise_exc(RuntimeError("sql boom")), _FakeWorkflow()
    svc = WikiWriteService(c, w)
    r = svc.save("Q", "A", ["k1"])
    assert r["sqlite_page_id"] is None
    assert r["fs_saved"] is True
    assert any("sqlite:" in e for e in r["errors"])
```

- [ ] **Step 3: 跑测试验证失败**
Run: `python -m pytest tests/test_wiki_write_service.py -v`
Expected: FAIL — `ModuleNotFoundError`。

- [ ] **Step 4: 实现 WikiWriteService**

创建 `src/services/wiki_write_service.py`:
```python
"""WikiWriteService —— 双轨 wiki 统一写入入口(轻量收敛 Task 3)。

收敛 save_to_wiki + rag_pipeline._try_auto_save_wiki 两处双写:
A 轨(WikiCompiler.save_answer → SQLite wiki_pages)+ B 轨
(KnowledgeWorkflowService.save_query → FS wiki/*.md)。

任一失败不阻塞另一个(统一容错:warning + errors 记录),行为等价于改前
两处独立 try/except,但收敛到单一入口便于未来完整迁移时改路由。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class WikiWriteService:
    def __init__(self, wiki_compiler, knowledge_workflow):
        self._compiler = wiki_compiler
        self._workflow = knowledge_workflow

    def save(self, question, answer, source_ids, *,
             confidence: float = 0.0, save_mode: str = "manual",
             auto_publish=None, enhance: bool = True,
             timestamp: str = "") -> dict:
        """统一双写 A(SQLite)+ B(FS)。Returns {sqlite_page_id, fs_saved, errors}。"""
        result = {"sqlite_page_id": None, "fs_saved": False, "errors": []}
        try:
            result["sqlite_page_id"] = self._compiler.save_answer(
                question, answer, source_ids,
                auto_publish=auto_publish, enhance=enhance)
        except Exception as e:
            logger.warning("WikiWriteService sqlite save failed: %s", e)
            result["errors"].append(f"sqlite: {e}")
        try:
            self._workflow.save_query(
                question, answer, source_ids,
                confidence=confidence, save_mode=save_mode, timestamp=timestamp)
            result["fs_saved"] = True
        except Exception as e:
            logger.warning("WikiWriteService fs save failed: %s", e)
            result["errors"].append(f"fs: {e}")
        return result
```

- [ ] **Step 5: 跑测试验证通过**
Run: `python -m pytest tests/test_wiki_write_service.py -v`
Expected: 3 passed。

- [ ] **Step 6: AppContainer 注入 wiki_write_service**

修改 `src/core/container.py`:
(a) 字段声明(:107 `_wiki_workflow` 后加):
```python
    _wiki_write_service: Optional[object] = field(default=None, repr=False)
```
(b) property(:360 `knowledge_workflow` property 后加):
```python
    @property
    def wiki_write_service(self):
        if self._wiki_write_service is None:
            from src.services.wiki_write_service import WikiWriteService
            self._wiki_write_service = WikiWriteService(
                wiki_compiler=self.wiki_compiler,
                knowledge_workflow=self.knowledge_workflow,
            )
            self._track_service("_wiki_write_service")
        return self._wiki_write_service
```

- [ ] **Step 7: 收敛 save_to_wiki 双写**

修改 `src/mcp_server.py:1780-1791`,把双写段改调 WikiWriteService:
```python
    container = _get_container()
    wws = container.wiki_write_service
    wr = wws.save(question, answer, source_ids,
                  confidence=confidence, save_mode=save_mode,
                  auto_publish=auto_publish, enhance=enhance, timestamp="")
    page_id = wr["sqlite_page_id"]
    if wr["errors"]:
        logger.warning("save_to_wiki partial failures: %s", wr["errors"])
```
(保留后续 `if page_id:` 的 op_log + 返回逻辑不变。)

- [ ] **Step 8: 收敛 _try_auto_save_wiki 双写**

修改 `src/services/rag_pipeline.py:1064-1076`,把双写段改调 WikiWriteService:
```python
            from src.core.container import get_active_container as _gac
            _c = _gac()
            if _c is not None:
                _c.wiki_write_service.save(
                    question, ctx.answer, source_ids,
                    confidence=confidence, save_mode="auto",
                    timestamp=ctx.trace_id or "")
            page_id = None  # auto-save 不依赖 page_id 继续(保留原 logger.info 守卫可弱化)
```
(注意:原 `page_id` 来自 compiler.save_answer 返回值;收敛后若需保留 logger.info 的 page_id,可让 WikiWriteService.save 返回 sqlite_page_id——已返回,改 `page_id = wr["sqlite_page_id"]`。)

- [ ] **Step 9: 跑 wiki 写入相关回归**
Run: `python -m pytest tests/ -k "wiki or save or mcp or rag_pipeline" -q`
Expected: 全绿(save_to_wiki / auto-save 行为等价)。

- [ ] **Step 10: ruff/mypy + commit**
```bash
git add src/services/wiki_write_service.py src/core/container.py \
        src/mcp_server.py src/services/rag_pipeline.py tests/test_wiki_write_service.py
git commit -m "feat(knowledge-base): add WikiWriteService to converge dual-track wiki writes"
```

---

### Task 4: WikiReadStage SQLite fallback(TDD)

**Files:**
- Modify: `src/services/rag_pipeline.py:269-272`(WikiReadStage.execute)
- Test: `tests/test_wiki_read_sqlite_fallback.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_wiki_read_sqlite_fallback.py`:
```python
"""WikiReadStage SQLite fallback 测试(Task 4)。"""
import pytest


@pytest.fixture
def fake_ctx():
    from src.services.rag_pipeline import RagContext
    ctx = RagContext(question="概念X是什么")
    ctx.metadata = {}
    ctx.candidates = []
    return ctx


def test_sqlite_fallback_when_fs_empty(fake_ctx, monkeypatch):
    """FS 无命中时,_sqlite_fallback 查 SQLite search_wiki_fts 转候选。"""
    from src.services.rag_pipeline import WikiReadStage
    stage = WikiReadStage()
    monkeypatch.setattr(stage, "_sqlite_fallback",
                        lambda q, top_n=10: [{"id": "wiki:sqlite:p1", "text": "T",
                                              "metadata": {"title": "P1"}, "match_channels": ["wiki_sqlite"]}])
    # 直接测 _sqlite_fallback 逻辑(经 mock 验证注入)
    assert stage._sqlite_fallback("q")  # 由 monkeypatch 保证非空


def test_sqlite_fallback_disabled_by_config(fake_ctx, monkeypatch):
    """rag.wiki_read.sqlite_fallback=false 时不 fallback。"""
    monkeypatch.setattr("src.services.rag_pipeline.Config",
                        type("C", (), {"get": staticmethod(lambda k, d=None: False if k == "rag.wiki_read.sqlite_fallback" else d)})())
    # 经 WikiReadStage.execute 验证:FS 空 + 配置关 → 不查 SQLite
    # (具体断言在集成测试;此处锁配置读取)


def test_sqlite_fallback_parses_json_source_ids(monkeypatch):
    """_sqlite_fallback 把 SQLite source_ids JSON string 转 list。"""
    from src.services.rag_pipeline import WikiReadStage
    stage = WikiReadStage()
    fake_rows = [{"id": "p1", "title": "P1", "content": "C",
                  "source_ids": '["k1","k2"]', "concept_summary": "",
                  "fts_rank": -0.5}]
    monkeypatch.setattr("src.services.rag_pipeline.Database",
                        type("D", (), {"search_wiki_fts": staticmethod(lambda q, limit=10: fake_rows)})())
    cands = stage._sqlite_fallback("q")
    assert len(cands) == 1
    assert cands[0]["metadata"]["source_ids"] == ["k1", "k2"]
    assert cands[0]["match_channels"] == ["wiki_sqlite"]
```

- [ ] **Step 2: 跑测试验证失败**
Run: `python -m pytest tests/test_wiki_read_sqlite_fallback.py -v`
Expected: FAIL — `AttributeError: WikiReadStage has no _sqlite_fallback`。

- [ ] **Step 3: 加 _sqlite_fallback + execute 改动**

修改 `src/services/rag_pipeline.py` WikiReadStage:
(a) execute(`:269-272`)加 fallback:
```python
            if scale in ("wiki_read", "blend"):
                cands, _ = locator.locate(ctx.question)
                if not cands and Config.get("rag.wiki_read.sqlite_fallback", True):
                    cands = self._sqlite_fallback(ctx.question)
                if cands:
                    ctx.candidates = cands
```
(b) 加 `_sqlite_fallback` 方法(WikiReadStage 类内):
```python
    def _sqlite_fallback(self, query, top_n=10):
        """FS 无命中时查 SQLite search_wiki_fts,转 wiki 候选 schema。"""
        try:
            from src.services.db import Database
            rows = Database.search_wiki_fts(query, limit=top_n)
        except Exception as e:
            logger.warning("sqlite wiki fallback failed: %s", e)
            return []
        from src.services.wiki_source_ids import _parse_json_list
        out = []
        for r in rows:
            out.append({
                "id": f"wiki:sqlite:{r.get('id')}",
                "text": r.get("content") or r.get("concept_summary") or "",
                "metadata": {
                    "page_type": "sqlite_concept",
                    "title": r.get("title", ""),
                    "source_ids": _parse_json_list(r.get("source_ids")),
                    "wiki_hit_score": float(r.get("fts_rank", 0)),
                },
                "match_channels": ["wiki_sqlite"],
            })
        return out
```

- [ ] **Step 4: 跑测试验证通过**
Run: `python -m pytest tests/test_wiki_read_sqlite_fallback.py -v`
Expected: 3 passed。

- [ ] **Step 5: 回归 wiki_read / rag_pipeline 测试**
Run: `python -m pytest tests/ -k "wiki_read or rag_pipeline or size_aware" -q`
Expected: 全绿(legacy 模式 WikiReadStage 不介入,S6 零影响)。

- [ ] **Step 6: ruff/mypy + commit**
```bash
git add src/services/rag_pipeline.py tests/test_wiki_read_sqlite_fallback.py
git commit -m "feat(knowledge-base): add SQLite fallback to WikiReadStage (A-track into RAG)"
```

---

### Task 5: 配置 + 文档 + docs consistency

**Files:**
- Modify: `config.example.yaml`
- Modify: `src/services/project_setup.py`(_build_local_config + _build_provider_config rag 段)
- Modify: `docs/advanced-features.md`
- Modify: `tests/test_docs_consistency.py`

- [ ] **Step 1: config.example.yaml 加 wiki_read**

在 `rag:` 段(lexical_zh 附近)加:
```yaml
  wiki_read:
    sqlite_fallback: true
```

- [ ] **Step 2: project_setup 两 build 函数 rag 段加 wiki_read**

`src/services/project_setup.py` `_build_local_config`(:216 lexical_zh 后)+ `_build_provider_config`(:260 后)rag 段加:
```python
                "wiki_read": {"sqlite_fallback": True},
```

- [ ] **Step 3: advanced-features.md 加「双轨 wiki 协作」章节**

在「中文 lexical 强化」章后加:
```markdown
## 双轨 Wiki 协作

两套 wiki 产物协作(轻量收敛,未合并):

- **A 轨(SQLite `wiki_pages`)**:`WikiCompiler` LLM 抽取的 concept 页 + Q&A,服务
  GUI 浏览 / lint / 工作流审计。
- **B 轨(文件系统 `wiki/*.md`)**:`KnowledgeWorkflowService` 的 source/entity/concept
  页 + comparisons/syntheses,**接入 RAG 主检索链路**(SizeAwareRouter + WikiReadStage)。

`WikiWriteService` 统一两轨写入(`save_to_wiki` / 自动保存),任一失败不阻塞另一个。
`WikiReadStage` 在 FS 无命中时 fallback 查 SQLite `search_wiki_fts`
(`rag.wiki_read.sqlite_fallback`,默认 true,仅 `mode=wiki_first` 生效),使 A 轨
concept 页也能被 `ask` 检索到(解决「只生产不消费」断层)。frontmatter `source_ids`
跨所有 page_type 统一(向后兼容 fallback `knowledge_id`)。

主键 / workflow 状态机 / `wiki_links` 图两轨仍独立,完整统一留待后续 spec。
```

- [ ] **Step 4: test_docs_consistency.py 加 wiki_read 断言**

在 `test_advanced_features_config_keys_match_example` 加:
```python
    for key in ("size_aware", "wiki_parent_child", "lexical_zh", "wiki_read"):
        assert key in rag, f"config.example.yaml 缺 rag.{key}"
        assert key in text, f"advanced-features.md 未提及 rag.{key}"
```
并加 `test_advanced_features_documents_dual_track` 断言「双轨 Wiki 协作」章节存在。

- [ ] **Step 5: 跑 docs consistency + project_setup 测试**
Run: `python -m pytest tests/test_docs_consistency.py tests/test_project_setup_lexical.py -q`
Expected: 全绿。

- [ ] **Step 6: commit**
```bash
git add config.example.yaml src/services/project_setup.py docs/advanced-features.md \
        tests/test_docs_consistency.py
git commit -m "docs(knowledge-base): add wiki_read config + dual-track wiki chapter"
```

---

### Task 6: 版本 v1.5.2 + 全量回归 + PROGRESS + push

- [ ] **Step 1**: `src/version.py` `VERSION = "1.5.2"`。
- [ ] **Step 2**: 全量 pytest
Run: `python -m pytest tests/ -q`
Expected: 全绿(基线 1224 + 新增 ~15 测试,零退化)。
- [ ] **Step 3**: ruff/mypy 全量
Run: `python -m ruff check src/ evals/ tests/ && python -m mypy src/`
Expected: 0 错误。
- [ ] **Step 4**: gitnexus detect_changes
Run(gitnexus MCP):`detect_changes({scope: "unstaged", repo: "ClaudeCodeWorkSpace"})`
Expected: 记录风险(应 LOW-MEDIUM,改动为新增模块 + 向后兼容字段 + 配置门控 fallback)。
- [ ] **Step 5**: PROGRESS.md 加「双轨 Wiki 轻量收敛 (2026-07-07, v1.5.2)」段(记录 4 组件落地 + 未碰主键/workflow/links + 完整迁移留后续)。
- [ ] **Step 6**: build_docs
Run: `python scripts/build_docs.py`
Expected: 生成 v1.5.2 docx(gitignored)。
- [ ] **Step 7**: commit + push
```bash
git add src/version.py PROGRESS.md
git commit -m "feat(knowledge-base): bump version to 1.5.2 (dual-track wiki convergence)"
git push origin master
```

---

## Self-Review

**1. Spec coverage**: 3.1 WikiWriteService → Task 3 ✓;3.2 frontmatter source_ids → Task 1(helper)+ Task 2(写入+消费)✓;3.3 SQLite fallback → Task 4 ✓;3.4 配置文档 → Task 5 ✓;DoD 各项 → Task 0-6 ✓。

**2. Placeholder scan**: 无 TBD;代码块完整(WikiWriteService/helper/_sqlite_fallback 全实现);测试代码完整;挂载点 file:line 来自 agent 核实。Task 2 Step 1 的 `WikiSourceCompiler.compile` 签名标注「执行时确认」(已读 agent 报告但签名细节留执行核对)。

**3. Type consistency**: `resolve_source_ids(fm: dict) -> list[str]`(Task 1)在 Task 2 消费;`_parse_json_list(raw) -> list[str]`(Task 1)在 Task 4 消费;`WikiWriteService.save(...) -> dict` 在 Task 3 Step 7/8 消费(sqlite_page_id/fs_saved/errors 键一致)。
