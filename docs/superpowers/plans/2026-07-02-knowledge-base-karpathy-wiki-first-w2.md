# knowledge-base Karpathy Wiki-First W2 实施计划(编译器阶段)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 ingest 真正"编译"为 wiki —— 任一文档入库后,`wiki/sources/` 出现规则模板生成的 source summary,`wiki/index.md`、`wiki/log.md` 自动更新;LLM 在硬上限内更新实体/概念页。

**Architecture:** 新增"文件系统 wiki 层"(`wiki/*.md`),与现有 SQLite `wiki_pages`(由 `WikiCompiler.ingest` 维护)是**两套并行产物**。新增 `KnowledgeWorkflowService` 编排 4 个独立编译器(source/entity/index/log),在 `mode=wiki_first` 时由 `path_indexer` 钩子触发;失败隔离,不阻塞检索主流程。

**Tech Stack:** Python 3.14、PyYAML、sqlite3、BGE-M3(不变)、LLMService(OpenAI 兼容)、pytest。

---

## 关键设计澄清(避免混淆)

1. **两套 wiki 产物分离**:
   - 现有 `WikiCompiler`(SQLite `wiki_pages`,LLM 概念提取)—— **不动**。
   - 新增"文件系统 wiki 层"(`wiki/sources|entities|concepts|comparisons|syntheses/*.md` + `index.md` + `log.md`)—— W2 产物。
   - `KnowledgeWorkflowService` 只管文件系统层。
2. **零 LLM 优先**:Task 1(source summary)完全规则化;Task 2(entity)才用 LLM。
3. **时间戳由调用方传入**:`compile(knowledge_id, ingested_at)`。编译器内**不取系统时间**,保证可复现测试。
4. **失败不阻塞**:编排器每步 try/except,钩子整体不抛(风险表 §9)。
5. **幂等**:同 source_hash 覆盖,不产生重复;log 按 (type,target,timestamp) hash 去重。
6. **mode 门控**:`Config.get("knowledge_workflow.mode", "legacy")` —— 仅 `wiki_first` 触发。

---

## Global Constraints

- Python 用 `python`(非 `python3`,Windows Store shim 不可靠)。
- 4 空格缩进;snake_case;`from __future__ import annotations`。
- 配置访问:`Config.get("knowledge_workflow.source_summary_dir", "wiki/sources")`(点号 + 默认值)。
- LLM 调用:`self._llm.chat([{"role":"user","content":prompt}], silent=True) -> str`,外层 try/except + `logger.warning`。
- 不破坏基线:每 Task 后跑该模块测试,阶段末跑全量(基线 1051 passed, 1 skipped)。
- 每个涉及现有符号的改动(Task 5)实现前先 `gitnexus impact`。
- 提交规范:`feat(knowledge-base): ...`,scope 必须 `knowledge-base`。

---

## File Structure

| 文件 | 职责 | 状态 |
|------|------|------|
| `src/services/wiki_slug.py` | 共用工具:slugify + frontmatter 读写 | 新建 |
| `src/services/wiki_source_compiler.py` | source summary 规则模板(零 LLM) | 新建 |
| `src/services/wiki_entity_updater.py` | LLM 实体/概念页(max 3 calls) | 新建 |
| `src/services/wiki_index_compiler.py` | `wiki/index.md` 聚合生成器 | 新建 |
| `src/services/wiki_log_compiler.py` | `wiki/log.md` 追加/重建 | 新建 |
| `src/services/knowledge_workflow.py` | `KnowledgeWorkflowService` 编排器 + 钩子 | 新建 |
| `src/core/container.py` | 注入 `knowledge_workflow` lazy property | 修改 |
| `src/services/path_indexer.py` | `_ingest_file`/`_reingest_file` 挂编译钩子 | 修改 |
| `tests/test_wiki_slug.py` | slug/frontmatter 工具测试 | 新建 |
| `tests/test_wiki_source_compiler.py` | source 编译器测试 | 新建 |
| `tests/test_wiki_entity_updater.py` | entity 更新器测试(mock LLM) | 新建 |
| `tests/test_wiki_index_compiler.py` | index 编译器测试 | 新建 |
| `tests/test_wiki_log_compiler.py` | log 编译器测试 | 新建 |
| `tests/test_knowledge_workflow.py` | 编排器 + 钩子 + path_indexer e2e(S2) | 新建 |

依赖顺序:wiki_slug → source_compiler → entity_updater → index_compiler → log_compiler → knowledge_workflow + container + path_indexer。

---

## Task 1: 共用工具 `wiki_slug.py`

**Files:**
- Create: `src/services/wiki_slug.py`
- Test: `tests/test_wiki_slug.py`

**Interfaces:**
- Produces: `slugify(title: str) -> str`、`resolve_slug(dir_path: Path, title: str, source_hash: str) -> tuple[str, Path]`、`read_frontmatter(path: Path) -> dict`、`write_markdown(path: Path, frontmatter: dict, body: str) -> None`

- [ ] **Step 1: 写失败测试**

`tests/test_wiki_slug.py`:

```python
"""wiki_slug 共用工具测试。"""
from pathlib import Path

from src.services.wiki_slug import (
    read_frontmatter,
    resolve_slug,
    slugify,
    write_markdown,
)


def test_slugify_lowercase_and_hyphen():
    assert slugify("Hello World") == "hello-world"


def test_slugify_strips_punctuation():
    assert slugify("API: Overview (v2)") == "api-overview-v2"


def test_slugify_keeps_chinese():
    assert slugify("知识库 入门") == "知识库-入门"


def test_slugify_empty():
    assert slugify("") == "untitled"
    assert slugify("!!!") == "untitled"


def test_resolve_slug_no_conflict(tmp_path):
    slug, path = resolve_slug(tmp_path, "My Title", "abc123")
    assert slug == "my-title"
    assert path == tmp_path / "my-title.md"


def test_resolve_slug_same_hash_idempotent(tmp_path):
    """同 hash 已存在 → 返回同路径(覆盖)。"""
    write_markdown(tmp_path / "dup.md", {"source_hash": "abc123"}, "old")
    slug, path = resolve_slug(tmp_path, "dup", "abc123")
    assert path == tmp_path / "dup.md"


def test_resolve_slug_conflict_appends_hash(tmp_path):
    """同 title 不同 hash → 追加 -{hash[:8]}。"""
    write_markdown(tmp_path / "conflict.md", {"source_hash": "oldhash"}, "old")
    slug, path = resolve_slug(tmp_path, "conflict", "newhash123")
    assert slug == "conflict-newhash1"
    assert path == tmp_path / "conflict-newhash1.md"


def test_write_and_read_frontmatter(tmp_path):
    p = tmp_path / "x.md"
    write_markdown(p, {"title": "T", "n": 3}, "Body text")
    fm = read_frontmatter(p)
    assert fm["title"] == "T"
    assert fm["n"] == 3
    assert "Body text" in p.read_text(encoding="utf-8")


def test_read_frontmatter_missing(tmp_path):
    p = tmp_path / "nofm.md"
    p.write_text("no frontmatter here", encoding="utf-8")
    assert read_frontmatter(p) == {}
```

- [ ] **Step 2: 验证失败**

Run: `python -m pytest tests/test_wiki_slug.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.services.wiki_slug'`

- [ ] **Step 3: 实现**

