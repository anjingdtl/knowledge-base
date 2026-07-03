# 大规模升级分段 Review & Fix 实施计划

> **执行方式:** 自主推进,每完成一个 phase commit 一次,全部完成后全量回归。用户已授权 commit。

**目标:** 系统性 review 2026-06-23→2026-07-03 升级窗口(6 大功能流,59 文件 +7416/-281,21 新文件)引入的回归,定位并修复真实缺陷,零退化。

**基线:** `pytest tests/` = **1179 passed / 1 skipped / 0 failed**(已验证绿)。回归门禁 = 不低于此。

**方法论:** 3 个深度 Explore agent 已完成全段实现映射 + 文件:行号级 bug 线索。4 个微妙高风险点已读真实代码确认为真 bug。本计划按功能段分 phase,每条 fix 先读真实代码(ground truth)→ 写失败回归测试(TDD)→ 最小修复 → 该段测试通过 → commit。

---

## 已确认 Bug 清单(去重 + 严重度排序)

### Phase S1 — 检索执行层(rag_pipeline / hybrid_search / search_service / lexical_zh)

| # | 严重度 | bug | 位置 | 修复方向 |
|---|--------|-----|------|---------|
| S1.1 | 高 | blend_fusion 在外层 try 内:fusion 抖动抛异常 → 外层 except 清空 `ctx.candidates=[]`,而 FTS 兜底在它之前被跳过 → 成功跑完 hybrid 却丢全部候选无兜底 | `rag_pipeline.py:448-490` | 把 blend_fusion 单独包 try,失败时回退用 hybrid 候选(不丢) |
| S1.2 | 高 | LRU 缓存浅拷贝污染:`_rag_cache.put(question, result)` 存的就是返回原 dict,`return dict(result)` 只浅拷贝;调用方改 `result["sources"]` 嵌套结构直接污染缓存 → 同一 query 不同请求返回被污染答案 | `rag_pipeline.py:1294,1324,1326` | put/return/get 三处深拷贝(或缓存不可变快照) |
| S1.3 | 中 | `_run_coroutine_sync` 有运行中事件循环时,超时后 daemon 线程阻塞在 maxsize=1 的 `result_queue.put`,线程+queue 泄漏 | `rag_pipeline.py:41-80` | queue 改无界,或 put_nowait+丢弃(超时后结果已无关) |
| S1.4 | 中 | `query()` 非 TimeoutError 的任意异常仍 fallback `_direct_query`(二次调 LLM);50轮 Bug-2 只堵了 TimeoutError 雪崩 | `rag_pipeline.py:1335-1340` | 收窄 fallback 条件,或对 `_direct_query` 也加超时/次数限制 |
| S1.5 | 中 | `lexical_zh.expand_query` 用 `word in query` 子串匹配:"AI" 命中 "available",注入无关同义词污染 FTS 召回 | `lexical_zh.py:69-82` | 改词边界匹配(中英文分别处理) |
| S1.6 | 中低 | title boost:`score` 回退链末位是 `distance`(越小越好),`score+boost_ratio` 让低距离高分结果变"差",语义反转(低频:distance 仅在前三者全 None 时胜出) | `search_service.py:163-202` | distance 胜出时先归一化为相似度 `1-distance/2` 再 boost |
| S1.7 | 低/清理 | 死代码 `_normalize_fts_rank` 定义后从未调用(实际用 `models.retrieval.normalize_fts_score`);死配置 `temperature/max_tokens/top_k/rerank_top_n` 读后未用 | `hybrid_search.py:280-289` + `rag_pipeline.py:542-545` | 删除死代码/死配置 |
| S1.8 | 低/防御 | RRF 两权重都设 0 时 `total_w>0` 为 False,跳过归一化后两权重仍 0 → 所有 rrf_score=0,静默退化无 warning | `hybrid_search.py:182-185` | 加 warning 或回退默认权重 |

**S1 commit:** `fix(knowledge-base): harden retrieval pipeline (blend fallback, cache deep-copy, async bridge leak, lexical word-boundary)`

### Phase S2 — Wiki 编译 + 数据层 + 迁移

| # | 严重度 | bug | 位置 | 修复方向 |
|---|--------|-----|------|---------|
| S2.1 | 必现 | alembic i001 `op.create_table` 不带 IF NOT EXISTS,与 db.py `_SCHEMA` 的 `CREATE TABLE IF NOT EXISTS conflict_*` 双重定义 → 已跑过 app 的库执行 `alembic upgrade head` 直接报 table already exists | `alembic/versions/i001_version_conflict.py:26-75` + `db.py:454-498` | 迁移改 `if_not_exists=True` 或 `op.execute("CREATE TABLE IF NOT EXISTS...")` |
| S2.2 | 中高 | `resolve_slug` 对空 `source_hash` 判等(空==空)→ 覆盖不相关同名源页 | `wiki_slug.py:37-39` + `wiki_source_compiler.py:51` | 空 hash 时不走幂等覆盖分支,强制走冲突后缀 |
| S2.3 | 中高 | `wiki_entity_updater` 直接 `write_markdown(path)` 覆盖,不用 resolve_slug;多源写同名 entity → 最后写入胜出,历史 facts 丢失 | `wiki_entity_updater.py:75,146` | 复用 resolve_slug 冲突后缀机制 |
| S2.4 | 中 | `version_conflict.execute_delete` 只调 `vs.delete_by_knowledge`(清 vec_chunks),不清 vec_blocks → block 向量长期泄漏 | `version_conflict.py:605-608` | 删除时一并 `block_store.delete_by_knowledge` |
| S2.5 | 中 | migrator: `_handle_migrate` plan/apply 前未 `Config.load()`,`_ensure_db` 读到的 data_dir 可能与 create_container 后不一致;apply 备份先 rmtree 旧备份再 copytree(中途失败丢旧备份,不可逆) | `cli.py:236-251` + `migrator.py:32-35,76-78` | 所有分支开头 `Config.load()`;备份改 rename-then-copy |
| S2.6 | 中 | `write_markdown` 注释说"原子写入"实为直接 `write_text`,崩溃可能损坏文件 | `wiki_slug.py:62-68` | 改 tmp+os.replace 原子写 |

