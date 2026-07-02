# knowledge-base Karpathy Wiki-First W3 实施计划(闭环阶段)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 闭环 —— 高价值 query 回写到 `wiki/syntheses/`、`wiki/comparisons/`(draft);wiki lint 检出 4 类问题;`shinehe wiki` CLI 子命令可用。

**Architecture:** W2 的文件系统 wiki 层已就位(source/entity/index/log)。W3 补"query → wiki"回写 + lint 增强 + CLI。复用 W2 的 `wiki_slug`/`WikiLogCompiler`,新增 `KnowledgeWorkflowService.save_query()` 写综合/对比页。

**Tech Stack:** Python 3.14、argparse(首次嵌套子命令)、pytest。

---

## 关键设计决策

1. **两套 wiki 产物仍分离**:现有 `save_to_wiki`→SQLite `wiki_pages`(manual 模式,向后兼容);W3 auto 回写→**文件系统** `wiki/syntheses|comparisons/*.md`(draft)。
2. **confidence 来源**:`ctx.sources[i]["score"]`(rerank→rrf→vector→distance 回退链,`rag_pipeline.py:560`),取 `max(sources.score)`。
3. **lint 4 类**:孤儿(W2 已有 `orphan`)/矛盾(已有 `contradiction`)/过时 claim(新增)/缺失 backlinks(新增)。
4. **CLI 首次嵌套子命令**:`shinehe wiki <lint|save-answer|ingest-source>`。

---

## Global Constraints

- Python `python`(非 `python3`);4 空格缩进;snake_case;`from __future__ import annotations`。
- 改现有符号前先 `gitnexus impact`;不破坏基线(1084 passed, 1 skipped)。
- 提交规范:`feat(knowledge-base): ...`。

---

## File Structure

| 文件 | 职责 | 状态 |
|------|------|------|
| `src/services/knowledge_workflow.py` | 加 `save_query()` 方法 | 修改 |
| `src/mcp_server.py` | `save_to_wiki` 加 `save_mode` 参数 | 修改 |
| `src/services/rag_pipeline.py` | `_try_auto_save_wiki` 加 confidence 门槛 + 调 save_query | 修改 |
| `src/services/wiki_lint.py` | 加 `outdated_claim` + `missing_backlinks` 两类检查 | 修改 |
| `src/cli.py` | 加 `wiki` 子命令组(嵌套) | 修改 |
| `tests/test_knowledge_workflow.py` | 扩展 save_query 测试 | 修改 |
| `tests/test_wiki_lint.py` | 加 2 类检查测试 | 修改/新建 |
| `tests/test_cli_wiki.py` | CLI wiki 子命令测试 | 新建 |

---

## Task 1: query 回写标准化(save_mode + confidence 门槛)

对应 spec §6.3 任务 3.1。**涉及现有符号,先 gitnexus impact。**

**Files:**
- Modify: `src/services/knowledge_workflow.py`(加 `save_query`)
- Modify: `src/mcp_server.py:1737-1743`(`save_to_wiki` 加 `save_mode`)
- Modify: `src/services/rag_pipeline.py:925-944`(`_try_auto_save_wiki` 加 confidence)
- Test: `tests/test_knowledge_workflow.py`(扩展)

**Interfaces:**
- Produces: `KnowledgeWorkflowService.save_query(question, answer, source_ids, confidence, page_type="syntheses", save_mode="manual", timestamp) -> dict`

- [ ] **Step 0: impact 评估**

```
impact({target: "save_to_wiki", direction: "upstream", repo: "ClaudeCodeWorkSpace"})
impact({target: "_try_auto_save_wiki", direction: "upstream", repo: "ClaudeCodeWorkSpace"})
```

- [ ] **Step 1: 写失败测试(扩展 test_knowledge_workflow.py)**

在 `tests/test_knowledge_workflow.py` 末尾追加:

```python
def test_save_query_writes_syntheses_draft(tmp_path, monkeypatch):
    """save_query 写文件系统 syntheses/*.md(draft)+ log。"""
    from src.services.knowledge_workflow import KnowledgeWorkflowService

    Config.set("knowledge_workflow.mode", "wiki_first")
    Config.set("knowledge_workflow.wiki_dir", str(tmp_path / "wiki"))
    Config.set("knowledge_workflow.synthesis_dir", str(tmp_path / "wiki" / "syntheses"))
    Config.set("knowledge_workflow.comparison_dir", str(tmp_path / "wiki" / "comparisons"))
    # log/index 用真实编译器
    svc = KnowledgeWorkflowService()
    result = svc.save_query(
        question="LLM 与传统搜索的区别?",
        answer="LLM 检索基于语义..." + "x" * 120,
        source_ids=["k1", "k2"],
        confidence=0.8,
        page_type="syntheses",
        save_mode="auto",
        timestamp="2026-07-02T11:00:00",
    )
    assert result["status"] == "saved"
    p = Path(result["path"])
    assert p.exists()
    fm = read_frontmatter(p)
    assert fm["status"] == "draft"
    assert fm["confidence"] == 0.8
    assert (tmp_path / "wiki" / "log.md").exists()


def test_save_query_auto_below_threshold_skips(tmp_path):
    """confidence < 0.6 + save_mode=auto → 跳过。"""
    from src.services.knowledge_workflow import KnowledgeWorkflowService

    Config.set("knowledge_workflow.mode", "wiki_first")
    Config.set("knowledge_workflow.wiki_dir", str(tmp_path / "wiki"))
    svc = KnowledgeWorkflowService()
    result = svc.save_query(
        question="q?", answer="short",
        source_ids=["k1"], confidence=0.3,
        save_mode="auto", timestamp="2026-07-02T11:00:00",
    )
    assert result["status"] == "skipped"
```

(需在文件顶部加 `from src.services.wiki_slug import read_frontmatter` import)

- [ ] **Step 2: 验证失败**

Run: `python -m pytest tests/test_knowledge_workflow.py::test_save_query_writes_syntheses_draft -v`
Expected: FAIL — `AttributeError: save_query`

- [ ] **Step 3: 实现 `save_query`**

在 `src/services/knowledge_workflow.py` 的 `KnowledgeWorkflowService` 类内(`compile` 方法之后)加:

```python
    def save_query(
        self,
        question: str,
        answer: str,
        source_ids: list[str] | None = None,
        confidence: float = 0.0,
        page_type: str = "syntheses",
        save_mode: str = "manual",
        timestamp: str | None = None,
    ) -> dict:
        """把高价值 query 回写为文件系统 wiki 页(comparisons/syntheses)。

        auto 模式按阈值(长度≥100 + confidence≥0.6 + source≥2)门控;
        manual 模式直接写。均写 draft 状态(走 review gate)。
        """
        mode = Config.get("knowledge_workflow.mode", "legacy")
        if mode != "wiki_first":
            return {"status": "skipped", "reason": f"mode={mode}"}

        min_len = int(Config.get("wiki.query_save_min_length", 100))
        if save_mode == "auto":
            if len(answer) < min_len or confidence < 0.6 or len(source_ids or []) < 2:
                return {"status": "skipped", "reason": "below_threshold"}

        wiki_dir = Config.get("knowledge_workflow.wiki_dir", "wiki")
        if page_type == "comparisons":
            target_dir = Path(
                Config.get("knowledge_workflow.comparison_dir", f"{wiki_dir}/comparisons")
            )
        else:
            target_dir = Path(
                Config.get("knowledge_workflow.synthesis_dir", f"{wiki_dir}/syntheses")
            )
        target_dir.mkdir(parents=True, exist_ok=True)

        ts = timestamp or ""
        slug, target = resolve_slug(target_dir, question, ts or "q")
        frontmatter = {
            "title": question[:120],
            "page_type": page_type,
            "status": "draft",
            "confidence": confidence,
            "source_ids": source_ids or [],
            "saved_at": ts,
            "save_mode": save_mode,
        }
        body = f"# {question[:120]}\n\n{answer}\n"
        write_markdown(target, frontmatter, body)

        # 追加 log
        try:
            self._log.append({
                "type": "query_save",
                "target": question[:60],
                "timestamp": ts,
                "detail": f"{page_type} confidence={confidence:.2f}",
            })
        except Exception as e:
            logger.warning("save_query log append failed: %s", e)

        return {"status": "saved", "path": str(target), "slug": slug}
```

(顶部 import 需加 `resolve_slug, write_markdown`:`from src.services.wiki_slug import resolve_slug, write_markdown`)

- [ ] **Step 4: 验证通过**

Run: `python -m pytest tests/test_knowledge_workflow.py -v`
Expected: PASS(8 tests)

- [ ] **Step 5: save_to_wiki MCP 工具加 save_mode**

Modify `src/mcp_server.py` 的 `save_to_wiki` 签名(`:1737-1743`),加参数:

```python
@_heartbeat
def save_to_wiki(
    question: str,
    answer: str,
    source_ids: list[str] | None = None,
    auto_publish: bool | None = None,
    enhance: bool = True,
    save_mode: str = "manual",
    confidence: float = 0.0,
) -> dict:
```