`src/services/wiki_slug.py`:

```python
"""wiki-first 文件系统层共用工具:slug 生成 + frontmatter 读写。"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

_UNSAFE_RE = re.compile(r"[^\w一-鿿\-]+")
_WS_RE = re.compile(r"\s+")


def slugify(title: str) -> str:
    """标题 → 文件名安全 slug。

    小写、标点去除(转空格)、空格转连字符、合并连续连字符;中文/字母/数字/连字符保留。
    """
    if not title:
        return "untitled"
    cleaned = _UNSAFE_RE.sub(" ", title).strip().lower()
    slug = _WS_RE.sub("-", cleaned)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or "untitled"


def resolve_slug(dir_path: Path, title: str, source_hash: str) -> tuple[str, Path]:
    """解析最终 slug,处理同名冲突。

    - 文件不存在 → (slugify(title), <slug>.md)
    - 已存在且 frontmatter source_hash 相同 → 返回同路径(幂等覆盖)
    - 已存在但 hash 不同 → 追加 ``-{hash[:8]}``
    """
    base = slugify(title)
    candidate = dir_path / f"{base}.md"
    if not candidate.exists():
        return base, candidate
    existing = read_frontmatter(candidate).get("source_hash", "")
    if existing == source_hash:
        return base, candidate
    suffix = (source_hash[:8]) if source_hash else "dup"
    conflicted = dir_path / f"{base}-{suffix}.md"
    return f"{base}-{suffix}", conflicted


def read_frontmatter(path: Path) -> dict:
    """读取 markdown frontmatter(`---` 之间的 YAML)。无则返回 {}。"""
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    try:
        data = yaml.safe_load(parts[1])
        return data if isinstance(data, dict) else {}
    except yaml.YAMLError:
        return {}


def write_markdown(path: Path, frontmatter: dict, body: str) -> None:
    """原子写入 frontmatter + body。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fm = yaml.safe_dump(
        frontmatter, allow_unicode=True, default_flow_style=False, sort_keys=False
    )
    path.write_text(f"---\n{fm}---\n\n{body}\n", encoding="utf-8")
```

- [ ] **Step 4: 验证通过**

Run: `python -m pytest tests/test_wiki_slug.py -v`
Expected: PASS(9 passed)

- [ ] **Step 5: Commit**

```bash
git add src/services/wiki_slug.py tests/test_wiki_slug.py
git commit -m "feat(knowledge-base): add wiki_slug shared tooling (slugify/frontmatter)"
```

---

## Task 2: `WikiSourceCompiler` — source summary 规则模板(零 LLM)

对应 spec §6.2 任务 2.1。

**Files:**
- Create: `src/services/wiki_source_compiler.py`
- Test: `tests/test_wiki_source_compiler.py`

**Interfaces:**
- Consumes: `Database.get_knowledge(item_id) -> dict | None`(字段:`title`/`content`/`source_path`/`file_type`/`content_hash`/`created_at`);`Config.get("knowledge_workflow.source_summary_dir"|"wiki_dir", ...)`
- Produces: `WikiSourceCompiler().compile(knowledge_id: str, ingested_at: str) -> dict` → `{"status","path","slug","key_entities"}`

- [ ] **Step 1: 写失败测试**

`tests/test_wiki_source_compiler.py`:

```python
"""WikiSourceCompiler 测试(规则模板,零 LLM)。"""
from pathlib import Path

import pytest

from src.services.db import Database
from src.services.wiki_source_compiler import WikiSourceCompiler


@pytest.fixture
def one_knowledge(tmp_path, monkeypatch):
    """注入一条 knowledge + wiki_first 配置,wiki_dir 指向 tmp_path。"""
    Database.reset_instance()
    db = Database(str(tmp_path / "kb.db"))
    Database._instance = db
    conn = db.get_conn()
    conn.execute(
        """INSERT INTO knowledge_items
           (id, title, content, source_type, source_path, file_type,
            content_hash, tags, version, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "kid-1", "API Overview",
            "# API Overview\n\nThe MCP API exposes tools.\n\n## Endpoints\n\nPOST /ask",
            "file", str(tmp_path / "raw" / "api.md"), "md",
            "hashabc12345", "[]", 1, "2026-07-02T10:00:00", "2026-07-02T10:00:00",
        ),
    )
    conn.commit()
    monkeypatch.setenv("SHINEHE_HOME", str(tmp_path))
    yield tmp_path
    Database.reset_instance()


def test_compile_generates_source_summary(one_knowledge, monkeypatch):
    monkeypatch.setattr(
        "src.services.wiki_source_compiler.Config.get",
        lambda key, default=None: {
            "knowledge_workflow.wiki_dir": str(one_knowledge / "wiki"),
            "knowledge_workflow.source_summary_dir": str(one_knowledge / "wiki" / "sources"),
        }.get(key, default),
    )
    result = WikiSourceCompiler().compile("kid-1", ingested_at="2026-07-02T10:00:00")
    assert result["status"] == "compiled"
    p = Path(result["path"])
    assert p.exists()
    text = p.read_text(encoding="utf-8")
    assert "API Overview" in text
    assert "hashabc12345" in text  # frontmatter source_hash
    assert "MCP" in text  # key entity extracted (acronym)


def test_compile_idempotent(one_knowledge, monkeypatch):
    monkeypatch.setattr(
        "src.services.wiki_source_compiler.Config.get",
        lambda key, default=None: {
            "knowledge_workflow.wiki_dir": str(one_knowledge / "wiki"),
            "knowledge_workflow.source_summary_dir": str(one_knowledge / "wiki" / "sources"),
        }.get(key, default),
    )
    c = WikiSourceCompiler()
    r1 = c.compile("kid-1", ingested_at="2026-07-02T10:00:00")
    r2 = c.compile("kid-1", ingested_at="2026-07-02T10:00:00")
    assert r1["path"] == r2["path"]  # 同路径覆盖,无第二文件
    sources_dir = Path(r1["path"]).parent
    assert len(list(sources_dir.glob("*.md"))) == 1


def test_compile_not_found(one_knowledge, monkeypatch):
    monkeypatch.setattr(
        "src.services.wiki_source_compiler.Config.get",
        lambda key, default=None: {
            "knowledge_workflow.wiki_dir": str(one_knowledge / "wiki"),
            "knowledge_workflow.source_summary_dir": str(one_knowledge / "wiki" / "sources"),
        }.get(key, default),
    )
    result = WikiSourceCompiler().compile("missing", ingested_at="2026-07-02T10:00:00")
    assert result["status"] == "not_found"


def test_extract_key_entities_acronyms_and_title():
    entities = WikiSourceCompiler._extract_key_entities(
        "The LLM and MCP tools work with the API.", "API Overview"
    )
    assert "LLM" in entities
    assert "MCP" in entities
    assert "API" in entities


def test_build_summary_truncates_to_500():
    long = "# H\n\n" + ("x" * 2000)
    summary = WikiSourceCompiler._build_summary(long)
    assert len(summary) <= 500
    assert "H" in summary  # heading path included
```

- [ ] **Step 2: 验证失败**

Run: `python -m pytest tests/test_wiki_source_compiler.py -v`
Expected: FAIL — `ModuleNotFoundError: ... wiki_source_compiler`

- [ ] **Step 3: 实现**

`src/services/wiki_source_compiler.py`:

