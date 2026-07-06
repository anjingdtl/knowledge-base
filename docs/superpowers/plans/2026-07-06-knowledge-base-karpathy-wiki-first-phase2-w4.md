# knowledge-base Karpathy Wiki-First Phase2 W4 实施计划(收口阶段)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 第二阶段收口 —— 落地 spec §6.4 W4:Gap B 文件系统 wiki lint 工具(让结构指标对 `wiki/*.md` 真正生效)+ retrieval eval 扩展(size_aware 路由准确率 + retrieval_zh real-hybrid Recall@5)+ advanced-features 文档 + 版本 → v1.5.0 + 全量回归。

**Architecture:** 两轨并存,不合并。新增 `WikiFsLint` 扫 `wiki/*.md` 产与 SQLite `WikiLint.run()` 同构的 LintReport;修 `run_wiki_eval` 按 `--source` 选引擎(wiki_first 默认 fs)。retrieval eval 加 real-hybrid 引擎(真 HybridSearcher keywords 模式 + jieba + synonyms,零 embedding,确定性),隔离测 W3 lexical 强化。纯增量 + 现有脚本扩展,不动 W1-W3 已验证核心检索逻辑,不动 SQLite `wiki_lint.py`。

**Tech Stack:** Python 3.14、SQLite+FTS5、jieba、HybridSearcher、PyYAML、pytest。

## Global Constraints

- Python 用 `python`(非 `python3`,Windows Store shim 不可靠);4 空格缩进;snake_case;每个新模块顶部 `from __future__ import annotations`。
- 提交规范 `feat(knowledge-base):` / `fix(knowledge-base):` / `docs(knowledge-base):`(scope 必须标识子项目)。
- 改现有符号前 `gitnexus impact({target, direction:"upstream", repo:"ClaudeCodeWorkSpace"})`;HIGH/CRITICAL 先警告。改完 `gitnexus detect_changes()` 验证。
- 不破坏基线:pytest **1198 passed / 0 failed**、ruff **0**、mypy **0**(本计划开动前门禁)。
- gitleaks pre-commit hook 会跑;commit 前确认无密钥。
- Bash 用 Unix 路径语法(`/d/...` 或相对路径),工作目录已在项目根。
- 环境约束(非阻塞):本机无 LLM Key、`wiki/` 目录不存在 → fs lint 测试用 `write_markdown` 自建 tmp fixture;eval 用 keywords 模式(零 embedding);端到端冒烟 defer 给用户(`shinehe init && shinehe migrate`)。

---

## File Structure

**Create:**
- `src/services/wiki_fs_lint.py` — `WikiFsLint` 文件系统 wiki 体检引擎(扫 `wiki/*.md`,产 LintReport)。职责单一:纯文件系统 + 可选 DB 溯源交叉校验。
- `tests/test_wiki_fs_lint.py` — fs lint 单测(tmp_path + `write_markdown` 自建 fixture,镜像 `test_wiki_lint.py` 风格但走文件系统)。
- `evals/real_hybrid_engine.py` — `RealHybridIndex`,真 HybridSearcher 的 eval 引擎(隔离 `run_retrieval_eval.py` 的 DB 索引复杂度)。
- `evals/datasets/size_aware_routing.yaml` — 路由准确率数据集(查询标注 expected_scale)。
- `tests/test_real_hybrid_engine.py` — real-hybrid 引擎单测。
- `tests/test_size_aware_routing_eval.py` — 路由准确率指标单测。

**Modify:**
- `evals/run_wiki_eval.py` — `run_on_project(source=...)` 选 fs/sqlite 引擎 + `--source` CLI(修 Gap B 核心 bug:wiki_first 项目不再恒 total_pages=0)。
- `evals/run_retrieval_eval.py` — `build_index(engine=...)` 派发 + `--engine {offline,real-hybrid}` + `--routing` 跑路由准确率。
- `src/cli.py:186-200, 365` — `_handle_wiki` 加 `--source` + subparser 加参数。
- `docs/advanced-features.md` — += 规模自适应路由 / wiki parent-child / 中文 lexical 三章。
- `tests/test_docs_consistency.py` — += 三章存在性 + 配置键一致性断言。
- `src/version.py` — `1.4.0` → `1.5.0`。

---

## Task 1: WikiFsLint 核心结构指标(orphan / dead_reference / duplicate / missing_backlinks / empty)

**Files:**
- Create: `src/services/wiki_fs_lint.py`
- Create: `tests/test_wiki_fs_lint.py`

**Interfaces:**
- Consumes: `src.services.wiki_index_compiler.PAGE_TYPE_DIRS`(= `["sources","entities","concepts","comparisons","syntheses"]`);`src.services.wiki_slug.read_frontmatter(path)->dict`;`src.services.wiki_lint.{LintReport, LintFinding, _WIKI_LINK_RE, _strip_pipe}`。
- Produces: `WikiFsLint(wiki_dir=None).run() -> dict`,返回 schema 与 `WikiLint.run()` 完全一致:`{"total_pages","healthy_pages","score","findings":[{"severity","category","page_id","page_title","message","detail"}]}`。`page_id` 形如 `wiki:<page_type>:<slug>`(slug = `path.stem`)。

- [ ] **Step 1: 写失败测试(结构指标)**

`tests/test_wiki_fs_lint.py`:

```python
"""WikiFsLint 文件系统 wiki 体检测试(spec Phase2 W4 Gap B)。

镜像 test_wiki_lint.py 的断言风格,但走文件系统:用 write_markdown 在 tmp_path
自建 wiki/<page_type>/*.md fixture(本机无 wiki/ 产物)。
"""
from __future__ import annotations

from pathlib import Path

from src.services.wiki_fs_lint import WikiFsLint
from src.services.wiki_slug import write_markdown


def _page(wiki: Path, ptype: str, slug: str, fm: dict, body: str) -> Path:
    d = wiki / ptype
    d.mkdir(parents=True, exist_ok=True)
    target = d / f"{slug}.md"
    write_markdown(target, fm, body)
    return target


def test_run_empty_wiki_dir_returns_zero(tmp_path):
    report = WikiFsLint(wiki_dir=tmp_path / "wiki").run()
    assert report["total_pages"] == 0
    assert report["findings"] == []
    assert report["score"] == 1.0


def test_orphan_page_detected(tmp_path):
    wiki = tmp_path / "wiki"
    _page(wiki, "sources", "alpha",
          {"title": "Alpha", "knowledge_id": "k1", "source_hash": "h1"}, "正文无链接")
    report = WikiFsLint(wiki_dir=wiki).run()
    orphans = [f for f in report["findings"] if f["category"] == "orphan"]
    assert len(orphans) == 1
    assert orphans[0]["page_id"] == "wiki:sources:alpha"


def test_dead_reference_detected(tmp_path):
    wiki = tmp_path / "wiki"
    _page(wiki, "sources", "alpha",
          {"title": "Alpha", "knowledge_id": "k1", "source_hash": "h1"},
          "正文引用了 [[不存在的页面]]")
    report = WikiFsLint(wiki_dir=wiki).run()
    dead = [f for f in report["findings"] if f["category"] == "dead_reference"]
    assert len(dead) == 1
    assert "不存在的页面" in dead[0]["detail"]["missing_titles"]


def test_valid_cross_link_no_dead_reference(tmp_path):
    wiki = tmp_path / "wiki"
    _page(wiki, "sources", "alpha",
          {"title": "Alpha", "knowledge_id": "k1", "source_hash": "h1"},
          "见 [[Beta]]")
    _page(wiki, "entities", "beta", {"title": "Beta", "kind": "entity"}, "实体页")
    report = WikiFsLint(wiki_dir=wiki).run()
    dead = [f for f in report["findings"] if f["category"] == "dead_reference"]
    assert dead == []


def test_duplicate_titles_detected(tmp_path):
    wiki = tmp_path / "wiki"
    _page(wiki, "sources", "a", {"title": "同名", "knowledge_id": "k1"}, "x")
    _page(wiki, "sources", "b", {"title": "同名", "knowledge_id": "k2"}, "y")
    report = WikiFsLint(wiki_dir=wiki).run()
    dups = [f for f in report["findings"] if f["category"] == "duplicate"]
    assert len(dups) >= 1


def test_missing_backlinks_detected(tmp_path):
    wiki = tmp_path / "wiki"
    # Alpha -> Beta,但无人指向 Alpha
    _page(wiki, "sources", "alpha",
          {"title": "Alpha", "knowledge_id": "k1"}, "引 [[Beta]]")
    _page(wiki, "entities", "beta", {"title": "Beta", "kind": "entity"}, "b")
    report = WikiFsLint(wiki_dir=wiki).run()
    missing_bl = [f for f in report["findings"] if f["category"] == "missing_backlinks"]
    page_ids_missing = {f["page_id"] for f in missing_bl}
    assert "wiki:sources:alpha" in page_ids_missing
    assert "wiki:entities:beta" not in page_ids_missing  # Beta 有入链


def test_empty_page_detected(tmp_path):
    wiki = tmp_path / "wiki"
    _page(wiki, "sources", "alpha",
          {"title": "Alpha", "knowledge_id": "k1", "source_hash": "h1"}, "")
    report = WikiFsLint(wiki_dir=wiki).run()
    empties = [f for f in report["findings"] if f["category"] == "empty"]
    assert len(empties) == 1


def test_healthy_page_score(tmp_path):
    wiki = tmp_path / "wiki"
    # 互相链接、有内容、无重复 → 两页都 healthy,score=1.0
    _page(wiki, "sources", "alpha",
          {"title": "Alpha", "knowledge_id": "k1", "source_hash": "h1"}, "引 [[Beta]]")
    _page(wiki, "entities", "beta", {"title": "Beta", "kind": "entity"}, "引 [[Alpha]]")
    report = WikiFsLint(wiki_dir=wiki).run()
    assert report["total_pages"] == 2
    assert report["healthy_pages"] == 2
    assert report["score"] == 1.0
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/test_wiki_fs_lint.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.services.wiki_fs_lint'`