在函数体现有 `compiler.save_answer(...)` 之后(`:1764` 之后)追加文件系统回写:

```python
    # wiki-first 文件系统层回写(comparisons/syntheses)
    try:
        from src.core.container import get_active_container
        _c = get_active_container()
        if _c is not None:
            _c.knowledge_workflow.save_query(
                question, answer, source_ids, confidence=confidence,
                save_mode=save_mode,
                timestamp=__import__("datetime").datetime.now().isoformat(),
            )
    except Exception:
        pass  # 文件系统回写失败不影响主流程
```

> 说明:manual 模式仍走 SQLite `save_answer`(向后兼容);save_mode=auto 时 `save_query` 内部按阈值门控。SQLite 与文件系统双写,二者产物分离。

- [ ] **Step 6: rag_pipeline 加 confidence 门槛**

Modify `src/services/rag_pipeline.py` 的 `_try_auto_save_wiki`(`:925-944`),在现有 `if len(ctx.sources) < 2` 检查处合并 confidence:

```python
    def _try_auto_save_wiki(self, question: str, ctx: "RagContext"):
        """自动保存高质量回答到 Wiki(静默,不影响主流程)。"""
        critical_warnings = [w for w in ctx.metadata.get("warnings", [])
                             if "no sources" in w.lower() or "failed" in w.lower()]
        # confidence: 取最高 source score(rerank→rrf→vector→distance 回退链)
        confidence = max((s.get("score", 0.0) for s in ctx.sources), default=0.0)
        if len(ctx.sources) < 2 or critical_warnings or confidence < 0.6:
            return
        source_ids = [s.get("knowledge_id") for s in ctx.sources if s.get("knowledge_id")]
        # SQLite 层(现有)
        compiler = WikiCompiler()
        compiler.save_answer(question, ctx.answer, source_ids)
        # 文件系统层(wiki-first)
        try:
            from src.core.container import get_active_container
            _c = get_active_container()
            if _c is not None:
                _c.knowledge_workflow.save_query(
                    question, ctx.answer, source_ids, confidence=confidence,
                    save_mode="auto", timestamp=ctx.answer_created_at if hasattr(ctx, "answer_created_at") else "",
                )
        except Exception:
            pass
```

(若 `WikiCompiler` 在方法内才 import,保持原样;`compiler = WikiCompiler()` 行原已有则不重复)

- [ ] **Step 7: 验证 + commit**

Run: `python -m pytest tests/test_knowledge_workflow.py -v`
Expected: PASS

```bash
git add src/services/knowledge_workflow.py src/mcp_server.py src/services/rag_pipeline.py tests/test_knowledge_workflow.py
git commit -m "feat(knowledge-base): standardize query saveback (save_mode + confidence threshold)"
```

---

## Task 2: wiki_lint 增强(过时 claim + 缺失 backlinks)

对应 spec §6.3 任务 3.2。**涉及 `WikiLint.run`,先 impact。**

**Files:**
- Modify: `src/services/wiki_lint.py`(`run()` 内加 2 类检查)
- Test: `tests/test_wiki_lint.py`

**Interfaces:**
- Consumes: `Database.get_knowledge_batch(source_ids) -> dict[str,dict]`(`db.py:865`);`Database.get_backlinks(page_id) -> list[dict]`(`db.py:2074`)
- Produces: `LintFinding(category="outdated_claim"|"missing_backlinks", ...)`

- [ ] **Step 0: impact**

```
impact({target: "run", direction: "upstream", repo: "ClaudeCodeWorkSpace", file_path: "projects/knowledge-base/src/services/wiki_lint.py", kind: "Method"})
```

- [ ] **Step 1: 写失败测试**

`tests/test_wiki_lint.py`(新建,若已存在则追加):