```python
"""wiki-first source summary 编译器(规则模板,零 LLM)。

从 knowledge 条目抽取标题/首段/关键实体,生成 ``wiki/sources/<slug>.md``。
幂等:同 source_hash 覆盖,不产生重复。
时间戳由调用方传入,内部不取系统时间(可复现)。
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from src.services.db import Database
from src.services.wiki_slug import resolve_slug, write_markdown
from src.utils.config import Config

logger = logging.getLogger(__name__)

SUMMARY_MAX_CHARS = 500
KEY_ENTITIES_LIMIT = 10
_ACRONYM_RE = re.compile(r"\b[A-Z]{2,6}\b")
_WORD_RE = re.compile(r"[\w一-鿿]+")
_STOPWORDS = {
    "the", "a", "an", "of", "and", "or", "to", "in", "for", "on", "with",
    "is", "are", "be", "as", "at", "by", "this", "that",
}


class WikiSourceCompiler:
    """单源摘要页编译器(规则驱动,无 LLM 调用)。"""

    def compile(self, knowledge_id: str, ingested_at: str) -> dict:
        """为 knowledge 生成 source summary 页。

        Returns:
            ``{"status","path","slug","key_entities"}``;knowledge 不存在时
            ``{"status":"not_found"}``。
        """
        item = Database.get_knowledge(knowledge_id)
        if not item:
            return {"status": "not_found"}

        wiki_dir = Config.get("knowledge_workflow.wiki_dir", "wiki")
        sources_dir = Path(
            Config.get("knowledge_workflow.source_summary_dir", f"{wiki_dir}/sources")
        )
        sources_dir.mkdir(parents=True, exist_ok=True)

        title = item.get("title") or "untitled"
        content = item.get("content") or ""
        source_hash = item.get("content_hash") or ""

        slug, target = resolve_slug(sources_dir, title, source_hash)
        summary = self._build_summary(content)
        entities = self._extract_key_entities(content, title)

        frontmatter = {
            "title": title,
            "source_path": item.get("source_path", ""),
            "file_type": item.get("file_type", ""),
            "source_hash": source_hash,
            "ingested_at": ingested_at,
            "key_entities": entities,
            "knowledge_id": knowledge_id,
        }
        body = self._render_body(frontmatter, summary)
        write_markdown(target, frontmatter, body)
        logger.info("source summary compiled: %s (kid=%s)", target, knowledge_id)
        return {
            "status": "compiled",
            "path": str(target),
            "slug": slug,
            "key_entities": entities,
            "summary": summary,
        }

    @staticmethod
    def _build_summary(content: str) -> str:
        """首段 + 标题路径精炼,截断 ≤500 字。"""
        if not content:
            return ""
        lines = content.splitlines()
        heading_path = [
            ln.strip().lstrip("#").strip() for ln in lines if ln.strip().startswith("#")
        ]
        first_para = ""
        for ln in lines:
            stripped = ln.strip()
            if stripped and not stripped.startswith("#"):
                first_para = stripped
                break
        parts: list[str] = []
        if heading_path:
            parts.append(" / ".join(heading_path[:5]))
        if first_para:
            parts.append(first_para)
        summary = "\n\n".join(parts) if parts else content[:SUMMARY_MAX_CHARS]
        return summary[:SUMMARY_MAX_CHARS]

    @staticmethod
    def _extract_key_entities(content: str, title: str) -> list[str]:
        """规则抽取专名/缩略词(零 LLM)。"""
        entities: list[str] = []
        for acr in _ACRONYM_RE.findall(content or ""):
            if acr not in entities:
                entities.append(acr)
        for w in _WORD_RE.findall(title or ""):
            wl = w.lower()
            if len(wl) > 1 and wl not in _STOPWORDS and w not in entities:
                entities.append(w)
        return entities[:KEY_ENTITIES_LIMIT]

    @staticmethod
    def _render_body(frontmatter: dict, summary: str) -> str:
        lines = [f"# {frontmatter['title']}", ""]
        lines.append(f"**Source:** `{frontmatter['source_path']}`  ")
        lines.append(f"**Type:** {frontmatter['file_type']}  ")
        lines.append(f"**Ingested:** {frontmatter['ingested_at']}")
        lines.append("")
        if frontmatter.get("key_entities"):
            lines.append("## Key entities")
            lines.append("")
            lines.append(", ".join(f"[[{e}]]" for e in frontmatter["key_entities"]))
            lines.append("")
        lines.append("## Summary")
        lines.append("")
        lines.append(summary or "(empty)")
        return "\n".join(lines)
```

- [ ] **Step 4: 验证通过**

Run: `python -m pytest tests/test_wiki_source_compiler.py -v`
Expected: PASS(5 passed)

- [ ] **Step 5: Commit**

```bash
git add src/services/wiki_source_compiler.py tests/test_wiki_source_compiler.py
git commit -m "feat(knowledge-base): add rule-based wiki_source_compiler (zero-LLM)"
```

---

## Task 3: `WikiEntityUpdater` — LLM 实体页(max 3 calls/ingest)

对应 spec §6.2 任务 2.2。

**Files:**
- Create: `src/services/wiki_entity_updater.py`
- Test: `tests/test_wiki_entity_updater.py`

**Interfaces:**
- Consumes: `LLMService`(`__init__` 可注入,测试传 mock);`Config.get("wiki.max_llm_calls_per_ingest", 3)`;`knowledge_workflow.entity_dir`/`concept_dir`
- Produces: `WikiEntityUpdater(llm=None).update(knowledge_id, source_summary: dict, ingested_at: str) -> dict` → `{"entities_created","concepts_created","llm_calls","contradictions"}`

- [ ] **Step 1: 写失败测试**

`tests/test_wiki_entity_updater.py`:

```python
"""WikiEntityUpdater 测试(mock LLM)。"""
from pathlib import Path

import pytest

from src.services.wiki_entity_updater import WikiEntityUpdater


class FakeLLM:
    """记录调用次数,按 entity 名返回固定 JSON。"""

    def __init__(self):
        self.calls = 0

    def chat(self, messages, silent=False):
        self.calls += 1
        prompt = messages[0]["content"]
        # 提取 prompt 里的实体名
        import re
        m = re.search(r"实体名: (.+)", prompt)
        entity = m.group(1).strip() if m else "X"
        import json
        return json.dumps({
            "action": "create",
            "summary": f"{entity} 是一个关键概念。",
            "facts": [f"{entity} 在源中被提及"],
            "contradictions": [],
        })


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "src.services.wiki_entity_updater.Config.get",
        lambda key, default=None: {
            "knowledge_workflow.wiki_dir": str(tmp_path / "wiki"),
            "knowledge_workflow.entity_dir": str(tmp_path / "wiki" / "entities"),
            "knowledge_workflow.concept_dir": str(tmp_path / "wiki" / "concepts"),
            "wiki.max_llm_calls_per_ingest": 3,
        }.get(key, default),
    )
    return tmp_path


def test_update_creates_entity_pages(dirs):
    fake = FakeLLM()
    updater = WikiEntityUpdater(llm=fake)
    result = updater.update(
        "kid-1",
        {"key_entities": ["LLM", "MCP", "API"], "title": "T", "summary": "s"},
        ingested_at="2026-07-02T10:00:00",
    )
    assert result["llm_calls"] == 3
    total_created = result["entities_created"] + result["concepts_created"]
    assert total_created == 3
    assert (dirs / "wiki" / "entities").is_dir()


def test_update_respects_max_calls(dirs):
    """5 个实体但 max=3 → 只调 3 次 LLM。"""
    fake = FakeLLM()
    updater = WikiEntityUpdater(llm=fake)
    result = updater.update(
        "kid-1",
        {"key_entities": ["A", "B", "C", "D", "E"], "title": "T", "summary": "s"},
        ingested_at="2026-07-02T10:00:00",
    )
    assert result["llm_calls"] == 3
    assert fake.calls == 3


def test_update_no_entities_skips_llm(dirs):
    fake = FakeLLM()
    updater = WikiEntityUpdater(llm=fake)
    result = updater.update(
        "kid-1",
        {"key_entities": [], "title": "T", "summary": "s"},
        ingested_at="2026-07-02T10:00:00",
    )
    assert result["llm_calls"] == 0
    assert fake.calls == 0
    assert result["entities_created"] == 0


def test_update_marks_contradictions(dirs):
    class ContradictionLLM:
        def __init__(self):
            self.calls = 0

        def chat(self, messages, silent=False):
            self.calls += 1
            import json
            return json.dumps({
                "action": "update",
                "summary": "更新",
                "facts": [],
                "contradictions": ["新源称 API v3,旧页称 v2"],
            })

    updater = WikiEntityUpdater(llm=ContradictionLLM())
    result = updater.update(
        "kid-1",
        {"key_entities": ["API"], "title": "T", "summary": "s"},
        ingested_at="2026-07-02T10:00:00",
    )
    assert result["contradictions"]  # 非空
    page = list((dirs / "wiki" / "entities").glob("*.md"))[0]
    assert "CONTRADICTION" in page.read_text(encoding="utf-8")


def test_update_llm_failure_skipped(dirs):
    class FailLLM:
        def chat(self, messages, silent=False):
            raise RuntimeError("api down")

    updater = WikiEntityUpdater(llm=FailLLM())
    result = updater.update(
        "kid-1",
        {"key_entities": ["A"], "title": "T", "summary": "s"},
        ingested_at="2026-07-02T10:00:00",
    )
    assert result["entities_created"] == 0
    assert result["llm_calls"] == 0  # 失败不计成功
```

- [ ] **Step 2: 验证失败**

Run: `python -m pytest tests/test_wiki_entity_updater.py -v`
Expected: FAIL — `ModuleNotFoundError: ... wiki_entity_updater`

- [ ] **Step 3: 实现**

`src/services/wiki_entity_updater.py`:

```python
"""wiki-first 实体/概念页 LLM 更新器。

根据 source summary 的 key_entities,用 LLM 生成/更新实体页与概念页。
硬上限:``wiki.max_llm_calls_per_ingest``(默认 3)。矛盾显式标注。
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from src.services.llm import LLMService
from src.services.wiki_slug import slugify, write_markdown
from src.utils.config import Config

logger = logging.getLogger(__name__)

ENTITY_PROMPT_TEMPLATE = """\
你正在维护一个 wiki 知识库的实体页。基于新源摘要更新实体信息。

实体名: {entity}
实体类型: {kind}
新源标题: {source_title}
新源摘要:
{source_summary}
新源关键实体: {key_entities}

已有实体页内容(空则新建):
{existing_content}

仅输出 JSON(无其它文字):
{{
  "action": "create" | "update",
  "summary": "该实体 50-150 字描述",
  "facts": ["从新源提取的事实条目"],
  "contradictions": ["与已有内容矛盾处;无则空列表"]
}}
"""


class WikiEntityUpdater:
    """LLM 驱动的实体/概念页更新器。"""

    def __init__(self, llm: LLMService | None = None):
        self._llm = llm or LLMService()

    def update(self, knowledge_id: str, source_summary: dict, ingested_at: str) -> dict:
        """为新源涉及的实体/概念生成或更新页面。"""
        max_calls = int(Config.get("wiki.max_llm_calls_per_ingest", 3))
        entities = list(source_summary.get("key_entities", []))[:max_calls]
        result = {
            "entities_created": 0,
            "concepts_created": 0,
            "llm_calls": 0,
            "contradictions": [],
        }
        if not entities:
            return result

        wiki_dir = Config.get("knowledge_workflow.wiki_dir", "wiki")
        entity_dir = Path(Config.get("knowledge_workflow.entity_dir", f"{wiki_dir}/entities"))
        concept_dir = Path(Config.get("knowledge_workflow.concept_dir", f"{wiki_dir}/concepts"))
        entity_dir.mkdir(parents=True, exist_ok=True)
        concept_dir.mkdir(parents=True, exist_ok=True)

        source_title = source_summary.get("title", "")
        source_summary_text = source_summary.get("summary", "")

        for entity in entities:
            if result["llm_calls"] >= max_calls:
                logger.warning("entity update hit max_llm_calls (%d), truncating", max_calls)
                break
            kind = self._classify(entity)
            target_dir = entity_dir if kind == "entity" else concept_dir
            existing_path = target_dir / f"{slugify(entity)}.md"
            existing_content = ""
            if existing_path.exists():
                existing_content = self._strip_frontmatter(
                    existing_path.read_text(encoding="utf-8")
                )
            try:
                resp = self._llm.chat(
                    [{"role": "user", "content": ENTITY_PROMPT_TEMPLATE.format(
                        entity=entity,
                        kind=kind,
                        source_title=source_title,
                        source_summary=source_summary_text[:400],
                        key_entities=", ".join(entities),
                        existing_content=existing_content[:1000],
                    )}],
                    silent=True,
                )
            except Exception as e:
                logger.warning("entity LLM call failed for %s: %s", entity, e)
                continue
            result["llm_calls"] += 1
            parsed = self._parse_json(resp)
            if not parsed:
                continue
            if parsed.get("contradictions"):
                result["contradictions"].extend(parsed["contradictions"])
            self._write_entity_page(
                target_dir, entity, kind, parsed, knowledge_id, ingested_at,
                bool(existing_content),
            )
            if kind == "entity":
                result["entities_created"] += 1
            else:
                result["concepts_created"] += 1
        return result

    @staticmethod
    def _classify(entity: str) -> str:
        """简单分类:全大写缩略词 → concept;其余 → entity。"""
        if entity.isupper() and len(entity) <= 6:
            return "concept"
        return "entity"

    @staticmethod
    def _parse_json(response: str) -> dict | None:
        if not response:
            return None
        start = response.find("{")
        end = response.rfind("}")
        if start < 0 or end < 0:
            return None
        try:
            data = json.loads(response[start : end + 1])
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _strip_frontmatter(text: str) -> str:
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 3:
                return parts[2].strip()
        return text.strip()

    @staticmethod
    def _write_entity_page(
        target_dir: Path, entity: str, kind: str, parsed: dict,
        knowledge_id: str, ingested_at: str, is_update: bool,
    ) -> None:
        slug = slugify(entity)
        path = target_dir / f"{slug}.md"
        body_lines = [f"# {entity}", "", parsed.get("summary", ""), ""]
        if parsed.get("facts"):
            body_lines.append("## Facts")
            body_lines.append("")
            for f in parsed["facts"]:
                body_lines.append(f"- {f}")
            body_lines.append("")
        if parsed.get("contradictions"):
            body_lines.append("## Contradictions")
            body_lines.append("")
            for c in parsed["contradictions"]:
                body_lines.append(f"> [CONTRADICTION] {c}")
            body_lines.append("")
        frontmatter = {
            "title": entity,
            "kind": kind,
            "knowledge_id": knowledge_id,
            "ingested_at": ingested_at,
            "updated": is_update,
        }
        write_markdown(path, frontmatter, "\n".join(body_lines))
```

