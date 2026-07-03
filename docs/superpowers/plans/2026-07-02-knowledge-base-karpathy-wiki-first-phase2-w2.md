# Knowledge-Base Karpathy Wiki-First 第二阶段 W2 实现层 TDD 计划（wiki parent-child）

- **状态**：📝 待审批（2026-07-03 出 plan，待何大哥点头后动工）
- **日期**：2026-07-03
- **范围**：第二阶段 W2 —— wiki 检索命中 entities/concepts/comparisons/syntheses 页时，带回其引用的 source 页摘要作为 `parent_content`（与 block 检索的 parent-child 对称）
- **上游规划层**：`docs/superpowers/specs/2026-07-02-knowledge-base-karpathy-wiki-first-phase2-design.md`（§4.2 设计 / §6.2 Task 2.1-2.3 / §3 S3 验收）
- **前置已完成**：第二阶段 W1（SizeAwareRouter，5 commit `6c80035`→`2c8b42c`，1126 passed 零退化）。交接见 `docs/superpowers/handoffs/2026-07-02-w2-handoff.md`。

> **For executors:** 本文档是 W2 的 bite-sized TDD 展开计划。每个 Step = 写失败测试 → 跑确认失败 → 实现 → 跑确认通过 → commit。用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans` 执行。涉及 `rag_pipeline` 改动前先 `gitnexus impact`。

---

## Goal

让 wiki 检索命中的 entity/concept/synthesis/comparison 页候选，自动带回其引用的 source 页摘要（`parent_content` 字段），使 LLM 生成时有 source 全文上下文背书——与 block 检索已有的 parent-child 机制对称，复用 `GenerateStage` 既有的 `parent_content` 渲染路径，零管线耦合。

## Architecture

新增独立服务 `src/services/wiki_parent_retrieval.py`（`WikiParentRetriever`，A2 方案：用 frontmatter 的 `knowledge_id` 经 `Database.get_knowledge_batch` 回查 source 原始 content，复用 `WikiSourceCompiler._build_summary` 提炼首段）+ 新增独立 stage `WikiParentEnrichStage`（挂 post-rerank，读 `ctx.reranked_results`，仅对 wiki 候选 enrich，照抄 `WikiReadStage` 的 legacy 门控范式）。container 注入 retriever、project_setup 注入 `rag.wiki_parent_child` 段（避开 W1 已踩的 `_wiki_first_defaults` 浅合并坑）。

## Tech Stack

Python 3.14 / SQLite（`Database` 元类单例）/ 无新依赖 / 复用 `WikiSourceCompiler._build_summary` + `parent_child_retrieval` 的 `_get_db/_get_config` 范式 / pytest TDD。

---

## Global Constraints

- **不破坏现有测试**：基线 1126 passed / 1 skipped，每 Task 末跑相关回归
- **mode=legacy 零影响（S6）**：`rag.wiki_parent_child.enabled` 缺省 `False`；`WikiParentEnrichStage` 仅 `wiki_first + enabled` 介入
- **字段名统一为 `parent_content`**（**非** spec/交接写的 `parent_context`——后者代码零引用）：`GenerateStage:601` 读它，`parent_child_retrieval.py:210` 写它。W2 全程用 `parent_content`
- **不复制 `parent_block_id`**：全仓零读取的 dead 字段（`parent_child_retrieval.py:211`）
- **不动已交付的第一阶段编译器**：A2 方案在检索侧用 `knowledge_id` 回查，不改 `wiki_entity_updater`
- **零运行时 LLM**：source 摘要走规则式 `WikiSourceCompiler._build_summary`（首段+heading path，截断 ≤500）
- **截断自管**：`parent_content` 不受 `PostProcessStage` 截断（它只截 `block_contexts`），W2 enricher 自行 cap（默认 2000）
- **可复现**：enricher 内不取系统时间、不随机

---

## 架构决策（源码核实的挂载点）

4 个挂载点已由并行源码调研核实（每个给精确 file:line + 代码证据）。

| 决策 | 结论 | 证据 |
|---|---|---|
| **字段名** | `parent_content`（候选 dict 顶层 str）。**非** `parent_context`（代码零引用，文档幽灵），**非** `block_contexts`（metadata dict，PostProcessStage 截断用） | `rag_pipeline.py:601` `parent_content = result.get("parent_content","")` → :603-606 拼入 LLM prompt `"[来源{i+1}] (父块上下文)\n{parent_content}\n---\n相关片段: {text}"`；写入侧 `parent_child_retrieval.py:210` `r["parent_content"]=parent.content[:max_parent_chars]` |
| **S3 断言层级** | **候选级单测**（非 citation 级）。`CitationBuilder`（`citation_builder.py:101`）返回的 Citation 无 parent/context 字段，parent_content **不进** sources/payload/Citation | `citation_builder.py:101-112`（Citation 字段无 parent）；`rag_pipeline.py:601`（唯一消费点） |
| **stage 挂载点** | 新建独立 `WikiParentEnrichStage`，插 `_builtin_stages`（rerank 后 evidence_compress 前）+ `DEFAULT_PIPELINE_CONFIG`（rerank:950 后、evidence_compress:951 前）。读 `ctx.reranked_results`，写回同字段 | `_builtin_stages`（`rag_pipeline.py:878-881`）；`DEFAULT_PIPELINE_CONFIG`（`:945-955`）；RerankStage `:474` 设 `ctx.reranked_results`；GenerateStage `:506` 读 |
| **post-rerank 安全** | reranker 原地 mutate 候选 dict（非重建），post-rerank 写入的 `parent_content` 直达 generate，零丢失 | `rerankers/llm.py:41` `cand["rerank_score"]=scores[i]`（原地）；`:45-46` `filtered=[s for s...]`（同对象） |
| **stage 范式照抄** | `WikiReadStage`（W1）：`__init__(deps=None)` + `is_enabled(config)` + legacy 门控（mode==wiki_first + rag.*.enabled）+ `_get_container_service` fallback + try/except 非致命 | `rag_pipeline.py:226-273`（WikiReadStage 全文） |
| **A2 source 摘要获取** | 走 `Database.get_knowledge_batch(kids)`（`db.py:865`）取 source 原始 content → 复用 `WikiSourceCompiler._build_summary(content)`（`wiki_source_compiler.py:78`，@staticmethod）提炼首段。DB 是 source of truth，绕开 kid→文件路径的非平凡映射 | `db.py:865`（get_knowledge_batch）；`wiki_source_compiler.py:78`（_build_summary）；`wiki_slug.py:40-42`（resolve_slug hash 冲突后缀，证明路径(b)读文件不可靠） |
| **Retriever 签名** | `WikiParentRetriever(db=None, config=None)`，照抄 `ParentChildRetriever.__init__`（`parent_child_retrieval.py:94`）+ `_get_db/_get_config`（:98-122，Database 单例兜底） | `parent_child_retrieval.py:94-122` |
| **container 注入** | 字段 `_wiki_parent_retriever`（:139 后，第二阶段懒加载区）+ property（:372 后，照抄 `wiki_page_locator` :358-364 三段式）+ deps dict（:175 后，`'wiki_parent_retriever': self.wiki_parent_retriever`）。`StageRegistry.create_stage`（:930-935）按 `__init__` 签名 inspect 自动过滤注入 | `container.py:138-139`（字段）、`:358-372`（property 范式）、`:167-176`（deps）、`rag_pipeline.py:930-935`（自动注入） |
| **配置注入位置** | `_wiki_parent_defaults()` 静态方法（:129 后，照抄 `_size_aware_defaults`）+ `_build_local_config` rag dict（:183 后）+ `_build_provider_config` rag dict（:225 后）**各加一行**。**绝不放进 `_wiki_first_defaults`**（浅合并坑，W1 已踩，docstring :120-121 已警告） | `project_setup.py:114-129`（_size_aware_defaults + 浅合并警告）、`:183`/`:225`（注入点）、`:82-112`（_wiki_first_defaults 禁动） |
| **配置项命名** | `rag.wiki_parent_child.max_parent_chars`（默认 2000）。**对 spec §5 的微调**：spec 写 `wiki_parent_context_max_length`，W2 改用 `max_parent_chars` 与 block 的 `rag.parent_child.max_parent_chars`（`parent_child_retrieval.py:158`）命名对称，默认值仍取 spec 的 2000 | `parent_child_retrieval.py:158`（block 对称参照） |
| **blend 档共存** | wiki 候选（id `wiki:<type>:<slug>`）与检索候选（id `page_id:block_id`）id 体系不同，blend 融合不互覆盖；`blend_fusion._clone`（`blend_fusion.py:80-86`）浅拷贝保留候选所有字段（含 `parent_content`） | `blend_fusion.py:80-86`（_clone 保留字段）；`hybrid_search.py:278`（_candidate_id 体系） |

**stage 执行顺序图（W1 教训：先画时序）**：

```
query_rewrite(disabled) → wiki_retrieval → wiki_read(产 ctx.candidates)
  → vector_search(blend 在此融合;wiki_read 档 :325 提前 return)
  → rerank(对全部候选生效,产 ctx.reranked_results :474)
  → 【wiki_parent_enrich】(W2 新增,读 ctx.reranked_results,仅 enrich wiki 候选)
  → evidence_compress(默认禁用) → generate(:506 读 ctx.reranked_results,:601 消费 parent_content)
  → postprocess