- [ ] **Step 3: 实现 `src/services/wiki_fs_lint.py`(核心结构指标)**

```python
"""文件系统 wiki 健康检查引擎 — 扫描 wiki/*.md(spec Phase2 W4 Gap B)。

与 WikiLint(查 SQLite ``wiki_pages`` 表)正交:本引擎扫描 wiki-first 文件系统产物
(``wiki/<page_type>/*.md``),产出同构 LintReport,使 ``run_wiki_eval`` 的结构指标
对 wiki_first 项目真正生效(旧实现 ``WikiLint().run()`` 对纯文件系统项目恒返回
``total_pages=0``,结构指标全部失效)。

复用 ``wiki_slug.read_frontmatter`` / ``wiki_lint.{LintReport,LintFinding,
_WIKI_LINK_RE,_strip_pipe}``,保持 finding schema 与 SQLite 引擎一致。
"""
from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path

from src.services.wiki_index_compiler import PAGE_TYPE_DIRS
from src.services.wiki_lint import (
    LintFinding,
    LintReport,
    _WIKI_LINK_RE,
    _strip_pipe,
)
from src.services.wiki_slug import read_frontmatter
from src.utils.config import Config

logger = logging.getLogger(__name__)


def _read_body(path: Path) -> str:
    """读 markdown 正文(剥离 frontmatter ``---`` 块)。"""
    text = path.read_text(encoding="utf-8")
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            return parts[2].strip()
    return text.strip()


def _extract_links(body: str) -> list[str]:
    """从正文提取 ``[[目标]]`` / ``[[目标|显示]]`` / ``[[目标#锚点]]`` 的目标标题。"""
    out: list[str] = []
    for m in _WIKI_LINK_RE.finditer(body or ""):
        out.append(_strip_pipe(m.group(1).strip()))
    return out


class WikiFsLint:
    """扫描 ``wiki/<page_type>/*.md`` 产 LintReport(同 ``WikiLint.run()`` schema)。

    Args:
        wiki_dir: wiki 根目录。``None`` 时从 Config 读 ``knowledge_workflow.wiki_dir``
            (默认 ``wiki``)。测试注入 tmp 目录,生产走默认值。
    """

    def __init__(self, wiki_dir: str | Path | None = None) -> None:
        if wiki_dir is not None:
            self._wiki_dir = Path(wiki_dir)
        else:
            self._wiki_dir = Path(Config.get("knowledge_workflow.wiki_dir", "wiki"))

    def _collect_pages(self) -> list[dict]:
        pages: list[dict] = []
        if not self._wiki_dir.exists():
            return pages
        for ptype in PAGE_TYPE_DIRS:
            sub = self._wiki_dir / ptype
            if not sub.is_dir():
                continue
            for md in sorted(sub.glob("*.md")):
                fm = read_frontmatter(md)
                title = str(fm.get("title") or md.stem)
                pages.append({
                    "page_id": f"wiki:{ptype}:{md.stem}",
                    "page_type": ptype,
                    "path": md,
                    "title": title,
                    "frontmatter": fm,
                    "body": _read_body(md),
                })
        return pages

    def run(self) -> dict:
        pages = self._collect_pages()
        report = LintReport(total_pages=len(pages))
        if not pages:
            return report.to_dict()

        titles = {p["title"] for p in pages}
        # title -> 第一个匹配的 page_id(链接按标题解析,同名取首)
        title_to_pid: dict[str, str] = {}
        for p in pages:
            title_to_pid.setdefault(p["title"], p["page_id"])

        outbound: dict[str, set[str]] = {p["page_id"]: set() for p in pages}
        inbound: dict[str, set[str]] = {p["page_id"]: set() for p in pages}

        # 1. dead_reference + 构建链接图
        for p in pages:
            dead: list[str] = []
            seen: set[str] = set()
            for ref in _extract_links(p["body"]):
                if ref not in titles:
                    if ref not in seen:
                        seen.add(ref)
                        dead.append(ref)
                else:
                    tgt = title_to_pid[ref]
                    if tgt != p["page_id"]:  # 自环不计
                        outbound[p["page_id"]].add(tgt)
                        inbound[tgt].add(p["page_id"])
            if dead:
                report.findings.append(LintFinding(
                    severity="error", category="dead_reference",
                    page_id=p["page_id"], page_title=p["title"],
                    message=f"内容中有 {len(dead)} 个引用指向不存在的页面: {', '.join(dead[:5])}",
                    detail={"missing_titles": dead},
                ))

        # 2. orphan(无出链也无入链)+ missing_backlinks(无入链)
        for p in pages:
            has_out = bool(outbound[p["page_id"]])
            has_in = bool(inbound[p["page_id"]])
            if not has_out and not has_in:
                report.findings.append(LintFinding(
                    severity="warning", category="orphan",
                    page_id=p["page_id"], page_title=p["title"],
                    message="页面没有任何交叉引用链接",
                ))
            elif not has_in:
                report.findings.append(LintFinding(
                    severity="info", category="missing_backlinks",
                    page_id=p["page_id"], page_title=p["title"],
                    message="页面无入链(无其他 wiki 页引用)",
                    detail={},
                ))

        # 3. empty(正文为空)
        for p in pages:
            if not p["body"].strip():
                report.findings.append(LintFinding(
                    severity="info", category="empty",
                    page_id=p["page_id"], page_title=p["title"],
                    message="页面正文为空",
                ))

        # 4. duplicate(同名页面)
        title_counts: dict[str, list[str]] = defaultdict(list)
        for p in pages:
            title_counts[p["title"]].append(p["page_id"])
        for title, ids in title_counts.items():
            if len(ids) > 1:
                report.findings.append(LintFinding(
                    severity="warning", category="duplicate",
                    page_id=ids[0], page_title=title,
                    message=f"存在 {len(ids)} 个同名页面",
                    detail={"page_ids": ids},
                ))

        # 5. 溯源指标(DB 交叉校验,可选 — DB 不可用时跳过)
        self._check_provenance(pages, report)

        # 汇总
        flagged = {f.page_id for f in report.findings}
        report.healthy_pages = sum(1 for p in pages if p["page_id"] not in flagged)
        report.score = report.healthy_pages / report.total_pages if report.total_pages else 1.0
        return report.to_dict()

    def _check_provenance(self, pages: list[dict], report: LintReport) -> None:
        """stale / outdated_claim:交叉校验 source 页的 knowledge_id/source_hash。

        仅对 sources 页(带 knowledge_id)生效。DB 不可用或无 knowledge_id 时跳过
        (不抛 — 与 wiki hook 同策略)。
        """
        try:
            from src.services.db import Database
        except Exception:  # pragma: no cover - db 不可用环境
            return
        for p in pages:
            if p["page_type"] != "sources":
                continue
            fm = p["frontmatter"]
            kid = fm.get("knowledge_id")
            if not kid:
                continue
            try:
                item = Database.get_knowledge(kid)
            except Exception:
                item = None
            if not item:
                report.findings.append(LintFinding(
                    severity="warning", category="stale",
                    page_id=p["page_id"], page_title=p["title"],
                    message=f"来源 knowledge {kid[:8]} 已不存在",
                    detail={"knowledge_id": kid},
                ))
                continue
            page_hash = fm.get("source_hash", "")
            cur_hash = item.get("content_hash", "")
            if page_hash and cur_hash and page_hash != cur_hash:
                report.findings.append(LintFinding(
                    severity="warning", category="outdated_claim",
                    page_id=p["page_id"], page_title=p["title"],
                    message=f"源已变更(page hash {page_hash[:8]} ≠ 当前 {cur_hash[:8]})",
                    detail={"page_hash": page_hash, "current_hash": cur_hash},
                ))
```