- [ ] **Step 4: 验证通过**

Run: `python -m pytest tests/test_wiki_entity_updater.py -v`
Expected: PASS(5 passed)

- [ ] **Step 5: Commit**

```bash
git add src/services/wiki_entity_updater.py tests/test_wiki_entity_updater.py
git commit -m "feat(knowledge-base): add wiki_entity_updater (LLM, max 3 calls/ingest)"
```

---

## Task 4: `WikiIndexCompiler` — `wiki/index.md` 生成器

对应 spec §6.2 任务 2.3。

**Files:**
- Create: `src/services/wiki_index_compiler.py`
- Test: `tests/test_wiki_index_compiler.py`

**Interfaces:**
- Consumes: `Config.get("knowledge_workflow.wiki_dir", "wiki")`;扫描子目录 `sources/entities/concepts/comparisons/syntheses`
- Produces: `WikiIndexCompiler().refresh() -> dict` → `{"status","path","page_count"}`

- [ ] **Step 1: 写失败测试**

`tests/test_wiki_index_compiler.py`:

```python
"""WikiIndexCompiler 测试。"""
from pathlib import Path

import pytest

from src.services.wiki_index_compiler import WikiIndexCompiler
from src.services.wiki_slug import write_markdown


@pytest.fixture
def wiki_with_pages(tmp_path, monkeypatch):
    wiki = tmp_path / "wiki"
    (wiki / "sources").mkdir(parents=True)
    (wiki / "entities").mkdir(parents=True)
    write_markdown(wiki / "sources" / "api.md", {"title": "API Overview"}, "# API")
    write_markdown(wiki / "sources" / "llm.md", {"title": "LLM Basics"}, "# LLM")
    write_markdown(wiki / "entities" / "foo.md", {"title": "Foo"}, "# Foo")
    monkeypatch.setattr(
        "src.services.wiki_index_compiler.Config.get",
        lambda key, default=None: {"knowledge_workflow.wiki_dir": str(wiki)}.get(key, default),
    )
    return wiki


def test_refresh_generates_index(wiki_with_pages):
    result = WikiIndexCompiler().refresh()
    assert result["status"] == "compiled"
    assert result["page_count"] == 3
    idx = Path(result["path"])
    assert idx.exists()
    text = idx.read_text(encoding="utf-8")
    assert "Sources" in text
    assert "Entities" in text
    assert "API Overview" in text
    assert "LLM Basics" in text
    assert "Foo" in text


def test_refresh_groups_by_type(wiki_with_pages):
    WikiIndexCompiler().refresh()
    text = (wiki_with_pages / "index.md").read_text(encoding="utf-8")
    # Sources 段下有 2 个,Entities 段下有 1 个
    sources_section = text.split("## Sources")[1].split("## Entities")[0]
    entities_section = text.split("## Entities")[1].split("## Concepts")[0]
    assert sources_section.count("- [") == 2
    assert entities_section.count("- [") == 1


def test_refresh_empty_wiki(tmp_path, monkeypatch):
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    monkeypatch.setattr(
        "src.services.wiki_index_compiler.Config.get",
        lambda key, default=None: {"knowledge_workflow.wiki_dir": str(wiki)}.get(key, default),
    )
    result = WikiIndexCompiler().refresh()
    assert result["page_count"] == 0
    text = (wiki / "index.md").read_text(encoding="utf-8")
    assert "_(none)_" in text  # 各段空占位


def test_refresh_idempotent(wiki_with_pages):
    c = WikiIndexCompiler()
    r1 = c.refresh()
    r2 = c.refresh()
    assert r1["page_count"] == r2["page_count"] == 3
```

- [ ] **Step 2: 验证失败**

Run: `python -m pytest tests/test_wiki_index_compiler.py -v`
Expected: FAIL — `ModuleNotFoundError: ... wiki_index_compiler`

- [ ] **Step 3: 实现**

`src/services/wiki_index_compiler.py`:

```python
"""``wiki/index.md`` 生成器:按 page type 分组聚合所有 wiki 页。全量重建。"""
from __future__ import annotations

from pathlib import Path

from src.services.wiki_slug import read_frontmatter, write_markdown
from src.utils.config import Config

PAGE_TYPE_DIRS = ["sources", "entities", "concepts", "comparisons", "syntheses"]
PAGE_TYPE_LABELS = {
    "sources": "Sources",
    "entities": "Entities",
    "concepts": "Concepts",
    "comparisons": "Comparisons",
    "syntheses": "Syntheses",
}


class WikiIndexCompiler:
    def refresh(self) -> dict:
        """扫描 wiki 子目录,全量重建 ``wiki/index.md``。"""
        wiki_dir = Path(Config.get("knowledge_workflow.wiki_dir", "wiki"))
        wiki_dir.mkdir(parents=True, exist_ok=True)
        sections: list[tuple[str, list[tuple[str, str]]]] = []
        total = 0
        for ptype in PAGE_TYPE_DIRS:
            label = PAGE_TYPE_LABELS[ptype]
            sub = wiki_dir / ptype
            entries: list[tuple[str, str]] = []
            if sub.is_dir():
                for md in sorted(sub.glob("*.md")):
                    fm = read_frontmatter(md)
                    title = fm.get("title") or md.stem
                    rel = md.relative_to(wiki_dir).as_posix()
                    entries.append((title, rel))
            total += len(entries)
            sections.append((label, entries))
        body = self._render(sections)
        index_path = wiki_dir / "index.md"
        write_markdown(index_path, {"generated": True}, body)
        return {"status": "compiled", "path": str(index_path), "page_count": total}

    @staticmethod
    def _render(sections: list[tuple[str, list[tuple[str, str]]]]) -> str:
        lines = ["# Wiki Index", ""]
        for label, entries in sections:
            lines.append(f"## {label}")
            lines.append("")
            if not entries:
                lines.append("_(none)_")
            else:
                for title, rel in entries:
                    lines.append(f"- [{title}]({rel})")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"
```

- [ ] **Step 4: 验证通过**