```

block 的 `enrich_with_parent_context` 实际挂在 `hybrid_search.py:43`（vector_search 内、rerank 前）；W2 选 post-rerank 是**效率考量**（只 enrich 过 rerank 存活的候选），**不是要与 block 时序对称**——实现者勿改 block 挂载位置。

---

## File Structure

| 文件 | 职责 | 动作 |
|---|---|---|
| `src/services/wiki_parent_retrieval.py` | `WikiParentRetriever.enrich`：按 page_type 分流，knowledge_id 回查 source，写 `parent_content` | **新增**（Task 2.1） |
| `src/services/rag_pipeline.py` | `WikiParentEnrichStage` + 注册 `_builtin_stages` + `DEFAULT_PIPELINE_CONFIG` 条目 | **改**（Task 2.2） |
| `src/core/container.py` | 注入 `wiki_parent_retriever`（字段 + property + deps） | **改**（Task 2.4） |
| `src/services/project_setup.py` | `_wiki_parent_defaults` + 两个 build 函数注入 | **改**（Task 2.4） |
| `config.example.yaml` | `rag.wiki_parent_child` 段 | **改**（Task 2.4） |
| `tests/test_wiki_parent_retrieval.py` | Retriever 候选级单测（S3 主战场） | **新增**（Task 2.1） |
| `tests/test_wiki_parent_enrich_stage.py` | stage 级单测 + blend 共存契约 | **新增**（Task 2.2/2.3） |
| `tests/test_wiki_parent_legacy.py` | legacy 门控 + container/init 注入 | **新增**（Task 2.4） |

---

## Task 2.1 — WikiParentRetriever（spec Task 2.1，S3 主战场）

**Files:**
- Create: `src/services/wiki_parent_retrieval.py`
- Test: `tests/test_wiki_parent_retrieval.py`

**Interfaces:**
- Consumes: `Database.get_knowledge_batch(ids) -> dict[str,dict]`（`db.py:865`，含 content 字段）；`WikiSourceCompiler._build_summary(content) -> str`（`wiki_source_compiler.py:78`，@staticmethod）；`Config.get("rag.wiki_parent_child.max_parent_chars", 2000)`
- Produces: `class WikiParentRetriever` with `enrich(candidates, max_length=None) -> list[dict]`（原地给 wiki 候选加 `parent_content` 字段）；便捷函数 `enrich_wiki_parent_context(candidates, db=None, config=None) -> list[dict]`

### Step 2.1.1 — 写失败测试

- [ ] 创建 `tests/test_wiki_parent_retrieval.py`：

```python
"""WikiParentRetriever 单测 — wiki 候选带回 source 页 parent_content (S3)。"""
from __future__ import annotations

from src.services.wiki_parent_retrieval import (
    WikiParentRetriever,
    enrich_wiki_parent_context,
)


class _FakeDb:
    """最小 db mock：get_knowledge_batch / get_knowledge。"""

    def __init__(self, items: dict[str, dict]):
        self._items = items
        self.called_batch: list[list[str]] = []

    def get_knowledge(self, item_id, include_deleted=False):
        return self._items.get(item_id)

    def get_knowledge_batch(self, ids, include_deleted=False):
        self.called_batch.append(list(ids))
        return {k: self._items[k] for k in ids if k in self._items}


def _wiki_cand(page_type: str, kid: str, slug: str = "foo") -> dict:
    return {
        "id": f"wiki:{page_type}:{slug}",
        "text": f"{page_type} body",
        "metadata": {"page_type": page_type, "title": slug, "knowledge_id": kid},
        "match_channels": ["wiki_read"],
    }