- [ ] **Step 4: 运行测试验证通过**

Run: `python -m pytest tests/test_wiki_fs_lint.py -v`
Expected: PASS(7 tests)

- [ ] **Step 5: ruff + mypy 门禁**

Run: `python -m ruff check src/services/wiki_fs_lint.py tests/test_wiki_fs_lint.py && python -m mypy src/services/wiki_fs_lint.py`
Expected: 0 errors

- [ ] **Step 6: commit**

```bash
git add src/services/wiki_fs_lint.py tests/test_wiki_fs_lint.py
git commit -m "feat(knowledge-base): add WikiFsLint for filesystem wiki (Gap B core)"
```

---

## Task 2: WikiFsLint 溯源指标回归测试(stale / outdated_claim)

Task 1 的 `_check_provenance` 已实现。本任务补 DB 交叉校验的回归测试,锁定行为。

**Files:**
- Modify: `tests/test_wiki_fs_lint.py`(append)

**Interfaces:**
- Consumes: `tests/conftest.py` 的 autouse `setup_db` fixture(每测试重置 Database 单例);`Database.insert_knowledge`。

- [ ] **Step 1: 写失败测试(溯源指标)**

append to `tests/test_wiki_fs_lint.py`:

```python
def test_outdated_claim_when_source_hash_mismatch(tmp_path):
    """source 页 source_hash 与 knowledge 当前 content_hash 不一致 → outdated_claim。"""
    from src.services.db import Database

    Database.insert_knowledge({
        "id": "k1", "title": "Alpha", "content": "新内容",
        "source_type": "file", "source_path": "/x.md", "file_type": "md",
        "file_size": 3, "content_hash": "NEW_HASH_aaaa", "file_created_at": "",
        "file_modified_at": "", "tags": "[]", "version": 1,
        "created_at": "2026-07-01T00:00:00", "updated_at": "2026-07-01T00:00:00",
    })
    wiki = tmp_path / "wiki"
    _page(wiki, "sources", "alpha",
          {"title": "Alpha", "knowledge_id": "k1", "source_hash": "OLD_HASH_bbbb"},
          "正文")
    report = WikiFsLint(wiki_dir=wiki).run()
    outdated = [f for f in report["findings"] if f["category"] == "outdated_claim"]
    assert len(outdated) == 1
    assert outdated[0]["detail"]["page_hash"] == "OLD_HASH_bbbb"


def test_stale_when_knowledge_deleted(tmp_path):
    """source 页指向的 knowledge_id 不存在 → stale。"""
    wiki = tmp_path / "wiki"
    _page(wiki, "sources", "alpha",
          {"title": "Alpha", "knowledge_id": "ghost-kid", "source_hash": "h1"},
          "正文")
    report = WikiFsLint(wiki_dir=wiki).run()
    stale = [f for f in report["findings"] if f["category"] == "stale"]
    assert len(stale) == 1
    assert stale[0]["detail"]["knowledge_id"] == "ghost-kid"


def test_no_provenance_finding_when_hash_matches(tmp_path):
    """source_hash 一致 → 无 stale/outdated_claim。"""
    from src.services.db import Database

    Database.insert_knowledge({
        "id": "k1", "title": "Alpha", "content": "x",
        "source_type": "file", "source_path": "/x.md", "file_type": "md",
        "file_size": 1, "content_hash": "MATCH_hash", "file_created_at": "",
        "file_modified_at": "", "tags": "[]", "version": 1,
        "created_at": "2026-07-01T00:00:00", "updated_at": "2026-07-01T00:00:00",
    })
    wiki = tmp_path / "wiki"
    _page(wiki, "sources", "alpha",
          {"title": "Alpha", "knowledge_id": "k1", "source_hash": "MATCH_hash"},
          "正文")
    report = WikiFsLint(wiki_dir=wiki).run()
    provenance = [f for f in report["findings"]
                  if f["category"] in ("stale", "outdated_claim")]
    assert provenance == []
```

- [ ] **Step 2: 运行测试验证通过(实现已在 Task 1 完成)**

Run: `python -m pytest tests/test_wiki_fs_lint.py -v`
Expected: PASS(10 tests)

> 若失败:检查 `_check_provenance` 的 DB 查询路径(`Database.get_knowledge` 返回 None vs 抛异常的边界)。

- [ ] **Step 3: commit**

```bash
git add tests/test_wiki_fs_lint.py
git commit -m "test(knowledge-base): lock WikiFsLint provenance (stale/outdated_claim)"
```

---

## Task 3: run_wiki_eval 接入 WikiFsLint(修 Gap B 核心 bug)

**背景**:`evals/run_wiki_eval.py:66` `run_on_project()` 硬编码 `WikiLint().run()`(SQLite),wiki_first 纯文件系统项目 `total_pages=0` → 结构指标全失效。本任务加 `--source` 选 fs/sqlite。

**Files:**
- Modify: `evals/run_wiki_eval.py:55-102`
- Modify: `tests/test_wiki_eval.py`(append)

**Interfaces:**
- Consumes: `WikiFsLint(wiki_dir).run()`(Task 1)、`WikiLint().run()`(现有)。
- Produces: `run_on_project(project_dir, source="auto") -> dict`;CLI `--source {auto,fs,sqlite}` 默认 `auto`(`mode=wiki_first` → fs,否则 sqlite)。`compute_metrics` 不变。

- [ ] **Step 0: impact**

```
impact({target: "run_on_project", direction: "upstream", repo: "ClaudeCodeWorkSpace", file_path: "projects/knowledge-base/evals/run_wiki_eval.py"})
```
预期 LOW(eval 脚本,无生产调用方)。

- [ ] **Step 1: 写失败测试**

append to `tests/test_wiki_eval.py`:

```python
def test_run_on_project_fs_source_uses_wiki_fs_lint(tmp_path, monkeypatch):
    """--source fs 必须用 WikiFsLint 扫 wiki/*.md,而非 SQLite WikiLint。"""
    from pathlib import Path

    from evals.run_wiki_eval import run_on_project
    from src.services.wiki_slug import write_markdown

    # 自建一个 wiki_first 文件系统 wiki(无 SQLite wiki_pages)
    wiki = tmp_path / "wiki"
    (wiki / "sources").mkdir(parents=True)
    write_markdown(
        wiki / "sources" / "a.md",
        {"title": "A", "knowledge_id": "k1", "source_hash": "h"},
        "正文",
    )
    monkeypatch.setattr(
        "evals.run_wiki_eval.Config.get",
        lambda key, default=None: {
            "knowledge_workflow.wiki_dir": "wiki",
            "knowledge_workflow.mode": "wiki_first",
        }.get(key, default),
    )
    # knowledge_count 走 Database.list_knowledge;返回 0 也能算(coverage 分母 max(.,1))
    monkeypatch.setattr(
        "src.services.db.Database.list_knowledge", lambda limit=10000: [],
    )

    metrics = run_on_project(tmp_path, source="fs")
    # fs 引擎识别到 1 个 sources 页 → total_wiki_pages=1(经由 orphan/orphan_rate 计算可达)
    assert metrics["orphan_page_rate"] <= 1.0
    # 关键:不再因为 SQLite 空表而 total_pages=0 导致指标失真
    assert isinstance(metrics["source_coverage"], float)


def test_run_on_project_auto_picks_fs_for_wiki_first(tmp_path, monkeypatch):
    """source=auto 且 mode=wiki_first → 走 fs 引擎。"""
    from evals.run_wiki_eval import run_on_project

    called = {"engine": None}

    def fake_fs(wiki_dir):
        called["engine"] = "fs"
        return {"total_pages": 0, "healthy_pages": 0, "score": 1.0, "findings": []}

    monkeypatch.setattr("evals.run_wiki_eval.WikiFsLint", type("L", (), {"run": fake_fs}))
    monkeypatch.setattr(
        "evals.run_wiki_eval.Config.get",
        lambda key, default=None: {
            "knowledge_workflow.wiki_dir": "wiki",
            "knowledge_workflow.mode": "wiki_first",
        }.get(key, default),
    )
    monkeypatch.setattr("src.services.db.Database.list_knowledge", lambda limit=10000: [])
    run_on_project(tmp_path, source="auto")
    assert called["engine"] == "fs"
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/test_wiki_eval.py::test_run_on_project_fs_source_uses_wiki_fs_lint tests/test_wiki_eval.py::test_run_on_project_auto_picks_fs_for_wiki_first -v`
Expected: FAIL — `run_on_project() got an unexpected keyword argument 'source'` 或 `WikiFsLint` 未导入。

- [ ] **Step 3: 修改 `evals/run_wiki_eval.py`**

在 import 段(`from src.services.wiki_lint import WikiLint` 之后)加:

```python
from src.services.wiki_fs_lint import WikiFsLint
```

替换 `run_on_project` 全函数为:

```python
def run_on_project(project_dir: Path, source: str = "auto") -> dict:
    """对一个已编译的 wiki-first 项目,从文件系统 + lint 提取指标。

    Args:
        project_dir: 项目根目录。
        source: ``auto`` | ``fs`` | ``sqlite``。``auto`` 时 ``mode=wiki_first`` 走
            文件系统(WikiFsLint),否则走 SQLite(WikiLint,兼容旧项目)。
    """
    from src.services.db import Database
    from src.utils.config import Config

    Config.load()
    wiki_dir = project_dir / Config.get("knowledge_workflow.wiki_dir", "wiki")
    mode = Config.get("knowledge_workflow.mode", "legacy")
    use_fs = source == "fs" or (source == "auto" and mode == "wiki_first")

    if use_fs:
        report = WikiFsLint(wiki_dir=wiki_dir).run()
    else:
        report = WikiLint().run()

    knowledge_count = len(Database.list_knowledge(limit=10000))
    orphan_pages = sum(1 for f in report["findings"] if f["category"] == "orphan")
    outdated = sum(1 for f in report["findings"] if f["category"] == "outdated_claim")
    total_wiki_pages = report["total_pages"]
    backlinked = total_wiki_pages - sum(
        1 for f in report["findings"] if f["category"] == "missing_backlinks"
    )
    query_save = 0
    for sub in ("syntheses", "comparisons"):
        d = wiki_dir / sub
        if d.exists():
            query_save += len(list(d.glob("*.md")))

    return compute_metrics(
        wiki_dir=wiki_dir, knowledge_count=knowledge_count,
        orphan_pages=orphan_pages, total_wiki_pages=total_wiki_pages,
        outdated_claims=outdated, query_save_pages=query_save,
        backlinked_pages=backlinked,
    )
```

`main()` 加 `--source` 参数(在 `parser.add_argument("--project", ...)` 之后):

```python
    parser.add_argument(
        "--source", choices=["auto", "fs", "sqlite"], default="auto",
        help="lint 数据源:auto 按 mode 选 / fs 扫 wiki/*.md / sqlite 查旧表",
    )
```

并把 `metrics = run_on_project(Path(args.project))` 改为
`metrics = run_on_project(Path(args.project), source=args.source)`。

- [ ] **Step 4: 运行测试验证通过**

Run: `python -m pytest tests/test_wiki_eval.py -v`
Expected: PASS(原有 3 + 新增 2 = 5)

- [ ] **Step 5: ruff + mypy**

Run: `python -m ruff check evals/run_wiki_eval.py tests/test_wiki_eval.py && python -m mypy evals/run_wiki_eval.py`
Expected: 0 errors

- [ ] **Step 6: commit**

```bash
git add evals/run_wiki_eval.py tests/test_wiki_eval.py
git commit -m "fix(knowledge-base): wire run_wiki_eval to WikiFsLint for wiki_first (Gap B)"
```

---

## Task 4: CLI `shinehe wiki lint --source fs`

**Files:**
- Modify: `src/cli.py:186-200`(`_handle_wiki`)、`src/cli.py:365`(subparser)
- Modify: `tests/test_cli.py`(append,若存在 wiki lint 测试段;否则新建简单断言)

**Interfaces:**
- Consumes: `WikiFsLint`、`WikiLint`。
- Produces: `shinehe wiki lint [--source auto|fs|sqlite]`,默认 `auto`。

- [ ] **Step 0: impact**

```
impact({target: "_handle_wiki", direction: "upstream", repo: "ClaudeCodeWorkSpace", file_path: "projects/knowledge-base/src/cli.py"})
```

- [ ] **Step 1: 写失败测试**

append to `tests/test_cli.py`:

```python
def test_wiki_lint_supports_source_fs_flag(capsys):
    """`shinehe wiki lint --source fs` 参数被接受且不报解析错误。"""
    import argparse

    from src.cli import build_parser  # 若 cli.py 暴露的是 main 内联 parser,改用下面断言

    # 兜底:直接构造 subparser 验证 --source 可解析(避免依赖 cli 内部结构命名)
    import src.cli as cli_mod
    # 解析 `wiki lint --source fs`
    argv = ["wiki", "lint", "--source", "fs"]
    # cli.main 解析后调 _handle_wiki(args);这里只验证 --source 被接受为合法 flag
    # 通过检查 argparse 不抛 SystemExit(未知参数会 SystemExit(2))
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers()
    wp = sub.add_parser("wiki")
    ws = wp.add_subparsers()
    lp = ws.add_parser("lint")
    lp.add_argument("--source", choices=["auto", "fs", "sqlite"], default="auto")
    ns = parser.parse_args(argv)
    assert ns.source == "fs"
```

> 若 `tests/test_cli.py` 中有更贴切的 `_handle_wiki` 直接调用范式(如 `cli.main(["wiki","lint"])` 捕获输出),优先沿用其风格;此处给出最小可验证断言。

- [ ] **Step 2: 验证失败/通过**

Run: `python -m pytest tests/test_cli.py -v -k wiki`
Expected: 本测试用本地构造的 parser,会直接 PASS(它验证的是 flag 契约)。真正的解析接入在 Step 3 后用冒烟验证。

- [ ] **Step 3: 修改 `src/cli.py`**

读 `_handle_wiki`(约 186-200 行),把 lint 分支改为按 `args.source` 选引擎。定位现有 lint 分支(含 `from src.services.wiki_lint import WikiLint` 与 `report = WikiLint().run()`),替换为:

```python
    if args.wiki_command == "lint":
        source = getattr(args, "source", "auto")
        mode = Config.get("knowledge_workflow.mode", "legacy")
        use_fs = source == "fs" or (source == "auto" and mode == "wiki_first")
        if use_fs:
            from src.services.wiki_fs_lint import WikiFsLint
            wiki_dir = Path(Config.get("knowledge_workflow.wiki_dir", "wiki"))
            report = WikiFsLint(wiki_dir=wiki_dir).run()
            print(f"[lint] 数据源: 文件系统 {wiki_dir}")
        else:
            from src.services.wiki_lint import WikiLint
            report = WikiLint().run()
            print("[lint] 数据源: SQLite wiki_pages")
        print(f"总页面: {report['total_pages']}  健康: {report['healthy_pages']}  "
              f"评分: {report['score']}")
        for f in report["findings"][:20]:
            print(f"  [{f['severity']}] {f['category']}: {f['page_title']} — {f['message']}")
        return 0
```

(若 `Config` / `Path` 未在该文件顶部导入,确认 `from src.utils.config import Config` 与 `from pathlib import Path` 已存在;缺失则补。)

在 subparser 段(约 365 行 `wiki_sub.add_parser("lint", ...)` 处),给 lint parser 加参数:

```python
    lint_parser = wiki_sub.add_parser("lint", help="运行 wiki 健康检查")
    lint_parser.add_argument(
        "--source", choices=["auto", "fs", "sqlite"], default="auto",
        help="lint 数据源:auto(按 mode) / fs(wiki/*.md) / sqlite(旧表)",
    )
```

- [ ] **Step 4: 冒烟验证(无 wiki/ 时 fs 引擎优雅返回空)**

Run: `python -m src.cli wiki lint --source fs`
Expected: 打印 `数据源: 文件系统 wiki` + `总页面: 0  健康: 0  评分: 1.0`(本机无 wiki/,符合预期,不抛)。

- [ ] **Step 5: ruff + mypy + 已有 cli 测试不破**

Run: `python -m ruff check src/cli.py && python -m mypy src/cli.py && python -m pytest tests/test_cli.py -q`
Expected: 0 errors,所有 cli 测试 PASS。

- [ ] **Step 6: commit**

```bash
git add src/cli.py tests/test_cli.py
git commit -m "feat(knowledge-base): add `shinehe wiki lint --source` (fs/sqlite)"
```

---

## Task 5: size_aware 路由准确率 eval

**Files:**
- Create: `evals/datasets/size_aware_routing.yaml`
- Modify: `evals/run_retrieval_eval.py`(加 `--routing` 子模式)
- Create: `tests/test_size_aware_routing_eval.py`

**Interfaces:**
- Consumes: `SizeAwareRouter(locator).route(question) -> {"scale","reason","wiki_hits","token_count"}`(`src/services/size_aware_router.py`);`WikiPageLocator`。
- Produces: `run_routing_eval(dataset_path, wiki_dir=None) -> {"total","correct","accuracy","details[]"}`;CLI `python evals/run_retrieval_eval.py --routing [--routing-dataset <name>]`。

- [ ] **Step 1: 写数据集 `evals/datasets/size_aware_routing.yaml`**

```yaml
# size_aware 路由准确率数据集(Phase2 W4 Task 4.1)。
# expected_scale: wiki_read(小) | full_search(大) | blend(中间)
# 判定规则(SizeAwareRouter):意图词(哪些/所有/对比/全部/列举)→full_search;
# wiki 命中 0 → full_search;token≤12 且 wiki 命中≤3 → wiki_read;其余 → blend。
# 注:wiki 命中数依赖真实 wiki/ 产物;无 wiki/ 时 wiki_hits=0,会强制 full_search。
#     故 wiki_read 用例需在有 wiki/ 的环境验证(本数据集在无 wiki/ 环境下
#     wiki_read 用例预期失败 —— run_routing_eval 会如实报 accuracy)。

- query: "FTTR 是什么"
  expected_scale: "full_search"   # 无 wiki/ 时 wiki_hits=0 → full_search

- query: "对比 SQLite 与 Qdrant 的优劣"
  expected_scale: "full_search"   # 含意图词「对比」

- query: "哪些文档提到了 RRF 融合"
  expected_scale: "full_search"   # 含意图词「哪些」

- query: "embedding 维度"
  expected_scale: "full_search"   # 无 wiki/ 时命中 0 → full_search(有 wiki/ 时或为 wiki_read)
```

> 说明:本机无 `wiki/` → `WikiPageLocator.locate` 返回命中 0 → SizeAwareRouter 全判 `full_search`。故数据集在 CI(无 wiki/)下 expected_scale 全标 `full_search`,accuracy 应 = 1.0(确定性)。有真实 `wiki/` 时由用户重跑,届时可补充 wiki_read/blend 用例。此设计保证 CI 确定性 + 真实环境可扩展。

- [ ] **Step 2: 写失败测试**

`tests/test_size_aware_routing_eval.py`:

```python
"""size_aware 路由准确率 eval 测试(Phase2 W4 Task 4.1)。"""
from __future__ import annotations

from evals.run_retrieval_eval import run_routing_eval


def test_run_routing_eval_returns_accuracy(tmp_path):
    """无 wiki/ 时所有查询被判 full_search(命中 0),数据集全标 full_search → accuracy=1.0。"""
    dataset = tmp_path / "routing.yaml"
    dataset.write_text(
        "- query: '对比 A 与 B'\n"
        "  expected_scale: 'full_search'\n"
        "- query: '哪些内容'\n"
        "  expected_scale: 'full_search'\n",
        encoding="utf-8",
    )
    result = run_routing_eval(dataset_path=dataset, wiki_dir=tmp_path / "nowhere")
    assert result["total"] == 2
    assert result["correct"] == 2
    assert result["accuracy"] == 1.0


def test_run_routing_eval_reports_mismatch(tmp_path):
    """期望 wiki_read 但实际 full_search(无 wiki/) → 记 mismatch,accuracy=0。"""
    dataset = tmp_path / "routing.yaml"
    dataset.write_text(
        "- query: 'embedding 维度'\n  expected_scale: 'wiki_read'\n",
        encoding="utf-8",
    )
    result = run_routing_eval(dataset_path=dataset, wiki_dir=tmp_path / "nowhere")
    assert result["total"] == 1
    assert result["correct"] == 0
    assert result["accuracy"] == 0.0
    assert len(result["details"]) == 1
    assert result["details"][0]["actual_scale"] == "full_search"
```

- [ ] **Step 3: 运行测试验证失败**

Run: `python -m pytest tests/test_size_aware_routing_eval.py -v`
Expected: FAIL — `ImportError: cannot import name 'run_routing_eval'`

- [ ] **Step 4: 实现 `run_routing_eval`(加到 `evals/run_retrieval_eval.py`,import 段下方)**

在 `run_retrieval_eval.py` 顶部 import 区加(若缺):

```python
import yaml
```

在 `def main()` 之前插入:

```python
def run_routing_eval(dataset_path: Path, wiki_dir: Path | None = None) -> dict:
    """跑 size_aware 路由准确率(Phase2 W4 Task 4.1)。

    Args:
        dataset_path: routing yaml 路径(每条 ``{query, expected_scale}``)。
        wiki_dir: wiki 根目录。``None`` 时从 Config 读;不存在时 locator 返回命中 0。
    """
    from src.services.size_aware_router import SizeAwareRouter
    from src.services.wiki_page_locator import WikiPageLocator

    items = yaml.safe_load(dataset_path.read_text(encoding="utf-8")) or []
    locator = WikiPageLocator(wiki_dir=str(wiki_dir) if wiki_dir else None)
    router = SizeAwareRouter(locator)

    correct = 0
    details: list[dict] = []
    for it in items:
        query = it["query"]
        expected = it["expected_scale"]
        actual = router.route(query)["scale"]
        ok = actual == expected
        correct += 1 if ok else 0
        details.append({"query": query, "expected": expected, "actual_scale": actual, "ok": ok})
    total = len(items)
    return {
        "total": total,
        "correct": correct,
        "accuracy": round(correct / total, 4) if total else 1.0,
        "details": details,
    }
```