**S2 commit:** `fix(knowledge-base): repair wiki compilation + migration safety (alembic idempotency, slug/hash, atomic write, vec cleanup)`

### Phase S3 — MCP Server + 工具契约

| # | 严重度 | bug | 位置 | 修复方向 |
|---|--------|-----|------|---------|
| S3.1 | 中高 | `ask_with_query` 新建 `RagPipeline(deps={db,llm,query_rewriter,reranker,hybrid_search})` 丢弃 `graph_backend/size_aware_router/wiki_page_locator/wiki_parent_retriever` → 规模自适应路由 + wiki parent-child(本次升级核心功能)在 ask_with_query 静默失效,即便项目已启用 | `mcp_server.py:2592-2602` | 复用 `container.rag_pipeline` 或传入完整 deps |
| S3.2 | 中高 | `ask` 工具无顶层 `except Exception`(`ask_with_query` 有),非超时异常直接冒泡成未处理 MCP 错误;与 50轮 Bug-2 "雪崩"同类的不对称韧性 | `mcp_server.py:588-640` | 加与 ask_with_query 一致的全面异常捕获+部分结果返回 |
| S3.3 | 中 | `_get_operation_log_service` 调 `get_container()` 缺必需 `request` 参数 → 每次必 TypeError → 永远走 except fallback;容器注入路径是死代码 | `version_conflict.py:131-132` | 改 `from src.core.container import get_active_container` |

**S3 commit:** `fix(knowledge-base): unify MCP ask pipeline deps + exception handling + operation_log DI`

### Phase S4 — 安全 + API/GUI

| # | 严重度 | bug | 位置 | 修复方向 |
|---|--------|-----|------|---------|
| S4.1 | 高/安全 | `parse_url` SSRF 检查只验初始 URL 主机,`follow_redirects=True` 的重定向目标不做 IP 校验 → 恶意 302 指向 `127.0.0.1`/云元数据 `169.254.169.254` 绕过;且有 DNS 重绑定 TOCTOU | `file_parser.py:806-874` | 禁用 follow_redirects 改手动跟随+逐跳重验,或加 httpx event_hooks 校验每跳目标 |
| S4.2 | 中低 | `MainWindow.closeEvent` 不向子视图传播,不等待/退出运行中的 QThread(ScanWorker/JudgeWorker/DedupWorker)→ 线程运行中被销毁,会话可能卡在 scanning/judging 无恢复路径 | `gui/main_window.py:56-64` + `gui/maintenance_view.py` | closeEvent 请求子视图退出 worker |

**S4 commit:** `fix(knowledge-base): close SSRF redirect bypass + GUI worker shutdown`

### Phase S5 — 全量回归

- `pytest tests/` ≥ 1179 passed / 0 failed
- `ruff check src tests evals tools scripts` 全绿
- `mypy src` 无错误
- 关键端到端检索冒烟(wiki_read/blend/full_search 三档 + ask/search)
- 更新 `PROGRESS.md` 记录本轮 review

**S5 commit:** `docs(knowledge-base): record upgrade-regression review & fixes`

---

## 明确延迟(不在本轮范围,附理由)

| 项 | 理由 |
|----|------|
| 双轨 wiki 编译(MCP→SQLite wiki_compiler vs path_indexer→文件系统 knowledge_workflow) | 架构级 gap,PROGRESS 已记为 Phase 2 W4 Gap B(文件系统 wiki lint/统计基础设施),属功能扩展非回归 bug |
| WikiIndexCompiler/LogCompiler 全量扫描性能 | 性能优化,W4 测量基础设施范围 |
| Database/BlockStore 双单例陈旧缓存 | 触发条件需更深入复现(GUI 切库/migrate 跨库);若 S5 回归暴露再补 |
| 硬编码 0.3 min_score 三处重复 | 重构 nice-to-have,非 bug |
| evidence_compress abstractive 丢 parent_content | 边缘(abstractive 非默认) |
| 浅合并坑 | 三 agent 一致确认**已正确规避 3 次**,非 bug |

---

## 验证标准

- 每条 fix 附回归测试(TDD:先失败后通过),锁死防退化
- 每 phase commit 前该段测试全绿
- S5 全量门禁达标方可结束
- `gitnexus detect_changes` 抽验影响范围与预期一致(可选)