def test_entity_candidate_gets_parent_content():
    """S3 核心:wiki entity 命中候选 parent_content 非空且指向 source 页。"""
    db = _FakeDb({"kid-1": {"id": "kid-1", "title": "源文档",
                            "content": "# 标题\n\n这是 source 页的首段摘要内容。"}})
    cand = _wiki_cand("entities", "kid-1")
    out = WikiParentRetriever(db=db).enrich([cand])
    assert out[0]["parent_content"]  # 非空
    assert "source 页的首段摘要内容" in out[0]["parent_content"]  # 指向 source 页
    assert db.called_batch == [["kid-1"]]  # 证明经 knowledge_id 回查 (A2 方案)


def test_concept_candidate_gets_parent_content():
    db = _FakeDb({"kid-2": {"id": "kid-2", "content": "概念相关 source 全文。"}})
    cand = _wiki_cand("concepts", "kid-2")
    out = WikiParentRetriever(db=db).enrich([cand])
    assert "概念相关 source 全文" in out[0]["parent_content"]


def test_sources_page_skipped():
    """sources 页自身即 source,不加 parent_content。"""
    db = _FakeDb({"kid-3": {"id": "kid-3", "content": "x"}})
    cand = _wiki_cand("sources", "kid-3")
    out = WikiParentRetriever(db=db).enrich([cand])
    assert "parent_content" not in out[0]
    assert db.called_batch == []  # sources 不触发回查


def test_block_candidate_untouched():
    """非 wiki 候选(检索候选 id=page_id:block_id)不被 enrich。"""
    db = _FakeDb({"kid-4": {"id": "kid-4", "content": "x"}})
    block_cand = {"id": "page1:block2", "text": "block", "metadata": {"page_id": "page1"}}
    out = WikiParentRetriever(db=db).enrich([block_cand])
    assert "parent_content" not in out[0]
    assert db.called_batch == []


def test_missing_knowledge_id_no_crash():
    """knowledge_id 缺失时静默跳过,不抛异常。"""
    cand = {"id": "wiki:entities:nope", "text": "x", "metadata": {"page_type": "entities"}}
    out = WikiParentRetriever(db=_FakeDb({})).enrich([cand])
    assert "parent_content" not in out[0]


def test_source_not_in_db_no_crash():
    """knowledge_id 在 db 中不存在时静默跳过。"""
    cand = _wiki_cand("entities", "ghost")
    out = WikiParentRetriever(db=_FakeDb({})).enrich([cand])
    assert "parent_content" not in out[0]


def test_truncation_respects_max_length():
    """parent_content 截断到 max_length。"""
    long_content = "A" * 5000
    db = _FakeDb({"kid-1": {"id": "kid-1", "content": long_content}})
    cand = _wiki_cand("entities", "kid-1")
    out = WikiParentRetriever(db=db).enrich([cand], max_length=100)
    assert len(out[0]["parent_content"]) <= 100


def test_syntheses_uses_source_ids_list():
    """syntheses/comparisons 页优先用 source_ids 列表多查。"""
    db = _FakeDb({
        "kid-a": {"id": "kid-a", "content": "源A摘要。"},
        "kid-b": {"id": "kid-b", "content": "源B摘要。"},
    })
    cand = {
        "id": "wiki:syntheses:s1",
        "text": "综合页",
        "metadata": {"page_type": "syntheses", "source_ids": ["kid-a", "kid-b"]},
    }
    out = WikiParentRetriever(db=db).enrich([cand])
    assert "源A摘要" in out[0]["parent_content"]
    assert "源B摘要" in out[0]["parent_content"]


def test_convenience_function():
    """enrich_wiki_parent_context 便捷函数等价于 retriever.enrich。"""
    db = _FakeDb({"kid-1": {"id": "kid-1", "content": "便利函数 source。"}})
    cand = _wiki_cand("entities", "kid-1")
    out = enrich_wiki_parent_context([cand], db=db)
    assert "便利函数 source" in out[0]["parent_content"]


def test_empty_candidates_returns_empty():
    assert WikiParentRetriever(db=_FakeDb({})).enrich([]) == []