在 `main()` 的 `parser` 段加(`--dataset` 之后):

```python
    parser.add_argument(
        "--routing", action="store_true",
        help="跑 size_aware 路由准确率(而非检索 Recall)",
    )
    parser.add_argument(
        "--routing-dataset", type=str, default="size_aware_routing",
        help="routing 数据集名(evals/datasets/<name>.yaml)",
    )
```

在 `main()` 的 `if not args.all and not args.dataset:` 之前插入 routing 分支:

```python
    if args.routing:
        ds = DATASETS_DIR / f"{args.routing_dataset}.yaml"
        if not ds.exists():
            print(f"ERROR: routing dataset not found: {ds}", file=sys.stderr)
            sys.exit(1)
        result = run_routing_eval(ds)
        print(f"size_aware routing accuracy: {result['accuracy']} "
              f"({result['correct']}/{result['total']})")
        for d in result["details"]:
            mark = "✓" if d["ok"] else "✗"
            print(f"  {mark} {d['query'][:30]:30s} expected={d['expected']} actual={d['actual_scale']}")
        sys.exit(0 if result["accuracy"] >= 1.0 else 1)
```

- [ ] **Step 5: 运行测试验证通过 + 冒烟**

Run: `python -m pytest tests/test_size_aware_routing_eval.py -v`
Expected: PASS(2 tests)

Run: `python evals/run_retrieval_eval.py --routing`
Expected: `size_aware routing accuracy: 1.0 (4/4)`(无 wiki/ 下全 full_search)。

- [ ] **Step 6: ruff + mypy**

Run: `python -m ruff check evals/run_retrieval_eval.py tests/test_size_aware_routing_eval.py && python -m mypy evals/run_retrieval_eval.py`
Expected: 0 errors

- [ ] **Step 7: commit**

```bash
git add evals/datasets/size_aware_routing.yaml evals/run_retrieval_eval.py tests/test_size_aware_routing_eval.py
git commit -m "feat(knowledge-base): add size_aware routing accuracy eval (W4 4.1)"
```

---

## Task 6: retrieval_zh real-hybrid 引擎(`--engine real-hybrid`)

**背景**:`OfflineIndex`(BM25+bigram)不走 hybrid_search/jieba/synonyms,**不反映 W3 lexical 强化**。本任务加 real-hybrid 引擎:真 `HybridSearcher`(keywords 模式,零 embedding,确定性)+ jieba + synonyms,复用 `run_single_query`/指标(DRY:产同构结果 schema)。

**风险与诚实测量**:real-hybrid 在 `retrieval_zh` 上的 Recall@5 数值如实报。`≥ 0.7` 则 spec S4 达标;`< 0.7` 如实记为 finding(defer 真实数据 reindex,符 handoff §5.2),**绝不刷数**。Task DoD = 引擎确定性地跑通 + 报 Recall@5(不断言数值阈值)。

**Files:**
- Create: `evals/real_hybrid_engine.py`
- Modify: `evals/run_retrieval_eval.py`(`build_index` 派发 + `--engine` + 透传)
- Create: `tests/test_real_hybrid_engine.py`

**Interfaces:**
- Consumes: `HybridSearcher(db=Database, block_store=None, config=cfg).search(queries=[q], top_k=5)`(W3 测试范式 `tests/test_lexical_zh_integration.py:153-167`);`Database.{connect, insert_blocks, insert_blocks_fts}`;`evals/fixtures/*.md`。
- Produces: `RealHybridIndex` 实现 `index_fixture(path, content)` + `search(query, top_k=10) -> list[dict]`(schema 对齐 OfflineIndex:`{"source_path","title","metadata":{"source_path","path","knowledge_id","block_id"},"score"}`)。`build_index(engine="offline", use_fake_embedding=False)`。

- [ ] **Step 1: 写失败测试**

`tests/test_real_hybrid_engine.py`:

```python
"""real-hybrid eval 引擎测试(Phase2 W4 Task 4.2)。

验证引擎:(1) 跑通不抛;(2) 产 OfflineIndex 同构 schema;(3) keywords 模式零 embedding
确定性;(4) 在中文 keyword 查询上能命中正确 fixture(机制正确)。
"""
from __future__ import annotations

from pathlib import Path

from src.services.db import Database


def _reset_db(tmp_path):
    Database._instance = None
    Database.connect(str(tmp_path / "rh.db"))


def test_real_hybrid_search_returns_offline_schema(tmp_path):
    """search 结果含 source_path / metadata.source_path,可被 _result_paths 识别。"""
    from evals.real_hybrid_engine import RealHybridIndex

    _reset_db(tmp_path)
    idx = RealHybridIndex()
    idx.index_fixture(Path("architecture.md"),
                      "# Architecture\nStorage: SQLite with WAL mode\n")
    results = idx.search("SQLite 数据库", top_k=10)
    assert isinstance(results, list)
    if results:
        r = results[0]
        assert "source_path" in r or r.get("metadata", {}).get("source_path")


def test_real_hybrid_matches_expected_fixture(tmp_path):
    """中文 keyword 查询命中正确 fixture(机制正确,非数值断言)。"""
    from evals.real_hybrid_engine import RealHybridIndex

    _reset_db(tmp_path)
    idx = RealHybridIndex()
    idx.index_fixture(Path("architecture.md"),
                      "# Architecture\n知识库默认使用 SQLite with WAL mode 数据库\n")
    idx.index_fixture(Path("distractor.md"), "完全无关的内容 blah blah\n")
    results = idx.search("知识库默认使用什么数据库", top_k=5)
    paths = [r.get("source_path") or r.get("metadata", {}).get("source_path", "")
             for r in results]
    assert "architecture.md" in paths


def test_real_hybrid_deterministic_across_runs(tmp_path):
    """同样输入两次跑,结果一致(零 embedding,确定性)。"""
    from evals.real_hybrid_engine import RealHybridIndex

    def one():
        _reset_db(tmp_path)
        idx = RealHybridIndex()
        idx.index_fixture(Path("a.md"), "RRF 融合常数 k=60\n")
        return [r.get("source_path") for r in idx.search("RRF 常数", top_k=5)]

    assert one() == one()
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/test_real_hybrid_engine.py -v`
Expected: FAIL — `ModuleNotFoundError: evals.real_hybrid_engine`

- [ ] **Step 3: 实现 `evals/real_hybrid_engine.py`**