```python
"""wiki_lint 增强 4 类检查测试。"""
import json

import pytest

from src.services.db import Database
from src.services.wiki_lint import WikiLint


def _insert_wiki_page(pid, title, source_ids, updated_at, content="body"):
    Database.insert_wiki_page({
        "id": pid, "title": title, "content": content,
        "source_ids": json.dumps(source_ids), "tags": "[]",
        "concept_summary": "", "status": "published", "lint_score": 1.0,
        "created_at": "2026-07-01T00:00:00", "updated_at": updated_at,
    })


def test_lint_outdated_claim():
    """source updated_at 晚于 wiki page updated_at → outdated_claim。"""
    Database.insert_knowledge({
        "id": "k1", "title": "src", "content": "c",
        "source_type": "file", "source_path": "r", "file_type": "md",
        "file_size": 1, "content_hash": "h", "file_created_at": "",
        "file_modified_at": "", "tags": "[]", "version": 1,
        "created_at": "2026-07-01T00:00:00", "updated_at": "2026-07-05T00:00:00",  # 晚于 wiki
    })
    _insert_wiki_page("w1", "Wiki Page", ["k1"], updated_at="2026-07-02T00:00:00")
    report = WikiLint().run()
    cats = [f.category for f in report.findings]
    assert "outdated_claim" in cats


def test_lint_missing_backlinks():
    """页面无入链且非 sources 类型 → missing_backlinks。"""
    _insert_wiki_page("w2", "Lonely Page", [], updated_at="2026-07-02T00:00:00")
    report = WikiLint().run()
    cats = [f.category for f in report.findings]
    assert "missing_backlinks" in cats
```

- [ ] **Step 2: 验证失败**

Run: `python -m pytest tests/test_wiki_lint.py -v`
Expected: FAIL — `outdated_claim`/`missing_backlinks` 不在 categories

- [ ] **Step 3: 实现**

在 `src/services/wiki_lint.py` 的 `run()` 中,现有检查循环内(`:70` for page in pages 之后,`findings_for_page.extend` 之前)追加:

```python
            # 过时 claim:source updated_at 晚于 wiki page updated_at
            src_ids = json.loads(page.get("source_ids", "[]"))
            if src_ids and page.get("updated_at"):
                sources_map = Database.get_knowledge_batch(src_ids)
                page_updated = page.get("updated_at", "")
                for sid, src in sources_map.items():
                    src_updated = src.get("updated_at", "")
                    if src_updated and src_updated > page_updated:
                        findings_for_page.append(LintFinding(
                            severity="warning",
                            category="outdated_claim",
                            page_id=page["id"],
                            page_title=page.get("title", ""),
                            message=f"source {sid} updated ({src_updated}) after page ({page_updated})",
                            detail={"source_id": sid, "source_updated": src_updated, "page_updated": page_updated},
                        ))
```

在循环外(broken_link/dead_reference 检查附近)加缺失 backlinks:

```python
        # 缺失 backlinks:页面无入链
        for page in pages:
            backlinks = Database.get_backlinks(page["id"])
            if not backlinks:
                report.findings.append(LintFinding(
                    severity="info",
                    category="missing_backlinks",
                    page_id=page["id"],
                    page_title=page.get("title", ""),
                    message="页面无入链(无其他 wiki 页引用)",
                    detail={},
                ))
```

> `json` 已在 wiki_lint.py import(`:82` 用过)。`LintFinding`/`Database` 已在作用域。

- [ ] **Step 4: 验证通过**

Run: `python -m pytest tests/test_wiki_lint.py -v`
Expected: PASS(2 tests)

- [ ] **Step 5: commit**

```bash
git add src/services/wiki_lint.py tests/test_wiki_lint.py
git commit -m "feat(knowledge-base): enhance wiki_lint (outdated_claim + missing_backlinks)"
```

---

## Task 3: CLI `shinehe wiki` 子命令组

对应 spec §6.3 任务 3.3。**涉及 `main`/handlers,先 impact。**

**Files:**
- Modify: `src/cli.py`(加 `wiki` 子命令组 + `_handle_wiki`)
- Test: `tests/test_cli_wiki.py`

- [ ] **Step 0: impact**

```
impact({target: "main", direction: "upstream", repo: "ClaudeCodeWorkSpace", file_path: "projects/knowledge-base/src/cli.py"})
```

- [ ] **Step 1: 写失败测试**

`tests/test_cli_wiki.py`:

```python
"""shinehe wiki 子命令测试。"""
from unittest.mock import patch

import pytest

from src.cli import main


def test_wiki_lint_command_parses():
    with patch("src.cli._handle_wiki", return_value=0) as mock:
        with pytest.raises(SystemExit) as exc:
            main(["wiki", "lint"])
        assert exc.value.code == 0
        mock.assert_called_once()
        args = mock.call_args[0][0]
        assert args.wiki_command == "lint"


def test_wiki_save_answer_command_parses():
    with patch("src.cli._handle_wiki", return_value=0) as mock:
        with pytest.raises(SystemExit):
            main(["wiki", "save-answer", "--question", "Q?", "--answer", "A"])
        args = mock.call_args[0][0]
        assert args.wiki_command == "save-answer"
        assert args.question == "Q?"
        assert args.answer == "A"


def test_wiki_ingest_source_command_parses():
    with patch("src.cli._handle_wiki", return_value=0) as mock:
        with pytest.raises(SystemExit):
            main(["wiki", "ingest-source", "/path/to/file.md"])
        args = mock.call_args[0][0]
        assert args.wiki_command == "ingest-source"
        assert args.path == "/path/to/file.md"


def test_wiki_no_subcommand_prints_help(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["wiki"])
    assert exc.value.code == 0
```