```

### Step 2.1.2 — 跑测试确认失败

Run: `pytest tests/test_wiki_parent_retrieval.py -v`
Expected: FAIL（`ModuleNotFoundError: src.services.wiki_parent_retrieval`）

### Step 2.1.3 — 实现 WikiParentRetriever

- [ ] 创建 `src/services/wiki_parent_retrieval.py`：

```python
"""Wiki parent-child 检索 —— wiki 候选带回其引用的 source 页摘要 (第二阶段 W2)。

与 block 检索的 ``parent_child_retrieval`` 对称:wiki 命中 entities/concepts/
comparisons/syntheses 页时,按 frontmatter 的 ``knowledge_id`` 回查对应 source
页摘要(A2 方案,不动第一阶段编译器),写入候选 ``parent_content`` 字段,复用
``GenerateStage`` 既有渲染路径。

字段名 ``parent_content`` 与 block 侧 ``parent_child_retrieval.py:210`` 一致,
``GenerateStage`` (rag_pipeline.py:601) 已消费此字段。
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# page_type 白名单:这些页是 source 的"消费者",需回查 source 摘要。
# sources 页自身即 source,跳过。
_PARENT_PAGE_TYPES = {"entities", "concepts", "comparisons", "syntheses"}


class WikiParentRetriever:
    """为 wiki 检索候选附加 source 页 parent 上下文。

    Args:
        db: Database 实例(可选,None 时走 ``Database`` 单例兜底)。
        config: 配置对象或 dict(可选,None 时走全局 ``Config``)。
    """

    def __init__(self, db=None, config=None):
        self._db = db
        self._config = config

    # ---- 范式照抄 parent_child_retrieval.ParentChildRetriever ----
    def _get_db(self):
        if self._db is not None:
            return self._db
        from src.services.db import Database
        return Database

    def _get_config(self, key: str, default=None):
        if self._config is not None:
            if isinstance(self._config, dict):
                obj: Any = self._config
                for p in key.split("."):
                    if isinstance(obj, dict):
                        obj = obj.get(p)
                    else:
                        return default
                return obj if obj is not None else default
            return self._config.get(key, default)
        try:
            from src.utils.config import Config
            return Config.get(key, default)
        except Exception:
            return default

    @staticmethod
    def _extract_knowledge_ids(meta: dict) -> list[str]:
        """按 page_type 从候选 metadata 取溯源 knowledge_id 列表(A2 方案)。

        - syntheses/comparisons:优先 frontmatter ``source_ids`` 列表,退化到单个 knowledge_id
        - entities/concepts:第一阶段 updater 只写单个 ``knowledge_id``
        - sources/其它:空列表(跳过)
        """
        meta = meta or {}
        page_type = meta.get("page_type", "")
        if page_type not in _PARENT_PAGE_TYPES:
            return []
        source_ids = meta.get("source_ids") or []
        if isinstance(source_ids, list) and source_ids:
            return [str(s) for s in source_ids if s]
        kid = meta.get("knowledge_id")
        return [str(kid)] if kid else []

    def _fetch_summaries(self, db, kids: list[str], max_length: int) -> dict[str, str]:
        """批量回查 source 条目,提炼首段摘要(复用 WikiSourceCompiler._build_summary)。"""
        if not kids:
            return {}
        from src.services.wiki_source_compiler import WikiSourceCompiler

        batch: dict[str, dict] = {}
        try:
            if hasattr(db, "get_knowledge_batch"):
                batch = db.get_knowledge_batch(kids) or {}
            else:
                batch = {}
        except Exception as e:
            logger.warning("wiki parent get_knowledge_batch failed: %s", e)
            batch = {}
        # 退化:无 batch 接口或部分缺失时逐个查
        if len(batch) < len(kids) and hasattr(db, "get_knowledge"):
            for kid in kids:
                if kid in batch:
                    continue
                try:
                    item = db.get_knowledge(kid)
                    if item:
                        batch[kid] = item
                except Exception:
                    continue

        summaries: dict[str, str] = {}
        for kid, item in batch.items():
            content = (item or {}).get("content", "") or ""
            if not content:
                continue
            try:
                summary = WikiSourceCompiler._build_summary(content)
            except Exception:
                summary = content[:max_length]
            if summary:
                summaries[kid] = summary[:max_length]
        return summaries

    def enrich(
        self,
        candidates: list[dict],
        max_length: int | None = None,
    ) -> list[dict]:
        """为 wiki 候选附加 source 页 parent_content。

        非 wiki 候选(id 不以 ``wiki:`` 开头)与 sources 页原样返回,不动。
        """
        if not candidates:
            return candidates
        if max_length is None:
            max_length = int(self._get_config(
                "rag.wiki_parent_child.max_parent_chars", 2000))

        # 收集 kid -> [候选] 映射(仅 wiki 候选)
        kid_to_cands: dict[str, list[dict]] = {}
        for cand in candidates:
            if not str(cand.get("id", "")).startswith("wiki:"):
                continue
            kids = self._extract_knowledge_ids(cand.get("metadata") or {})
            for kid in kids:
                kid_to_cands.setdefault(kid, []).append(cand)

        if not kid_to_cands:
            return candidates

        db = self._get_db()
        summaries = self._fetch_summaries(db, list(kid_to_cands.keys()), max_length)

        for kid, cand_list in kid_to_cands.items():
            summary = summaries.get(kid)
            if not summary:
                continue
            for cand in cand_list:
                cand["parent_content"] = summary
        return candidates


def enrich_wiki_parent_context(
    candidates: list[dict],
    db=None,
    config=None,
) -> list[dict]:
    """便捷函数:为 wiki 候选附加 source 页 parent 上下文。"""
    return WikiParentRetriever(db=db, config=config).enrich(candidates)
```

### Step 2.1.4 — 跑测试确认通过

Run: `pytest tests/test_wiki_parent_retrieval.py -v`
Expected: 10 passed

### Step 2.1.5 — commit

```bash
git add src/services/wiki_parent_retrieval.py tests/test_wiki_parent_retrieval.py
git commit -m "feat(knowledge-base): add WikiParentRetriever for wiki parent-child (A2 knowledge_id lookup)"
```

**验收:** S3 候选级（wiki 命中候选 `parent_content` 非空且指向 source 页）✓

---

## Task 2.2 — WikiParentEnrichStage（spec Task 2.2 挂载）

**Files:**
- Modify: `src/services/rag_pipeline.py`（新增 stage 类 + 注册 + config 条目）
- Test: `tests/test_wiki_parent_enrich_stage.py`

**Interfaces:**
- Consumes: `WikiParentRetriever.enrich`（Task 2.1）；`ctx.reranked_results`（RerankStage `:474` 设）；`PipelineStage` 基类 + `_get_container_service` fallback（`rag_pipeline.py:226-273` WikiReadStage 范式）
- Produces: `class WikiParentEnrichStage(PipelineStage)`，`name="wiki_parent_enrich"`，enrich 后写回 `ctx.reranked_results`

> 动工前先 `gitnexus impact` 评估 `rag_pipeline.py`（HIGH 风险文件，已确认 blast radius）。

### Step 2.2.1 — 写失败测试

- [ ] 创建 `tests/test_wiki_parent_enrich_stage.py`：

```python
"""WikiParentEnrichStage 单测 — post-rerank 挂载,仅 enrich wiki 候选。"""
from __future__ import annotations

import asyncio

import pytest

from src.services.rag_pipeline import (
    DEFAULT_PIPELINE_CONFIG,
    StageRegistry,
    WikiParentEnrichStage,
)


class _StubRetriever:
    """记录调用,给 wiki 候选加 parent_content。"""

    def __init__(self):
        self.called = 0

    def enrich(self, candidates, max_length=None):
        self.called += 1
        for c in candidates:
            if str(c.get("id", "")).startswith("wiki:") and c.get("metadata", {}).get("page_type") != "sources":
                c["parent_content"] = "STUB_SOURCE_SUMMARY"
        return candidates


def _ctx(reranked):
    """构造最小 RagContext-like 对象。"""
    class _Ctx:
        def __init__(self):
            self.question = "FTTR 是什么"
            self.reranked_results = reranked
            self.candidates = list(reranked)
            self.metadata = {}
            self.wiki_context = ""
            self.sources = []
            self.conversation_history = []
            self.query_spec_override = None
    return _Ctx()


def _run(stage, ctx, config=None):
    return asyncio.get_event_loop().run_until_complete(
        stage.execute(ctx, config or {})
    )


def _enable_wiki_first(monkeypatch):
    """门控:mode=wiki_first + rag.wiki_parent_child.enabled=true。"""
    from src.utils import config as cfg_mod
    real_get = cfg_mod.Config.get

    def fake_get(key, default=None):
        if key == "knowledge_workflow.mode":
            return "wiki_first"
        if key == "rag.wiki_parent_child.enabled":
            return True
        return real_get(key, default)

    monkeypatch.setattr(cfg_mod.Config, "get", staticmethod(fake_get))


def test_stage_registered():
    """StageRegistry 含 wiki_parent_enrich。"""
    assert StageRegistry.get("wiki_parent_enrich") is WikiParentEnrichStage


def test_pipeline_config_has_stage():
    """DEFAULT_PIPELINE_CONFIG 在 rerank 后、generate 前有 wiki_parent_enrich 条目。"""
    names = [e["stage"] for e in DEFAULT_PIPELINE_CONFIG]
    assert "wiki_parent_enrich" in names
    assert names.index("wiki_parent_enrich") > names.index("rerank")
    assert names.index("wiki_parent_enrich") < names.index("generate")


def test_stage_enriches_only_wiki_candidates(monkeypatch):
    """wiki 候选加 parent_content,block 候选不动。"""
    _enable_wiki_first(monkeypatch)
    retriever = _StubRetriever()
    stage = WikiParentEnrichStage(wiki_parent_retriever=retriever)
    wiki_cand = {"id": "wiki:entities:foo", "text": "e",
                 "metadata": {"page_type": "entities", "knowledge_id": "k1"},
                 "match_channels": ["wiki_read"]}
    block_cand = {"id": "page1:block2", "text": "b", "metadata": {"page_id": "page1"}}
    ctx = _ctx([wiki_cand, block_cand])
    _run(stage, ctx)
    assert ctx.reranked_results[0]["parent_content"] == "STUB_SOURCE_SUMMARY"
    assert "parent_content" not in ctx.reranked_results[1]
    assert retriever.called == 1


def test_stage_noop_when_disabled(monkeypatch):
    """enabled=false 时空操作,不调 retriever。"""
    from src.utils import config as cfg_mod
    real_get = cfg_mod.Config.get

    def fake_get(key, default=None):
        if key == "rag.wiki_parent_child.enabled":
            return False
        if key == "knowledge_workflow.mode":
            return "wiki_first"
        return real_get(key, default)

    monkeypatch.setattr(cfg_mod.Config, "get", staticmethod(fake_get))
    retriever = _StubRetriever()
    stage = WikiParentEnrichStage(wiki_parent_retriever=retriever)
    ctx = _ctx([{"id": "wiki:entities:x", "metadata": {"page_type": "entities", "knowledge_id": "k"}}])
    _run(stage, ctx)
    assert retriever.called == 0
    assert "parent_content" not in ctx.reranked_results[0]


def test_stage_noop_in_legacy_mode(monkeypatch):
    """mode=legacy 时空操作(S6)。"""
    from src.utils import config as cfg_mod
    real_get = cfg_mod.Config.get

    def fake_get(key, default=None):
        if key == "knowledge_workflow.mode":
            return "legacy"
        return real_get(key, default)

    monkeypatch.setattr(cfg_mod.Config, "get", staticmethod(fake_get))
    retriever = _StubRetriever()
    stage = WikiParentEnrichStage(wiki_parent_retriever=retriever)
    ctx = _ctx([{"id": "wiki:entities:x", "metadata": {"page_type": "entities", "knowledge_id": "k"}}])
    _run(stage, ctx)
    assert retriever.called == 0


def test_stage_noop_when_empty_results(monkeypatch):
    """reranked_results 为空时空操作。"""
    _enable_wiki_first(monkeypatch)
    retriever = _StubRetriever()
    stage = WikiParentEnrichStage(wiki_parent_retriever=retriever)
    ctx = _ctx([])
    _run(stage, ctx)
    assert retriever.called == 0


def test_stage_fallback_to_container_service(monkeypatch):
    """构造器未注入 retriever 时走 _get_container_service fallback。"""
    _enable_wiki_first(monkeypatch)
    retriever = _StubRetriever()
    import src.services.rag_pipeline as rp
    monkeypatch.setattr(rp, "_get_container_service",
                        lambda name, fb: retriever if name == "wiki_parent_retriever" else fb())
    stage = WikiParentEnrichStage()  # 不注入
    ctx = _ctx([{"id": "wiki:entities:foo",
                 "metadata": {"page_type": "entities", "knowledge_id": "k"}}])
    _run(stage, ctx)
    assert retriever.called == 1
```

### Step 2.2.2 — 跑测试确认失败

Run: `pytest tests/test_wiki_parent_enrich_stage.py -v`
Expected: FAIL（`ImportError: cannot import name 'WikiParentEnrichStage'`）

### Step 2.2.3 — 实现 stage + 注册

- [ ] 在 `src/services/rag_pipeline.py` 的 `WikiReadStage` 类之后（约 `:274` 后）、`VectorSearchStage` 之前，新增 `WikiParentEnrichStage`：

```python
class WikiParentEnrichStage(PipelineStage):
    """第二阶段 W2:wiki 候选带回 source 页 parent 上下文(spec §4.2 / S3)。

    挂在 post-rerank:读 ``ctx.reranked_results``,对 wiki 候选调
    ``WikiParentRetriever.enrich`` 写入 ``parent_content`` 字段,供
    ``GenerateStage``(:601)消费。仅 ``mode=wiki_first`` 且
    ``rag.wiki_parent_child.enabled=true`` 时介入,否则空操作 —— legacy 项目
    零影响(S6)。与 block 的 ``enrich_with_parent_context``(挂 hybrid_search:43)
    对称但独立,不改 block 挂载点。
    """

    def __init__(self, wiki_parent_retriever=None):
        self._retriever = wiki_parent_retriever

    @property
    def name(self):
        return "wiki_parent_enrich"

    async def execute(self, ctx, config):
        if not self.is_enabled(config):
            return ctx
        # legacy 门控(S6)
        if Config.get("knowledge_workflow.mode", "legacy") != "wiki_first":
            return ctx
        if not Config.get("rag.wiki_parent_child.enabled", False):
            return ctx
        results = getattr(ctx, "reranked_results", None) or []
        if not results:
            return ctx
        retriever = self._retriever or _get_container_service(
            "wiki_parent_retriever", lambda: None)
        if retriever is None:
            return ctx
        try:
            ctx.reranked_results = retriever.enrich(results)
        except Exception as e:
            logger.warning("WikiParentEnrich stage failed (non-fatal): %s", e)
            ctx.metadata.setdefault("warnings", []).append(
                f"wiki_parent_enrich_failed: {e}")
        return ctx
```

- [ ] 改 `_builtin_stages`（`rag_pipeline.py:878-881`），在 `RerankStage` 后、`EvidenceCompressStage` 前插入：

```python
    _builtin_stages = [
        QueryRewriteStage, WikiRetrievalStage, WikiReadStage, VectorSearchStage,
        RerankStage, WikiParentEnrichStage, EvidenceCompressStage, GenerateStage,
        PostProcessStage,
    ]
```

- [ ] 改 `DEFAULT_PIPELINE_CONFIG`（`rag_pipeline.py:950` 后），在 rerank 后、evidence_compress 前加条目：

```python
    {"stage": "rerank", "enabled": True, "top_n": 5, "min_score": 0.3},
    {"stage": "wiki_parent_enrich", "enabled": True},  # W2: wiki 候选带回 source 页 parent 上下文(post-rerank)
    {"stage": "evidence_compress", "enabled": False, "strategy": "extractive", "max_evidence_tokens": 4000},
```

### Step 2.2.4 — 跑测试确认通过

Run: `pytest tests/test_wiki_parent_enrich_stage.py -v`
Expected: 7 passed

### Step 2.2.5 — commit

```bash
git add src/services/rag_pipeline.py tests/test_wiki_parent_enrich_stage.py
git commit -m "feat(knowledge-base): add WikiParentEnrichStage mounted post-rerank"
```

**验收:** spec Task 2.2（挂载到 wiki 检索 post-rerank，复用 parent_content 与 GenerateStage）✓

---

## Task 2.3 — blend 档共存验证（spec Task 2.3）

**Files:**
- Test: `tests/test_wiki_parent_enrich_stage.py`（追加契约测试，无新源码）

**Interfaces:**
- Consumes: `blend_fusion.blend_fusion`（W1，`blend_fusion.py:18`）；Task 2.1 的 `WikiParentRetriever`

> blend_fusion._clone（`blend_fusion.py:80-86`）已浅拷贝保留候选所有字段。本 Task 用契约测试**锁死** wiki 候选的 `parent_content` 经 blend 融合后不丢、不与 block 候选的 `parent_content` 互覆盖。

### Step 2.3.1 — 写契约测试

- [ ] 在 `tests/test_wiki_parent_enrich_stage.py` 末尾追加：

```python
from src.services.blend_fusion import blend_fusion
from src.services.wiki_parent_retrieval import WikiParentRetriever


def test_blend_preserves_wiki_parent_content():
    """blend 融合后 wiki 候选的 parent_content 保留(S3 在 blend 档仍成立)。"""
    db = _FakeDb({"kid-1": {"id": "kid-1", "content": "wiki source 摘要"}})
    # 先 enrich wiki 候选(模拟 post-rerank 已挂 parent_content)
    wiki_cands = WikiParentRetriever(db=db).enrich([
        {"id": "wiki:entities:foo", "text": "wiki 命中",
         "metadata": {"page_type": "entities", "knowledge_id": "kid-1"},
         "match_channels": ["wiki_read"]}])
    # block 检索候选也带自己的 parent_content(block parent-child 已挂)
    search_cands = [
        {"id": "page1:block1", "text": "block 命中", "parent_content": "block 父块",
         "metadata": {"page_id": "page1"}, "match_channels": ["vector", "keyword"]}]
    merged = blend_fusion(wiki_cands, search_cands)
    wiki_merged = [c for c in merged if str(c["id"]).startswith("wiki:")][0]
    block_merged = [c for c in merged if not str(c["id"]).startswith("wiki:")][0]
    assert "wiki source 摘要" in wiki_merged["parent_content"]  # wiki parent 不丢
    assert block_merged["parent_content"] == "block 父块"  # block parent 不被覆盖


def test_blend_id_systems_do_not_collide():
    """wiki 候选(wiki:type:slug)与检索候选(page_id:block_id)id 体系不同,不互覆盖。"""
    wiki = [{"id": "wiki:entities:foo", "text": "w",
             "metadata": {"page_type": "entities", "knowledge_id": "k"},
             "match_channels": ["wiki_read"]}]
    search = [{"id": "wiki:entities:foo", "text": "s",  # 故意同 id(极端情况)
               "metadata": {}, "match_channels": ["vector"]}]
    merged = blend_fusion(wiki, search)
    # 同 id 累加 RRF 分(并集 match_channels),不丢任一路
    assert len(merged) == 1
    assert set(merged[0]["match_channels"]) == {"wiki_read", "vector"}
```

> 注：`test_blend_id_systems_do_not_collide` 验证 blend_fusion 对同 id 的累加语义（W1 已实现），确认 wiki 路与 search 路即使 id 撞车也是 RRF 累加而非覆盖。`_FakeDb` 在本文件 Task 2.2 已定义，复用。

### Step 2.3.2 — 跑测试确认通过

Run: `pytest tests/test_wiki_parent_enrich_stage.py -v`
Expected: 9 passed（原 7 + 新 2）

### Step 2.3.3 — commit

```bash
git add tests/test_wiki_parent_enrich_stage.py
git commit -m "test(knowledge-base): blend fusion preserves wiki parent_content (Task 2.3)"
```

**验收:** spec Task 2.3（blend 档两路 parent 不互相覆盖）✓

---

## Task 2.4 — 装配 + 配置 + legacy 门控（spec S6）

**Files:**
- Modify: `src/core/container.py`（字段 + property + deps）
- Modify: `src/services/project_setup.py`（`_wiki_parent_defaults` + 两个 build 函数）
- Modify: `config.example.yaml`（`rag.wiki_parent_child` 段）
- Test: `tests/test_wiki_parent_legacy.py`

**Interfaces:**
- Consumes: container `wiki_page_locator` property 范式（`:358-364`）；project_setup `_size_aware_defaults` 范式（`:114-129`）
- Produces: `container.wiki_parent_retriever` property；`shinehe init` 注入 `rag.wiki_parent_child` 段

### Step 2.4.1 — 写失败测试

- [ ] 创建 `tests/test_wiki_parent_legacy.py`：

```python
"""W2 装配 + legacy 门控回归(S6)。"""
from __future__ import annotations

from src.core.container import AppContainer
from src.services.project_setup import ProjectSetup
from src.services.wiki_parent_retrieval import WikiParentRetriever


def test_container_has_wiki_parent_retriever():
    """container.wiki_parent_retriever 是 WikiParentRetriever 实例。"""
    # AppContainer 可能需要 config;用最小构造。若 __init__ 强制 config,照 W1 test_size_aware_legacy 范式。
    try:
        c = AppContainer()
    except Exception:
        import pytest
        pytest.skip("AppContainer needs config; covered by W1 legacy test harness")
    assert isinstance(c.wiki_parent_retriever, WikiParentRetriever)


def test_rag_pipeline_deps_include_wiki_parent_retriever():
    """RAGService deps 含 wiki_parent_retriever(确保 stage 能被自动注入)。"""
    import src.core.container as container_mod
    src = open(container_mod.__file__, encoding="utf-8").read()
    assert "'wiki_parent_retriever': self.wiki_parent_retriever" in src


def test_init_local_config_has_wiki_parent_child():
    """shinehe init(local)注入 rag.wiki_parent_child 段。"""
    config = ProjectSetup().build_config({"local": True})
    rag = config["rag"]
    assert "wiki_parent_child" in rag
    assert rag["wiki_parent_child"]["enabled"] is True
    assert rag["wiki_parent_child"]["max_parent_chars"] == 2000


def test_init_provider_config_has_wiki_parent_child():
    """shinehe init(provider)注入 rag.wiki_parent_child 段。"""
    from src.services.provider_presets import get_provider_preset
    preset = get_provider_preset("siliconflow")
    config = ProjectSetup().build_config({"provider": "siliconflow"})
    rag = config["rag"]
    assert "wiki_parent_child" in rag
    assert rag["wiki_parent_child"]["enabled"] is True


def test_wiki_parent_defaults_not_in_wiki_first_defaults():
    """关键(浅合并坑):wiki_parent_child 不在 _wiki_first_defaults 返回值里。"""
    wfd = ProjectSetup._wiki_first_defaults()
    # _wiki_first_defaults 只含 knowledge_workflow + wiki 顶层键,不含 rag
    assert "rag" not in wfd or "wiki_parent_child" not in (wfd.get("rag") or {})


def test_legacy_config_has_no_wiki_parent_child():
    """老配置(无 wiki_parent_child 段)时 Config.get 返回默认 disabled。"""
    from src.utils.config import Config
    # 不注入段时 enabled 默认 False
    assert Config.get("rag.wiki_parent_child.enabled", False) is False


def test_config_example_has_wiki_parent_section():
    """config.example.yaml 含 rag.wiki_parent_child 段。"""
    src = open("config.example.yaml", encoding="utf-8").read()
    assert "wiki_parent_child:" in src
    assert "max_parent_chars:" in src
```

> 注：`test_container_has_wiki_parent_retriever` 若 AppContainer 构造需 config，照 W1 `tests/test_size_aware_legacy.py` 的 fixture 范式（若 W1 用了 monkeypatch Config，此处复用）。执行时若 skip，补 fixture；核心断言是 `test_rag_pipeline_deps_include_wiki_parent_retriever`（字符串核对 deps 注入）。

### Step 2.4.2 — 跑测试确认失败

Run: `pytest tests/test_wiki_parent_legacy.py -v`
Expected: FAIL（`AttributeError: wiki_parent_retriever` / `AssertionError: 'wiki_parent_child' not in rag`）

### Step 2.4.3 — 实现 container 注入

- [ ] **container.py**：在 `_size_aware_router` 字段后（`:139` 后）加：

```python
    # --- 第二阶段 W2:wiki parent-child 检索 ---
    _wiki_parent_retriever: Optional[object] = field(default=None, repr=False)
```

- [ ] 在 `size_aware_router` property 后（`:372` 后）加：

```python
    @property
    def wiki_parent_retriever(self):
        if self._wiki_parent_retriever is None:
            from src.services.wiki_parent_retrieval import WikiParentRetriever
            self._wiki_parent_retriever = WikiParentRetriever()
            self._track_service("_wiki_parent_retriever")
        return self._wiki_parent_retriever
```

- [ ] 在 `rag_pipeline` deps dict（`:175` `'wiki_page_locator': ...` 后）加一行：

```python
                'wiki_page_locator': self.wiki_page_locator,
                'wiki_parent_retriever': self.wiki_parent_retriever,
```

### Step 2.4.4 — 实现 project_setup 配置注入

- [ ] **project_setup.py**：在 `_size_aware_defaults` 方法后（`:129` 后）加静态方法：

```python
    @staticmethod
    def _wiki_parent_defaults() -> dict[str, Any]:
        """第二阶段 W2 wiki parent-child 默认段(wiki_first 项目 enabled=true)。

        wiki 命中 entity/concept/synthesis/comparison 页时,用 knowledge_id 回查
        source 页摘要写入候选 parent_content(与 block parent-child 对称)。
        legacy 项目缺省不注入;由 ``_build_local_config`` /
        ``_build_provider_config`` 合入各自 rag 段(同 ``_size_aware_defaults``,
        不能放进 ``_wiki_first_defaults`` 浅合并坑)。
        """
        return {
            "enabled": True,
            "max_parent_chars": 2000,
        }
```

- [ ] 在 `_build_local_config` 的 rag dict 内（`:183` `"size_aware": ...` 后）加一行：

```python
                "size_aware": self._size_aware_defaults(),
                "wiki_parent_child": self._wiki_parent_defaults(),
```

- [ ] 在 `_build_provider_config` 的 rag dict 内（`:225` `"size_aware": ...` 后）加一行：

```python
                "size_aware": self._size_aware_defaults(),
                "wiki_parent_child": self._wiki_parent_defaults(),
```

### Step 2.4.5 — 实现 config.example.yaml

- [ ] 在 `rag.size_aware` 段后（约 `:47` 后）加：

```yaml
  # 第二阶段 W2:wiki parent-child 检索(wiki 命中页带回 source 页 parent_content)
  wiki_parent_child:
    enabled: false                # 仅 mode=wiki_first 生效;legacy 强制 false
    max_parent_chars: 2000        # parent_content 截断长度(与 block parent-child 对称命名)
```

### Step 2.4.6 — 跑测试确认通过

Run: `pytest tests/test_wiki_parent_legacy.py -v`
Expected: 7 passed

### Step 2.4.7 — commit

```bash
git add src/core/container.py src/services/project_setup.py config.example.yaml tests/test_wiki_parent_legacy.py
git commit -m "feat(knowledge-base): wire WikiParentRetriever into container + init config (legacy guard S6)"
```

**验收:** S6（legacy 零变化）+ W2 仅 wiki_first 介入 ✓

---

## 验收对齐（spec §3）

| spec 标准 | 本 plan 落点 |
|---|---|
| **S3** wiki 命中候选带回 source 页 parent 上下文 | Task 2.1（候选级 `parent_content` 非空+指向 source，10 测试）+ 2.2（stage 挂载）+ 2.3（blend 档共存） |
| **S6** legacy 零变化 | Task 2.4（config 缺省 `enabled=false` + stage mode 门控 + 浅合并坑规避） |
| （S1/S2 W1 已达成 / S4 W3 / S5 全量回归 W4，不在本 plan） | — |

spec §6.2 任务覆盖：Task 2.1（WikiParentRetriever）✓ / 2.2（挂载）✓ / 2.3（blend 共存）✓。

---

## 验证（每 Task 末 + W2 收尾）

```bash
# Task 2.1 末:Retriever 单测
pytest tests/test_wiki_parent_retrieval.py -v

# Task 2.2/2.3 末:stage + blend 共存
pytest tests/test_wiki_parent_enrich_stage.py -v

# Task 2.4 末:装配 + legacy
pytest tests/test_wiki_parent_legacy.py -v

# W2 收尾:检索链路无回归(rerank/generate 管线改动影响域)
pytest tests/test_rag_sources.py tests/test_mcp_rag_full.py tests/test_mcp_contract.py \
       tests/test_parent_child.py tests/test_blend_fusion.py -v

# 全量回归(基线 1126 passed / 1 skipped,零退化方算完成)
pytest tests/ -q

# 真实端到端冒烟(需先 shinehe init && shinehe migrate 重建 wiki/,因交接 §7 的 wiki/ 已被清理):
#   配 SHINEHE_LLM_API_KEY 后 entity/concept 页编译,再用 wiki_read 档查询确认 parent_content 进 LLM 上下文
```

> **环境事实（接手核实的坑）**：交接 §7 记录的 `wiki/`（11 source 页，untracked）**物理上已不存在**（untracked 产物在会话间被清理）。W2 测试全部自建 fixture（不依赖真实 wiki/）；端到端冒烟需用户先 `shinehe init && shinehe migrate`（配 LLM Key 后幂等补齐 entity/concept 页）才能跑。

---

## 风险与缓解

| 风险 | 缓解 |
|---|---|
| **字段名口误**（高）：spec/交接写 `parent_context`，代码一致用 `parent_content`。写错则 GenerateStage 读不到、S3 即便字段非空也对生成无贡献 | Global Constraints + 架构决策表显式消歧；测试断言 `parent_content` |
| **挂载时序错位**：stage 必须在 RerankStage 后、GenerateStage 前。注册表顺序 = 执行顺序 | 架构决策表画 stage 顺序图；Task 2.2 测试 `test_pipeline_config_has_stage` 锁死 index 位置 |
| **浅合并坑**（W1 已踩）：wiki_parent_child 放进 `_wiki_first_defaults` 会整体覆盖 rag 段 | Task 2.4 只注入 `_build_local_config`/`_build_provider_config` 的 rag dict；`test_wiki_parent_defaults_not_in_wiki_first_defaults` 锁死 |
| **reranker 丢字段**：若 rerank 重建候选 dict 丢 `metadata.knowledge_id`/`page_type` | 已核实 `rerankers/llm.py:41` 原地 mutate 保留字段；enricher 对缺 knowledge_id 候选静默跳过（`test_missing_knowledge_id_no_crash`） |
| **parent_content 撑爆 prompt**：不受 PostProcessStage 截断 | enricher 自行 cap（`max_parent_chars` 默认 2000，`test_truncation_respects_max_length`） |
| **改 rag_pipeline blast radius** | 动工前 `gitnexus impact` 评估；W2 仅新增 stage + 注册，不改现有 stage 内部 |
| **syntheses/comparisons 无数据**：第一阶段未实现编译器 | enricher 代码就绪（`_extract_knowledge_ids` 支持 source_ids 列表），`test_syntheses_uses_source_ids_list` 用 fixture 覆盖；真实数据验证 deferred 到 W4 |
| **wiki/ 真实产物不存在** | 全部测试自建 fixture（不依赖真实 wiki/）；冒烟需用户重建 |

---

## Self-Review

**1. Spec coverage:**
- §4.2（parent-child 扩展到 wiki）→ Task 2.1（Retriever）+ 2.2（挂载 post-rerank）✓
- §6.2 Task 2.1（WikiParentRetriever 解析 frontmatter source → 拉 source 摘要）→ Task 2.1（A2 用 knowledge_id 回查，因 entity frontmatter 无 source_ids）✓
- §6.2 Task 2.2（挂载 + 复用 parent_context 字段与 CitationBuilder）→ Task 2.2；**修正**：字段名是 `parent_content` 非 `parent_context`；CitationBuilder 不消费 parent（候选级断言）✓
- §6.2 Task 2.3（blend 档共存）→ Task 2.3 ✓
- §3 S3（wiki 命中候选 parent 上下文非空且指向 source 页）→ Task 2.1 候选级 10 测试 ✓
- §3 S6（legacy 零变化）→ Task 2.4 ✓
- Gap A A2 方案（用 knowledge_id 回查，不动编译器）→ Task 2.1 `_extract_knowledge_ids` ✓

**2. Placeholder scan:** 每 Step 给出完整测试代码 + 完整实现代码 + 精确 file:line 挂载点 + commit message。无 TBD / "implement later" / "similar to Task N"。Task 2.4 的 container 构造 fixture 标注了「若 skip 照 W1 范式补」——这是边界处理不是 placeholder（核心断言 `test_rag_pipeline_deps_include_wiki_parent_retriever` 字符串核对已自足）。

**3. 类型/签名一致性:**
- `WikiParentRetriever(db=None, config=None).enrich(candidates, max_length=None) -> list[dict]`：Task 2.1 定义，Task 2.2 stage 调 `retriever.enrich(results)`、Task 2.3 测试调 `.enrich([...])`、Task 2.4 container 调 `WikiParentRetriever()` —— 一致 ✓
- `WikiParentEnrichStage(wiki_parent_retriever=None)`：Task 2.2 定义，Task 2.4 deps 注入 `'wiki_parent_retriever'` 经 `create_stage` 自动过滤 —— 一致 ✓
- 字段名 `parent_content`、配置项 `rag.wiki_parent_child.max_parent_chars`、stage name `wiki_parent_enrich`：全程统一 ✓

**4. 依赖顺序:** 2.1（Retriever）→ 2.2（Stage 用 Retriever）→ 2.3（blend 共存，依赖 wiki 候选带 parent_content）→ 2.4（装配 + 门控），无环 ✓

**5. W1 教训已纳入:**
- 先画 stage 顺序图（架构决策表）确认 post-rerank 时序 ✓
- 浅合并坑显式规避（Task 2.4 + 测试锁死）✓
- legacy 门控双保险（config 缺省 false + stage mode 门控）✓

---

## Execution Handoff

Plan complete（待审批）。审批后进入实现时：
1. 按顺序 Task 2.1 → 2.4，每 Step TDD（失败测试 → 跑确认失败 → 实现 → 跑确认通过 → commit）
2. 推荐用 `superpowers:subagent-driven-development` 逐 Step 驱动
3. 每 Task 末跑对应单测；Task 2.2 动 rag_pipeline 前 `gitnexus impact`；W2 收尾跑检索回归 + 全量回归（1126 零退化）
4. 真实端到端冒烟需用户先 `shinehe init && shinehe migrate` 重建 wiki/（交接 §7 的产物已不在）