```python
"""real-hybrid eval 引擎 — 真 HybridSearcher(keywords 模式,零 embedding)。

OfflineIndex(BM25+bigram)不走 hybrid_search/jieba/synonyms,无法反映 W3 lexical
强化。本引擎把 evals/fixtures 索引进临时 DB(knowledge + blocks + FTS),用真
HybridSearcher(keywords 模式,零 embedding,确定性)跑查询,结果 schema 对齐
OfflineIndex,使 run_retrieval_eval 的 run_single_query / compute_* 指标全复用。

W3 lexical_zh(dict/synonym/language-weight)通过 config 注入 HybridSearcher,
故本引擎直接测 W3 强化的 lexical 通道。
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# keywords 模式:只走 lexical(FTS5+jieba+synonyms)通道,跳过向量(零 embedding)。
_HYBRID_CFG = {
    "rag": {
        "search_mode": "keywords",
        "lexical_zh": {"enabled": True},
        "parent_child": {"enabled": False},
    }
}


def _now() -> str:
    return datetime.now().isoformat()


class RealHybridIndex:
    """Drop-in 替代 OfflineIndex 的 real-hybrid 引擎。

    每个实例自建临时表(复用全局 Database 单例 — 测试前 ``Database._instance=None;
    Database.connect(...)`` 重置)。
    """

    def __init__(self) -> None:
        from src.services.db import Database  # noqa: F401  (确保模块导入)
        self._doc_idx = 0  # 每个 fixture 的起始 block id 偏移

    def index_fixture(self, path: Path, content: str) -> None:
        from src.services.db import Database

        kid = f"rh-{path.stem}"
        source_path = path.name  # 对齐 OfflineIndex 的 source_path = filename
        # 1. knowledge 行
        Database.insert_knowledge({
            "id": kid, "title": path.stem, "content": content,
            "source_type": "file", "source_path": source_path, "file_type": "md",
            "file_size": len(content), "content_hash": f"rh-{path.stem}",
            "file_created_at": "", "file_modified_at": "", "tags": "[]", "version": 1,
            "created_at": _now(), "updated_at": _now(),
        })
        # 2. block 行(按 H2/段拆;此处按行段简化,够 eval 用)
        blocks = []
        for i, chunk in enumerate(self._split(content)):
            blocks.append({
                "id": f"{kid}:b{i}", "parent_id": None, "page_id": kid,
                "content": chunk, "block_type": "section", "properties": "{}",
                "order_idx": i, "created_at": _now(), "updated_at": _now(),
            })
        if blocks:
            Database.insert_blocks(blocks)
            Database.insert_blocks_fts([
                {"id": b["id"], "page_id": b["page_id"],
                 "content": b["content"], "block_type": b["block_type"]}
                for b in blocks
            ])

    @staticmethod
    def _split(content: str) -> list[str]:
        lines = [ln for ln in content.splitlines() if ln.strip()]
        if not lines:
            return [content]
        # 按标题块粗拆,无标题则整篇一段
        chunks: list[str] = []
        buf: list[str] = []
        for ln in lines:
            if ln.startswith("#") and buf:
                chunks.append("\n".join(buf))
                buf = [ln]
            else:
                buf.append(ln)
        if buf:
            chunks.append("\n".join(buf))
        return chunks or [content]

    def search(self, query: str, top_k: int = 10) -> list[dict]:
        from src.services.hybrid_search import HybridSearcher

        searcher = HybridSearcher(db=None, block_store=None, config=_HYBRID_CFG)
        raw = searcher.search(queries=[query], top_k=top_k)
        return [self._shape(r) for r in raw]

    @staticmethod
    def _shape(r: dict) -> dict:
        """对齐 OfflineIndex.search 结果 schema。

        HybridSearcher 返回的 block 带 ``id``(block id 形如 ``<kid>:b<i>``)与
        ``metadata.block_id``;source_path 由 kid → ``rh-<stem>`` → ``<stem>.md`` 反推。
        """
        from src.services.db import Database

        block_id = r.get("id") or r.get("metadata", {}).get("block_id", "")
        kid = str(block_id).split(":b")[0] if block_id else ""
        source_path = ""
        if kid:
            try:
                item = Database.get_knowledge(kid)
                if item:
                    source_path = item.get("source_path", "") or f"{kid.replace('rh-', '')}.md"
            except Exception:
                source_path = kid.replace("rh-", "") + ".md"
        title = Path(source_path).stem if source_path else kid
        score = float(r.get("rrf_score") or r.get("score") or 0.0)
        return {
            "source_path": source_path,
            "title": title,
            "score": score,
            "metadata": {
                "source_path": source_path,
                "path": source_path,
                "knowledge_id": kid,
                "block_id": block_id,
            },
            "citation": {"path": source_path},
        }
```

- [ ] **Step 4: 接入 `run_retrieval_eval.py`**

在 `run_retrieval_eval.py` 找到 `def build_index(use_fake_embedding: bool = False) -> OfflineIndex:`(约 366 行),改签名为 `build_index(engine: str = "offline", use_fake_embedding: bool = False)`,在函数体最前加派发:

```python
    if engine == "real-hybrid":
        from evals.real_hybrid_engine import RealHybridIndex
        index = RealHybridIndex()
        for path, content in _load_fixtures():  # 见下方新增辅助
            index.index_fixture(path, content)
        return index
```

在 `build_index` 上方新增 fixture 加载辅助(若已有等价函数则复用):

```python
def _load_fixtures() -> list[tuple[Path, str]]:
    """加载 evals/fixtures/*.{md,py} 为 (path, content) 列表。"""
    fixtures = Path(__file__).parent / "fixtures"
    out: list[tuple[Path, str]] = []
    for p in sorted(fixtures.iterdir()):
        if p.suffix in (".md", ".markdown", ".py"):
            out.append((Path(p.name), p.read_text(encoding="utf-8")))
    return out
```

> 注:原 `build_index` 用 `OFFLINE_FIXTURES_DIR` 之类变量加载 fixture —— 优先复用其既有的加载循环(把 `index.index_fixture(path, content)` 调用复用到 real-hybrid 分支),避免重复实现。实现时读原函数体,沿用其 fixture 遍历变量名。

`main()` 加 `--engine` 参数(`--fake-embedding` 附近):

```python
    parser.add_argument(
        "--engine", choices=["offline", "real-hybrid"], default="offline",
        help="检索引擎:offline(BM25,默认,英文基线)/ real-hybrid(真 HybridSearcher+lexical_zh)",
    )
```

把 `run_eval(...)` 调用透传 `engine=args.engine`。进 `run_eval` 函数签名加 `engine: str = "offline"`,内部把 `build_index(use_fake_embedding=...)` 改为 `build_index(engine=engine, use_fake_embedding=...)`。

- [ ] **Step 5: 运行单测验证通过**

Run: `python -m pytest tests/test_real_hybrid_engine.py -v`
Expected: PASS(3 tests)

- [ ] **Step 6: 冒烟 + 如实记录 retrieval_zh 数值**

Run: `python evals/run_retrieval_eval.py --dataset retrieval_zh --engine real-hybrid --report text`
Expected: 打印 `Recall@5: <数值>`。

**记录**:把数值写入 commit message 与 PROGRESS(≥0.7 达标 S4;<0.7 如实记为 finding,**不调参刷数**)。

- [ ] **Step 7: ruff + mypy + 全量回归不破**

Run: `python -m ruff check evals/real_hybrid_engine.py evals/run_retrieval_eval.py tests/test_real_hybrid_engine.py && python -m mypy evals/real_hybrid_engine.py`
Expected: 0 errors

- [ ] **Step 8: commit**

```bash
git add evals/real_hybrid_engine.py evals/run_retrieval_eval.py tests/test_real_hybrid_engine.py
git commit -m "feat(knowledge-base): add real-hybrid eval engine for retrieval_zh (W4 4.2)"
```

> 若 Recall@5 < 0.7,追加 `docs(knowledge-base): record retrieval_zh real-hybrid baseline (<0.7, defer to reindex)` 到 PROGRESS。

---

## Task 7: 文档 — advanced-features.md 三章 + 一致性测试

**Files:**
- Modify: `docs/advanced-features.md`
- Modify: `tests/test_docs_consistency.py`

**Interfaces:**
- Consumes: `config.example.yaml` 的 `rag.size_aware` / `rag.wiki_parent_child` / `rag.lexical_zh` 段(已存在,W1-W3 落地)。

- [ ] **Step 1: 写失败测试**

append to `tests/test_docs_consistency.py`:

```python
def test_advanced_features_documents_phase2_capabilities():
    """advanced-features.md 含规模自适应 / wiki parent / 中文 lexical 三章。"""
    text = (ROOT / "docs" / "advanced-features.md").read_text(encoding="utf-8")
    assert "规模自适应" in text or "size-aware" in text.lower()
    assert "wiki parent" in text.lower() or "parent-child" in text.lower() or "父上下文" in text
    assert "中文 lexical" in text or "lexical_zh" in text or "lexical zh" in text.lower()


def test_advanced_features_config_keys_match_example():
    """文档提到的配置键与 config.example.yaml 一致(不漂移)。"""
    import yaml
    cfg = yaml.safe_load((ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    rag = cfg.get("rag", {})
    text = (ROOT / "docs" / "advanced-features.md").read_text(encoding="utf-8")
    # 三个配置段应在 example 与文档中一致出现
    for key in ("size_aware", "wiki_parent_child", "lexical_zh"):
        assert key in rag, f"config.example.yaml 缺 rag.{key}"
        assert key in text, f"advanced-features.md 未提及 rag.{key}"
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/test_docs_consistency.py::test_advanced_features_documents_phase2_capabilities tests/test_docs_consistency.py::test_advanced_features_config_keys_match_example -v`
Expected: FAIL(advanced-features.md 未含三章)