Run: `python -m pytest tests/test_wiki_index_compiler.py -v`
Expected: PASS(4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/services/wiki_index_compiler.py tests/test_wiki_index_compiler.py
git commit -m "feat(knowledge-base): add wiki_index_compiler (index.md generator)"
```

---

## Task 5: `WikiLogCompiler` — `wiki/log.md` 生成器

对应 spec §6.2 任务 2.4。

**Files:**
- Create: `src/services/wiki_log_compiler.py`
- Test: `tests/test_wiki_log_compiler.py`

**Interfaces:**
- Consumes: `Config.get("knowledge_workflow.wiki_dir", "wiki")`
- Produces:
  - `WikiLogCompiler().append(event: dict) -> dict`,`event = {"type","target","timestamp","detail"}` → `{"status","path"}`
  - `WikiLogCompiler().rebuild(events: list[dict]) -> dict` → `{"status","path","entries"}`

- [ ] **Step 1: 写失败测试**

`tests/test_wiki_log_compiler.py`:

```python
"""WikiLogCompiler 测试。"""
from pathlib import Path

import pytest

from src.services.wiki_log_compiler import WikiLogCompiler


@pytest.fixture
def wiki_dir(tmp_path, monkeypatch):
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    monkeypatch.setattr(
        "src.services.wiki_log_compiler.Config.get",
        lambda key, default=None: {"knowledge_workflow.wiki_dir": str(wiki)}.get(key, default),
    )
    return wiki


def test_append_writes_entry(wiki_dir):
    c = WikiLogCompiler()
    result = c.append({
        "type": "ingest", "target": "API Overview",
        "timestamp": "2026-07-02T10:00:00", "detail": "compiled kid-1",
    })
    assert result["status"] == "appended"
    text = (wiki_dir / "log.md").read_text(encoding="utf-8")
    assert "ingest" in text
    assert "API Overview" in text
    assert "2026-07-02T10:00:00" in text


def test_append_creates_header(wiki_dir):
    c = WikiLogCompiler()
    c.append({"type": "ingest", "target": "T", "timestamp": "ts1", "detail": "d"})
    text = (wiki_dir / "log.md").read_text(encoding="utf-8")
    assert text.startswith("# Wiki Log")


def test_append_dedup(wiki_dir):
    c = WikiLogCompiler()
    ev = {"type": "ingest", "target": "T", "timestamp": "ts1", "detail": "d"}
    r1 = c.append(ev)
    r2 = c.append(ev)
    assert r1["status"] == "appended"
    assert r2["status"] == "duplicate"
    text = (wiki_dir / "log.md").read_text(encoding="utf-8")
    assert text.count("**ingest**") == 1


def test_rebuild_sorts_and_dedups(wiki_dir):
    c = WikiLogCompiler()
    events = [
        {"type": "ingest", "target": "B", "timestamp": "2026-07-02T12:00:00", "detail": "d"},
        {"type": "ingest", "target": "A", "timestamp": "2026-07-02T10:00:00", "detail": "d"},
        {"type": "ingest", "target": "A", "timestamp": "2026-07-02T10:00:00", "detail": "d"},  # dup
    ]
    result = c.rebuild(events)
    assert result["entries"] == 2
    text = (wiki_dir / "log.md").read_text(encoding="utf-8")
    assert text.index("2026-07-02T10:00:00") < text.index("2026-07-02T12:00:00")
```

- [ ] **Step 2: 验证失败**

Run: `python -m pytest tests/test_wiki_log_compiler.py -v`
Expected: FAIL — `ModuleNotFoundError: ... wiki_log_compiler`

- [ ] **Step 3: 实现**

`src/services/wiki_log_compiler.py`:

```python
"""``wiki/log.md`` 生成器:追加 ingest/query/lint 时间线。

幂等:同 ``(type,target,timestamp)`` 不重复(以 hash 注释标记)。
``rebuild`` 从事件列表全量重建(去重 + 按 timestamp 排序)。
时间戳由调用方传入。
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from src.utils.config import Config


class WikiLogCompiler:
    def append(self, event: dict) -> dict:
        """追加单条事件;同事件已存在则跳过。"""
        wiki_dir = Path(Config.get("knowledge_workflow.wiki_dir", "wiki"))
        wiki_dir.mkdir(parents=True, exist_ok=True)
        log_path = wiki_dir / "log.md"
        h = self._event_hash(event)
        existing = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
        if h in existing:
            return {"status": "duplicate", "path": str(log_path)}
        line = self._format(event, h)
        with log_path.open("a", encoding="utf-8") as f:
            if not existing:
                f.write("# Wiki Log\n\n")
            f.write(line + "\n")
        return {"status": "appended", "path": str(log_path)}

    def rebuild(self, events: list[dict]) -> dict:
        """从事件列表全量重建 log.md(去重 + 按 timestamp 排序)。"""
        wiki_dir = Path(Config.get("knowledge_workflow.wiki_dir", "wiki"))
        wiki_dir.mkdir(parents=True, exist_ok=True)
        log_path = wiki_dir / "log.md"
        seen: set[str] = set()
        unique: list[dict] = []
        for ev in events:
            h = self._event_hash(ev)
            if h in seen:
                continue
            seen.add(h)
            unique.append(ev)
        unique.sort(key=lambda e: e.get("timestamp", ""))
        lines = ["# Wiki Log", ""]
        for ev in unique:
            lines.append(self._format(ev, self._event_hash(ev)))
        lines.append("")
        log_path.write_text("\n".join(lines), encoding="utf-8")
        return {"status": "rebuilt", "path": str(log_path), "entries": len(unique)}

    @staticmethod
    def _format(event: dict, h: str) -> str:
        etype = event.get("type", "event")
        target = event.get("target", "")
        ts = event.get("timestamp", "")
        detail = event.get("detail", "")
        return f"- [{ts}] **{etype}**: {target} — {detail} <!-- {h} -->"

    @staticmethod
    def _event_hash(event: dict) -> str:
        key = f"{event.get('type')}|{event.get('target')}|{event.get('timestamp')}"
        return hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]
```

- [ ] **Step 4: 验证通过**

Run: `python -m pytest tests/test_wiki_log_compiler.py -v`
Expected: PASS(4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/services/wiki_log_compiler.py tests/test_wiki_log_compiler.py
git commit -m "feat(knowledge-base): add wiki_log_compiler (log.md append/rebuild)"
```

---

## Task 6: `KnowledgeWorkflowService` 编排 + AppContainer 注入 + path_indexer 钩子

对应 spec §6.2 任务 2.5。**本任务涉及现有符号,实现前先 gitnexus impact。**

**Files:**
- Create: `src/services/knowledge_workflow.py`
- Modify: `src/core/container.py`(加字段 + lazy property,参考 `:106` `_wiki_compiler` / `:188` `wiki_compiler` property)
- Modify: `src/services/path_indexer.py:325-397`(`_ingest_file`)+ `:399-440`(`_reingest_file`)
- Test: `tests/test_knowledge_workflow.py`

**Interfaces:**
- Consumes: 4 个编译器(均 `__init__` 可注入);`Config.get("knowledge_workflow.mode", "legacy")`;`Database.get_knowledge()`
- Produces:
  - `KnowledgeWorkflowService(...).compile(knowledge_id, ingested_at=None) -> dict`
  - 模块级 `try_knowledge_workflow_compile(knowledge_id, ingested_at=None) -> dict | None`(非阻塞)

- [ ] **Step 0: gitnexus impact 评估**

Run impact on `_ingest_file` 与 `_reingest_file`(`PathIndexService`):

```
impact({target: "_ingest_file", direction: "upstream", repo: "ClaudeCodeWorkSpace"})
impact({target: "_reingest_file", direction: "upstream", repo: "ClaudeCodeWorkSpace"})
```

记录调用方数量与风险等级。若 HIGH/CRITICAL,先向用户报告再继续。预期调用方仅 `apply_diff`/`compute_diff` 内部,风险 LOW-MEDIUM。

- [ ] **Step 1: 写失败测试**

`tests/test_knowledge_workflow.py`:

```python
"""KnowledgeWorkflowService 编排器 + path_indexer e2e(S2)。"""
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.services.db import Database
from src.services.knowledge_workflow import (
    KnowledgeWorkflowService,
    try_knowledge_workflow_compile,
)


class FakeCompilers:
    """四个全 mock 的编译器,记录调用。"""

    def __init__(self):
        self.source = MagicMock()
        self.source.compile.return_value = {
            "status": "compiled", "key_entities": ["A", "B"], "summary": "s", "title": "T",
        }
        self.entity = MagicMock()
        self.entity.update.return_value = {
            "entities_created": 2, "concepts_created": 0, "llm_calls": 2, "contradictions": [],
        }
        self.index = MagicMock()
        self.index.refresh.return_value = {"status": "compiled", "page_count": 1}
        self.log = MagicMock()
        self.log.append.return_value = {"status": "appended"}


@pytest.fixture
def knowledge_in_db(tmp_path):
    Database.reset_instance()
    db = Database(str(tmp_path / "kb.db"))
    Database._instance = db
    conn = db.get_conn()
    conn.execute(
        """INSERT INTO knowledge_items
           (id, title, content, source_type, source_path, file_type,
            content_hash, tags, version, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        ("kid-1", "T", "# T\nbody", "file", str(tmp_path / "f.md"), "md",
         "h1", "[]", 1, "2026-07-02T10:00:00", "2026-07-02T10:00:00"),
    )
    conn.commit()
    yield
    Database.reset_instance()


def _wiki_first_config(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "src.services.knowledge_workflow.Config.get",
        lambda key, default=None: {
            "knowledge_workflow.mode": "wiki_first",
        }.get(key, default),
    )


def test_compile_wiki_first_triggers_all(knowledge_in_db, monkeypatch, tmp_path):
    _wiki_first_config(monkeypatch, tmp_path)
    fakes = FakeCompilers()
    svc = KnowledgeWorkflowService(
        source_compiler=fakes.source, entity_updater=fakes.entity,
        index_compiler=fakes.index, log_compiler=fakes.log,
    )
    result = svc.compile("kid-1", ingested_at="2026-07-02T10:00:00")
    assert result["mode"] == "wiki_first"
    fakes.source.compile.assert_called_once_with("kid-1", "2026-07-02T10:00:00")
    fakes.entity.update.assert_called_once()
    fakes.index.refresh.assert_called_once()
    fakes.log.append.assert_called_once()


def test_compile_legacy_skips(knowledge_in_db, monkeypatch):
    monkeypatch.setattr(
        "src.services.knowledge_workflow.Config.get",
        lambda key, default=None: {"knowledge_workflow.mode": "legacy"}.get(key, default),
    )
    fakes = FakeCompilers()
    svc = KnowledgeWorkflowService(
        source_compiler=fakes.source, entity_updater=fakes.entity,
        index_compiler=fakes.index, log_compiler=fakes.log,
    )
    result = svc.compile("kid-1", ingested_at="2026-07-02T10:00:00")
    assert result["skipped"] is True
    fakes.source.compile.assert_not_called()


def test_compile_isolates_failure(knowledge_in_db, monkeypatch, tmp_path):
    _wiki_first_config(monkeypatch, tmp_path)
    fakes = FakeCompilers()
    fakes.source.compile.side_effect = RuntimeError("boom")
    svc = KnowledgeWorkflowService(
        source_compiler=fakes.source, entity_updater=fakes.entity,
        index_compiler=fakes.index, log_compiler=fakes.log,
    )
    result = svc.compile("kid-1", ingested_at="2026-07-02T10:00:00")  # 不抛
    assert result["errors"]  # 收集了错误
    fakes.index.refresh.assert_called_once()  # 后续阶段继续


def test_compile_not_found(monkeypatch, tmp_path):
    _wiki_first_config(monkeypatch, tmp_path)
    Database.reset_instance()
    db = Database(str(tmp_path / "empty.db"))
    Database._instance = db
    fakes = FakeCompilers()
    svc = KnowledgeWorkflowService(
        source_compiler=fakes.source, entity_updater=fakes.entity,
        index_compiler=fakes.index, log_compiler=fakes.log,
    )
    result = svc.compile("ghost", ingested_at="2026-07-02T10:00:00")
    assert result.get("skipped") is True or result.get("reason") == "not_found"


def test_try_hook_returns_none_without_container(knowledge_in_db, monkeypatch):
    """无 active container 时返回 None,不抛。"""
    monkeypatch.setattr(
        "src.core.container.get_active_container", lambda: None,
    )
    assert try_knowledge_workflow_compile("kid-1") is None
```

并新增 e2e 测试(验证 spec S2):

```python
def test_path_indexer_triggers_wiki_first_e2e(tmp_path, monkeypatch):
    """spec S2:ingest 后 wiki/sources/ + index.md + log.md 出现(e2e,不 mock 编译器)。"""
    import os

    from src.services.path_indexer import PathIndexService

    # 临时项目布局
    project = tmp_path / "proj"
    (project / "raw").mkdir(parents=True)
    src_file = project / "raw" / "doc.md"
    src_file.write_text("# Real Doc\n\nThe MCP and LLM APIs are documented.\n", encoding="utf-8")

    Database.reset_instance()
    db = Database(str(project / "data" / "kb.db"))
    Database._instance = db

    monkeypatch.setattr(
        "src.services.knowledge_workflow.Config.get",
        lambda key, default=None: {
            "knowledge_workflow.mode": "wiki_first",
            "knowledge_workflow.wiki_dir": str(project / "wiki"),
            "knowledge_workflow.source_summary_dir": str(project / "wiki" / "sources"),
            "knowledge_workflow.entity_dir": str(project / "wiki" / "entities"),
            "knowledge_workflow.concept_dir": str(project / "wiki" / "concepts"),
            "wiki.max_llm_calls_per_ingest": 0,  # 关掉 LLM,纯验证文件系统层
        }.get(key, default),
    )
    # entity_updater 无 LLM key 时会抛 → 被 KnowledgeWorkflowService 隔离
    monkeypatch.setattr(
        "src.services.wiki_source_compiler.Config.get",
        lambda key, default=None: {
            "knowledge_workflow.wiki_dir": str(project / "wiki"),
            "knowledge_workflow.source_summary_dir": str(project / "wiki" / "sources"),
        }.get(key, default),
    )

    svc = PathIndexService(db=db, config=MagicMock(), indexed_file_repo=MagicMock())
    kid = svc._ingest_file(src_file)

    # S2 三处产物
    sources = list((project / "wiki" / "sources").glob("*.md"))
    assert sources, "source summary 未生成"
    assert (project / "wiki" / "index.md").exists()
    assert (project / "wiki" / "log.md").exists()
    Database.reset_instance()
```

- [ ] **Step 2: 验证失败**

Run: `python -m pytest tests/test_knowledge_workflow.py -v`
Expected: FAIL — `ModuleNotFoundError: ... knowledge_workflow`

- [ ] **Step 3: 实现 `knowledge_workflow.py`**

```python
"""KnowledgeWorkflowService — wiki-first 文件系统层编排器。

mode=wiki_first 时,ingest 后编排 source/entity/index/log 四个编译器。
失败隔离(每步 try/except),整体不抛。时间戳由调用方传入(可复现)。
"""
from __future__ import annotations

import logging

from src.services.db import Database
from src.services.wiki_entity_updater import WikiEntityUpdater
from src.services.wiki_index_compiler import WikiIndexCompiler
from src.services.wiki_log_compiler import WikiLogCompiler
from src.services.wiki_source_compiler import WikiSourceCompiler
from src.utils.config import Config

logger = logging.getLogger(__name__)


class KnowledgeWorkflowService:
    def __init__(
        self,
        source_compiler: WikiSourceCompiler | None = None,
        entity_updater: WikiEntityUpdater | None = None,
        index_compiler: WikiIndexCompiler | None = None,
        log_compiler: WikiLogCompiler | None = None,
    ):
        self._source = source_compiler or WikiSourceCompiler()
        self._entity = entity_updater or WikiEntityUpdater()
        self._index = index_compiler or WikiIndexCompiler()
        self._log = log_compiler or WikiLogCompiler()

    def compile(self, knowledge_id: str, ingested_at: str | None = None) -> dict:
        """编排 wiki-first 编译。失败隔离,不抛。"""
        mode = Config.get("knowledge_workflow.mode", "legacy")
        if mode != "wiki_first":
            return {"mode": mode, "skipped": True}

        item = Database.get_knowledge(knowledge_id)
        if not item:
            return {"mode": mode, "skipped": True, "reason": "not_found"}
        ts = ingested_at or item.get("created_at") or ""

        result: dict = {"mode": mode, "errors": []}

        try:
            src = self._source.compile(knowledge_id, ts)
            result["source"] = src
        except Exception as e:
            logger.warning("source compile failed (%s): %s", knowledge_id, e)
            result["errors"].append({"stage": "source", "error": str(e)})
            src = {}

        try:
            ent = self._entity.update(knowledge_id, self._as_entity_input(src, item), ts)
            result["entity"] = ent
        except Exception as e:
            logger.warning("entity update failed (%s): %s", knowledge_id, e)
            result["errors"].append({"stage": "entity", "error": str(e)})

        try:
            result["index"] = self._index.refresh()
        except Exception as e:
            logger.warning("index refresh failed (%s): %s", knowledge_id, e)
            result["errors"].append({"stage": "index", "error": str(e)})

        try:
            log_ev = {
                "type": "ingest",
                "target": item.get("title", knowledge_id),
                "timestamp": ts,
                "detail": f"compiled {knowledge_id}",
            }
            result["log"] = self._log.append(log_ev)
        except Exception as e:
            logger.warning("log append failed (%s): %s", knowledge_id, e)
            result["errors"].append({"stage": "log", "error": str(e)})

        return result

    @staticmethod
    def _as_entity_input(src: dict, item: dict) -> dict:
        return {
            "key_entities": src.get("key_entities", []),
            "title": item.get("title", ""),
            "summary": src.get("summary", ""),
        }


def try_knowledge_workflow_compile(
    knowledge_id: str, ingested_at: str | None = None
) -> dict | None:
    """非阻塞钩子:从 active container 取服务并编译。失败返回 None。"""
    try:
        from src.core.container import get_active_container

        container = get_active_container()
        if container is None:
            return None
        return container.knowledge_workflow.compile(knowledge_id, ingested_at)
    except Exception as e:
        logger.warning("knowledge workflow compile failed (%s): %s", knowledge_id, e)
        return None
```

- [ ] **Step 4: 注入 AppContainer**

Modify `src/core/container.py`:

4a. 在 `_path_indexer` 字段后(`:132` 附近)加字段:

```python
    # --- W2: wiki-first 编排 ---
    _knowledge_workflow: Optional[object] = field(default=None, repr=False)
```

4b. 在 `path_indexer` property 之后(`:339` 之后)加 property:

```python
    @property
    def knowledge_workflow(self):
        if self._knowledge_workflow is None:
            from src.services.knowledge_workflow import KnowledgeWorkflowService
            self._knowledge_workflow = KnowledgeWorkflowService()
            self._track_service("_knowledge_workflow")
        return self._knowledge_workflow
```

- [ ] **Step 5: 挂 path_indexer 钩子**

Modify `src/services/path_indexer.py`:

`_ingest_file`(`:396-397`)—— 在 `index_knowledge_item(item)` 之后、`return item_id` 之前插入:

```python
        index_knowledge_item(item)

        # wiki-first 钩子:编译文件系统 wiki 层(失败不阻塞索引)
        try:
            from src.services.knowledge_workflow import try_knowledge_workflow_compile
            try_knowledge_workflow_compile(item_id, ingested_at=item.created_at)
        except Exception as e:
            logger.warning("wiki-first hook failed for %s: %s", item_id, e)

        return item_id
```

`_reingest_file`(`:437-438`)—— 在 `index_knowledge_item(item)` 之后、`return existing_kid` 之前插入相同钩子:

```python
            index_knowledge_item(item)

            # wiki-first 钩子(更新路径同样触发)
            try:
                from src.services.knowledge_workflow import try_knowledge_workflow_compile
                try_knowledge_workflow_compile(existing_kid, ingested_at=item.created_at)
            except Exception as e:
                logger.warning("wiki-first hook failed for %s: %s", existing_kid, e)

            return existing_kid
```

> 注意:去重短路(`_ingest_file` line 346-349 命中 existing 直接 return)不触发编译 —— 复用已有 source summary,符合幂等。

- [ ] **Step 6: 验证通过**

Run: `python -m pytest tests/test_knowledge_workflow.py -v`
Expected: PASS(6 passed,含 S2 e2e)

- [ ] **Step 7: detect_changes 确认影响范围**

```
detect_changes({scope: "unstaged", repo: "ClaudeCodeWorkSpace"})
```

确认影响的符号仅 `PathIndexService._ingest_file`/`_reingest_file` 与新增文件,无意外传播。

- [ ] **Step 8: Commit**

```bash
git add src/services/knowledge_workflow.py src/core/container.py src/services/path_indexer.py tests/test_knowledge_workflow.py
git commit -m "feat(knowledge-base): add KnowledgeWorkflowService + path_indexer wiki-first hook"
```

---

## W2 阶段验收(阶段审查检查点)

**W2 Definition of Done**(对应 spec §3 成功标准 S2):

- [ ] 6 个新模块测试全绿:`python -m pytest tests/test_wiki_slug.py tests/test_wiki_source_compiler.py tests/test_wiki_entity_updater.py tests/test_wiki_index_compiler.py tests/test_wiki_log_compiler.py tests/test_knowledge_workflow.py -v`
- [ ] spec S2 e2e 通过(ingest → wiki/sources + index.md + log.md 三处产物)
- [ ] 全量测试无回归(基线 1051 passed, 1 skipped):`python -m pytest tests/ -q`
- [ ] `detect_changes()` 影响范围符合预期(仅 path_indexer 钩子点 + 新增文件)
- [ ] 阶段审查报告:提交记录、测试计数、S2 验证结果,等待批准进入 W3

**风险复核**:
- 编译失败是否阻塞检索?—— 否(KnowledgeWorkflowService 全程 try/except,钩子 `try_knowledge_workflow_compile` 再包一层)
- LLM 成本?—— entity_updater `max_llm_calls_per_ingest=3` 硬上限;source/index/log 零 LLM
- 可复现性?—— 时间戳全由调用方传入(item.created_at),编译器内无系统时间