- [ ] **Step 2: 验证失败**

Run: `python -m pytest tests/test_cli_wiki.py -v`
Expected: FAIL — `wiki` 子命令不存在

- [ ] **Step 3: 实现**

在 `src/cli.py` 加 handler(在 `_handle_mcp` 之后):

```python
def _handle_wiki(args: argparse.Namespace) -> int:
    """处理 wiki 子命令组。"""
    cmd = getattr(args, "wiki_command", None)
    if cmd is None:
        print("用法: shinehe wiki <lint|save-answer|ingest-source>")
        return 0

    if cmd == "lint":
        from src.services.wiki_lint import WikiLint
        report = WikiLint().run()
        for f in report.findings:
            print(f"  [{f.severity.upper()}] {f.category}: {f.page_title} — {f.message}")
        print(f"\n结果: {len(report.findings)} 个问题, 健康分 {report.score:.2f}, 共 {report.total_pages} 页")
        return 1 if report.findings else 0

    if cmd == "save-answer":
        from src.services.knowledge_workflow import KnowledgeWorkflowService
        from datetime import datetime
        result = KnowledgeWorkflowService().save_query(
            question=args.question, answer=args.answer,
            source_ids=[], confidence=1.0,
            page_type="syntheses", save_mode="manual",
            timestamp=datetime.now().isoformat(),
        )
        print(f"[OK] 保存: {result.get('path', result.get('status'))}")
        return 0

    if cmd == "ingest-source":
        target = Path(args.path).resolve()
        if not target.exists():
            print(f"[ERROR] 路径不存在: {target}", file=sys.stderr)
            return 1
        from src.services.path_indexer import PathIndexService
        from src.core.container import get_active_container
        container = get_active_container()
        indexer = container.path_indexer if container else PathIndexService()
        kid = indexer._ingest_file(target)
        print(f"[OK] ingest 完成: {kid}")
        return 0

    print(f"[ERROR] 未知 wiki 子命令: {cmd}", file=sys.stderr)
    return 1
```

在 `main()` 的 subparsers 区(`mcp_parser` 之后,`args = parser.parse_args(argv)` 之前)加:

```python
    # --- wiki (嵌套子命令组) ---
    wiki_parser = subparsers.add_parser(
        "wiki", help="wiki-first 知识维护",
        description="wiki 编译/检索闭环:lint / save-answer / ingest-source。",
    )
    wiki_sub = wiki_parser.add_subparsers(dest="wiki_command", help="wiki 子命令")

    lint_p = wiki_sub.add_parser("lint", help="运行 wiki 健康检查")
    save_p = wiki_sub.add_parser("save-answer", help="保存问答为 wiki 综合页")
    save_p.add_argument("--question", required=True, help="问题")
    save_p.add_argument("--answer", required=True, help="回答")
    ingest_p = wiki_sub.add_parser("ingest-source", help="ingest 单源并触发 wiki 编译")
    ingest_p.add_argument("path", help="源文件路径")
```

在 `handlers` dict 加 `"wiki": _handle_wiki`。

> 嵌套子命令:`args.command == "wiki"`,`args.wiki_command == "lint"|"save-answer"|"ingest-source"`。无 wiki_command 时 handler 打印帮助退出 0。

- [ ] **Step 4: 验证通过**

Run: `python -m pytest tests/test_cli_wiki.py -v`
Expected: PASS(4 tests)

- [ ] **Step 5: commit**

```bash
git add src/cli.py tests/test_cli_wiki.py
git commit -m "feat(knowledge-base): add 'shinehe wiki' CLI subcommand group"
```

---

## W3 阶段验收

**W3 DoD**(对应 spec §3 S3、S4):

- [ ] 3 个 Task 测试全绿
- [ ] S3:`WikiLint.run()` 检出 4 类(孤儿/矛盾/过时claim/缺失backlinks)
- [ ] S4:auto 模式按阈值写 draft 状态文件系统页
- [ ] 全量回归无回归(基线 1084 passed, 1 skipped)
- [ ] 阶段审查报告,等待批准进入 W4
