# Knowledge-Base 双轨 Wiki 轻量收敛设计

- **状态**：待实施（2026-07-07，用户授权自主推进）
- **日期**：2026-07-07
- **范围**：双轨 wiki 编译（MCP→SQLite vs path_indexer→文件系统）的**轻量收敛**——收敛双写点 + 统一 frontmatter `source_ids` + A 轨 SQLite wiki 接入 RAG 主链路（浅 fallback）。保留两轨协作，不动主键/workflow/links（留待完整迁移）。
- **上游依据**：
  - spec Gap B（phase2 §6.4 前置）：W4 已完成文件系统 lint 测量层（`wiki_fs_lint`），未合并两轨
  - Agent 2 双轨 wiki 编译代码现状报告（2026-07-07，10+ 模块核实）
- **适用版本**：ShineHeKnowledge v1.5.1 → v1.5.2

## 1. 背景与动机

W4 收口时双轨 wiki 编译的分离记为 Phase 3 候选技术债。两轨：

- **A 轨**（MCP→SQLite `WikiCompiler`）：concept-centric（LLM 抽取的概念图谱）+ Q&A，存 `wiki_pages` 表
- **B 轨**（path_indexer→文件系统 `KnowledgeWorkflowService`）：source-centric（文档摘要 + 实体索引），存 `wiki/*.md`

两轨同源（`knowledge_items`）但产出互补。当前三个问题：

1. **A 轨断层**：只有 B 轨接入 RAG 主检索链路（SizeAwareRouter + WikiReadStage）；A 轨 SQLite `wiki_pages` 只服务 GUI 浏览/lint/工作流审计，**没进 ask**——「只生产不消费」孤岛。
2. **双写散落**：`save_to_wiki`（mcp_server.py:1780-1791）+ `rag_pipeline._try_auto_save_wiki`（:1064-1076）两处独立实现 A.`save_answer` + B.`save_query` 双写，逻辑重复 + 容错策略不一致（前者 `logger.warning`，后者 `except: pass`）。
3. **frontmatter 异构**：sources 页用 `knowledge_id`，comparisons/syntheses 用 `source_ids`，entities/concepts 无溯源——消费者（WikiParentRetriever/WikiFsLint）需特殊处理每种 page_type。

本设计轻量收敛这三个问题，**不碰**完整迁移的高风险障碍（主键/workflow/links）。

## 2. 现状（Agent 2 源码核实）

### 2.1 双写交叉点（2 处）

**save_to_wiki**（mcp_server.py:1778-1798）：
```python
compiler = container.wiki_compiler
page_id = compiler.save_answer(question, answer, source_ids, auto_publish=..., enhance=...)
try:
    container.knowledge_workflow.save_query(
        question, answer, source_ids, confidence=..., save_mode=..., timestamp="")
except Exception as _e:
    logger.warning("save_to_wiki filesystem saveback failed: %s", _e)
```

**_try_auto_save_wiki**（rag_pipeline.py:1049-1081）：同样 `save_answer` + `save_query` 双写，B 轨 `except: pass`（静默吞错）。

### 2.2 WikiReadStage（rag_pipeline.py:242-276）

`scale in ("wiki_read","blend")` 时调 `locator.locate(query)`，`cands` 非空才设 `ctx.candidates`。**`cands` 为空时无 fallback**——A 轨 SQLite 内容检索不到。

### 2.3 frontmatter 溯源字段（异构）

| page_type | 溯源字段 |
|---|---|
| sources | `knowledge_id`（无 `source_ids`）|
| entities/concepts | `knowledge_id`（无 `source_ids`）|
| comparisons/syntheses | `source_ids`（list）|

## 3. 设计（4 组件）

### 3.1 WikiWriteService（收敛双写）

**新建** `src/services/wiki_write_service.py`：

```python
class WikiWriteService:
    def __init__(self, wiki_compiler, knowledge_workflow):
        self._compiler = wiki_compiler
        self._workflow = knowledge_workflow

    def save(self, question, answer, source_ids, *,
             confidence=0.0, save_mode="manual",
             auto_publish=None, enhance=True, timestamp="") -> dict:
        """统一双写入口:A(SQLite save_answer)+ B(FS save_query)。
        任一失败不阻塞另一个(统一容错:warning + 记录 errors)。"""
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

**收敛点**：
- `mcp_server.save_to_wiki`：双写段（:1780-1791）改调 `container.wiki_write_service.save(...)`
- `rag_pipeline._try_auto_save_wiki`：双写段（:1064-1076）改调 `WikiWriteService`（经 container 取）

**AppContainer**：新增 `wiki_write_service` 属性（lazy 构造，依赖 `wiki_compiler` + `knowledge_workflow`）。

### 3.2 统一 frontmatter source_ids

**改动**：
- `WikiSourceCompiler.compile()`：sources 页 frontmatter 加 `source_ids: [<knowledge_id>]`
- `WikiEntityUpdater.update()`：entities/concepts 页 frontmatter 加 `source_ids: [<knowledge_id>]`
- comparisons/syntheses：已有 `source_ids`，不变

**消费者统一读**（向后兼容 helper）：
```python
def resolve_source_ids(fm: dict) -> list[str]:
    """统一读 source_ids;旧文件无该字段时 fallback knowledge_id。"""
    sids = fm.get("source_ids")
    if sids:
        return sids if isinstance(sids, list) else [sids]
    kid = fm.get("knowledge_id")
    return [kid] if kid else []