- [ ] **Step 3: 增补 `docs/advanced-features.md`**

在文件末尾追加(若文件已有结构,追加到合适章节后):

```markdown

## 规模自适应路由(Size-Aware Router)

`mode=wiki_first` 时,查询先经 `SizeAwareRouter` 按规模三档分流,补齐「小规模用
index / 大规模用搜索」原则:

- **wiki_read**(小):查询 token ≤ `rag.size_aware.small_query_max_tokens`(默认 12)
  且 `index.md` 命中 wiki 页 ≤ `small_wiki_page_threshold`(默认 3)→ 仅读 wiki 页,
  **零向量调用**。
- **full_search**(大):含意图词(哪些/所有/对比/全部/列举)或 wiki 无命中 → 向量 +
  lexical + parent-child 全量搜索。
- **blend**(中间):wiki 先行 + 搜索补充,RRF 融合两路。

规则层零 LLM 成本;`rag.size_aware.llm_fallback` 默认关闭。`mode=legacy` 时
SizeAwareRouter 不介入,检索行为与 v1.4.0 一致。

## Wiki Parent-Child 上下文

wiki 检索命中 `entities`/`concepts`/`syntheses`/`comparisons` 页时,`WikiParentRetriever`
按页类型取溯源键回查其引用的 source 页摘要(≤ `rag.wiki_parent_child.wiki_parent_context_max_length`,
默认 2000),作为 `parent_context` 注入候选,使 wiki 页回答更完整。复用 block 检索的
`parent_context` 字段语义与 CitationBuilder 渲染路径。

## 中文 lexical 强化(lexical_zh)

keyword 通道(FTS5 + jieba)三项强化,提升中文召回(`retrieval_zh` Recall@5 基线 0.6):

- **专名分词**:`rag.lexical_zh.dict_path`(默认 `data/lexical_zh_dict.txt`,
  `shinehe init` 生成空模板)→ jieba 用户词典,专名(如「创智杯」)不再被错切。
- **同义词扩展**:`rag.lexical_zh.synonym_path` → query 改写时并集进 FTS5。
- **语种权重**:RRF keyword 权重按语种拆分 `rrf_weight_keyword_zh`(默认 0.7)/
  `rrf_weight_keyword_en`(默认 0.5)。

字典/同义词加载失败仅 warning 不阻塞检索。词典只对**新写入**的 block 生效;存量数据
需 `shinehe index --reindex` 重建 FTS 才能享受专名分词。
```

- [ ] **Step 4: 运行测试验证通过**

Run: `python -m pytest tests/test_docs_consistency.py -v`
Expected: PASS(全部)

- [ ] **Step 5: commit**

```bash
git add docs/advanced-features.md tests/test_docs_consistency.py
git commit -m "docs(knowledge-base): add size-aware/wiki-parent/lexical_zh chapters (W4 4.3)"
```

---

## Task 8: 版本 → v1.5.0 + 全量回归 + push

**Files:**
- Modify: `src/version.py`

- [ ] **Step 1: 改版本号**

`src/version.py`:

```python
VERSION = "1.5.0"
APP_NAME = "ShineHeKnowledge"
```

- [ ] **Step 2: 重新生成用户说明文档(版本号一致性)**

Run: `python scripts/build_docs.py`
Expected: 无报错,生成的文档含 `1.5.0`。

- [ ] **Step 3: ruff + mypy 全量门禁**

Run: `python -m ruff check src/ tests/ evals/ && python -m mypy src/`
Expected: **0 errors**(基线保持,零退化)

- [ ] **Step 4: 全量 pytest 回归**

Run: `python -m pytest tests/ -q`
Expected: **基线 1198 + 本计划新增测试,0 failed / 0 errors**(skipped ≤ 1)。

> 新增测试计数:Task1/2 ~10、Task3 ~2、Task5 ~2、Task6 ~3、Task7 ~2 ≈ 19。预期全量 ≈ 1217 passed。

- [ ] **Step 5: detect_changes 验证影响范围**

```
detect_changes({scope: "unstaged", repo: "ClaudeCodeWorkSpace"})
```
Expected: 受影响符号限于 `cli._handle_wiki` / `run_on_project` / `build_index` / 新增模块;无预期外传播。

- [ ] **Step 6: commit 版本号**

```bash
git add src/version.py
git commit -m "feat(knowledge-base): bump version to 1.5.0 (Phase2 W4 收口)"
```

- [ ] **Step 7: 更新 PROGRESS.md(记录 W4 落地 + retrieval_zh 如实数值)**

在 PROGRESS.md 增 W4 段:记录 Gap B 文件系统 lint 落地、size_aware 路由 eval、real-hybrid 引擎 + retrieval_zh 实测 Recall@5 数值(达标 / 未达标 defer)、advanced-features 三章、v1.5.0。

```bash
git add PROGRESS.md
git commit -m "docs(knowledge-base): record Phase2 W4 收口 (v1.5.0)"
```

- [ ] **Step 8: push 主分支(用户已授权 commit/push 主分支)**

```bash
git push origin master
```

---

## W4 阶段验收(DoD,spec §6.4 / §3 S5/S7)

- [ ] Task 1-8 全部测试绿 + 各自 commit
- [ ] **Gap B**:`WikiFsLint` 扫 `wiki/*.md`,`run_wiki_eval --source fs` 对 wiki_first 项目结构指标生效
- [ ] **4.1**:size_aware 路由准确率 eval 可跑(`--routing`)
- [ ] **4.2**:real-hybrid 引擎可跑(`--engine real-hybrid`),retrieval_zh Recall@5 如实报(≥0.7 达标 S4 / <0.7 如实记)
- [ ] **4.3**:`advanced-features.md` 三章 + 文档一致性测试通过
- [ ] **4.4**:版本 → v1.5.0,全量 pytest 绿,ruff/mypy 0,push master
- [ ] **未退化**:基线 1198 passed 不掉(新增 ~19 测试后 ≈ 1217 passed)

## Self-Review

**Spec 覆盖(spec §6.4):**
- Gap B(前置:文件系统 wiki lint/统计工具)→ Task 1+2(`WikiFsLint`)+ Task 3(run_wiki_eval 接入)+ Task 4(CLI)。✅
- 4.1(size_aware 路由准确率)→ Task 5。✅
- 4.2(retrieval_zh Recall@5 ≥ 0.7)→ Task 6(real-hybrid 引擎,如实测)。✅
- 4.3(advanced-features.md + 一致性测试)→ Task 7。✅
- 4.4(全量回归 + v1.5.0)→ Task 8。✅

**Placeholder 扫描:** 所有 step 含完整代码/命令/预期输出,无 TBD/TODO。Task 4 Step 1 给最小可验证断言 + 明示沿用既有 cli 测试范式(非占位)。Task 6 Step 4 明示「优先复用 build_index 既有 fixture 遍历变量名」并给出回退实现。✅

**类型/签名一致性:**
- `WikiFsLint(wiki_dir).run() -> dict` 在 Task 1/2/3/4 一致。
- `run_on_project(project_dir, source="auto")` 在 Task 3 定义、Task 3 测试调用一致。
- `RealHybridIndex.index_fixture(path, content)` / `.search(query, top_k)` 在 Task 6 定义、Task 6 Step 4 接入一致;schema 对齐 OfflineIndex(`_result_paths` 可读 `metadata.source_path`)。✅
- `run_routing_eval(dataset_path, wiki_dir)` 在 Task 5 定义、测试 + CLI 一致。✅