```

- `WikiParentRetriever` / `WikiFsLint._check_provenance` 改用 `resolve_source_ids(fm)`
- 旧文件无 `source_ids` 时 fallback `knowledge_id`，零破坏

### 3.3 WikiReadStage SQLite fallback（浅）

**改动** `WikiReadStage.execute`（rag_pipeline.py:269-272）：
```python
if scale in ("wiki_read", "blend"):
    cands, _ = locator.locate(ctx.question)
    if not cands and Config.get("rag.wiki_read.sqlite_fallback", True):
        cands = self._sqlite_fallback(ctx.question)
    if cands:
        ctx.candidates = cands
```

**新增** `_sqlite_fallback(query, top_n=10)`：
```python
def _sqlite_fallback(self, query, top_n=10):
    """FS 无命中时查 SQLite search_wiki_fts,转 wiki 候选 schema。"""
    try:
        rows = Database.search_wiki_fts(query, limit=top_n)
    except Exception as e:
        logger.warning("sqlite wiki fallback failed: %s", e)
        return []
    out = []
    for r in rows:
        # SQLite wiki_pages.source_ids 是 JSON string(如 '["k1","k2"]'),需 json.loads;
        # 区别于 3.2 的 resolve_source_ids(读 FM 的 list/单值)
        out.append({
            "id": f"wiki:sqlite:{r['id']}",
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

**门控**：`rag.wiki_read.sqlite_fallback` 默认 true，仅 `mode=wiki_first` 生效（WikiReadStage 已门控 mode）。legacy 零影响（S6）。

### 3.4 配置 + 文档

- `config.example.yaml`：`rag.wiki_read: {sqlite_fallback: true}`
- `project_setup._build_local_config/_build_provider_config`：rag 段加 `wiki_read: {sqlite_fallback: true}`
- `docs/advanced-features.md`：加「双轨 wiki 协作」章节
- `tests/test_docs_consistency.py`：加 `wiki_read` 配置键一致性断言

## 4. 验收标准（DoD）

- [ ] `WikiWriteService` 新模块 + 单测（双写 + 容错：A/B 任一失败不阻塞）
- [ ] `save_to_wiki` + `_try_auto_save_wiki` 改调 `WikiWriteService`（行为等价：A+B 都写，容错策略统一为 warning + errors 记录）
- [ ] frontmatter `source_ids` 统一（sources/entities/concepts 有 source_ids）+ 向后兼容测试（旧文件 fallback knowledge_id）
- [ ] `WikiReadStage` SQLite fallback（FS 无命中时查 SQLite）+ 单测
- [ ] 集成：A 轨 concept 页能被 wiki_read 档检索到（端到端）
- [ ] config.example + project_setup + advanced-features + docs consistency
- [ ] ruff/mypy 0 错误，全量 pytest 绿（基线 1224，零退化）
- [ ] legacy 模式零影响（S6：sqlite_fallback 仅 wiki_first 生效）
- [ ] gitnexus impact：`save_to_wiki`/`_try_auto_save_wiki`/`WikiReadStage` 改动 blast radius 评估
- [ ] 版本 → v1.5.2

## 5. 风险与回滚

- **风险等级**：MEDIUM。`WikiWriteService` 新模块（低爆炸半径）；`WikiReadStage` 加 fallback（向后兼容，配置门控）；frontmatter 加字段（向后兼容）；收敛双写点（行为等价，需回归）。
- **GitNexus 规矩**：动 `save_to_wiki` / `_try_auto_save_wiki` / `WikiReadStage` 前 `gitnexus impact` 评估，HIGH/CRITICAL 记录（用户已授权自主推进）。
- **回滚**：所有改动可 `git revert`。`sqlite_fallback` 配置默认 true 但可关；frontmatter 加字段向后兼容；`WikiWriteService` 收敛保持行为等价。
- **关键回归点**：`save_to_wiki` / auto-save-wiki 的双写行为必须与改前等价（A+B 都写，容错统一）。

## 6. 非目标（YAGNI）

- ❌ 统一主键（`uuid4`↔路径式）—— 完整迁移范围
- ❌ workflow 状态机迁移 —— 完整迁移范围
- ❌ `wiki_links` 物化到 SQLite 缓存 —— 完整迁移范围
- ❌ A 轨 `WikiCompiler` 改写为 FS 编译器 —— 完整迁移范围
- ❌ 深融合（FS+SQLite RRF）—— 用户选浅 fallback
- ❌ A 轨内容同步导出到 FS `wiki/*.md` —— 「仅索引同步」方案，未选

## 7. 后续

- **完整迁移**（FS 为 SoT + SQLite 降级缓存/审计层）留待独立 spec，依赖本次轻量收敛铺路（`WikiWriteService` 作为统一写入入口，未来内部路由可改为只写 FS + 派生 SQLite 缓存）。
- 真实 wiki/ 端到端冒烟需用户 `shinehe init && shinehe migrate` + 配 LLM Key 编译 entity/concept 页。
