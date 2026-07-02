# knowledge-base Karpathy Wiki-First W4 实施计划(收口阶段)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 收口 —— README/config 一致;`shinehe migrate` 把老项目切到 wiki-first;wiki-compilation eval 在 fixture 上产出 5 项指标。完成后全量回归 + push 主分支。

**Architecture:** W1-W3 已建完 wiki-first 全链路。W4 是收尾:文档对齐 + 迁移工具 + 评测脚本。纯增量,不改 W1-W3 已验证的核心逻辑。

---

## Global Constraints

- Python `python`;4 空格缩进;snake_case;`from __future__ import annotations`。
- 改现有符号前 `gitnexus impact`;不破坏基线(1092 passed, 1 skipped)。
- `feat(knowledge-base):` 提交规范。

---

## Task 1: README/config 文档一致性

对应 spec §6.4 任务 4.1。**确认的不一致**:`README.md:103` 说 "Default `core` profile",但 `:138`/`:12` badge/`config.example.yaml` 均为 `extended`。

**Files:**
- Modify: `README.md:103`
- Modify: `README_zh.md`(对应中文处,若同样不一致)
- Test: `tests/test_docs_consistency.py`(新建)

- [ ] **Step 1: 写失败测试**

`tests/test_docs_consistency.py`:

```python
"""文档与 config.example.yaml 一致性测试(spec S7)。"""
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _example_config():
    with open(ROOT / "config.example.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_readme_default_profile_matches_config():
    """README 不再说 'Default core profile'(应为 extended)。"""
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "Default `core` profile" not in text, "README 仍称默认 core profile"
    assert "extended" in text


def test_readme_profile_mentioned_matches_config():
    """README MCP Tool Profiles 段描述的默认 profile 与 config.example 一致。"""
    cfg = _example_config()
    expected = cfg["mcp"]["tool_profile"]  # extended
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    # README 应在某处说明默认是 expected profile
    assert expected in text, f"README 未提及默认 profile '{expected}'"
```

- [ ] **Step 2: 验证失败**

Run: `python -m pytest tests/test_docs_consistency.py -v`
Expected: FAIL — `test_readme_default_profile_matches_config`(README:103 仍含 "Default `core` profile")

- [ ] **Step 3: 修 README.md:103**

Edit `README.md:102-103`:

```
### MCP Tool Profiles
Default `extended` profile exposes 20 tools (10 core read tools + Query DSL, source graph, async ingest). Switch to `core`, `admin`, `full`, or `legacy` via `config.yaml`.
```

并检查 `README_zh.md` 对应处(grep "默认.*core\|core.*profile"),若同样不一致则修正为 extended。

- [ ] **Step 4: 验证通过 + commit**

Run: `python -m pytest tests/test_docs_consistency.py -v` → PASS

```bash
git add README.md README_zh.md tests/test_docs_consistency.py
git commit -m "docs(knowledge-base): fix README default profile (core→extended) + consistency test"
```

---

## Task 2: `shinehe migrate` 迁移工具

对应 spec §6.4 任务 4.2、§10。**涉及 cli.main,先 impact。**

**Files:**
- Create: `src/services/migrator.py`(`MigrationService`)
- Modify: `src/cli.py`(加 `migrate` 子命令)
- Test: `tests/test_migrator.py`

**Interfaces:**
- Produces:
  - `MigrationService(plan_only=True).plan() -> dict`(扫描 data/,输出迁移计划,不写盘)
  - `MigrationService().apply(backup=True) -> dict`(备份 data/ → 导出源到 raw/ → 重编译 → 切 mode)

- [ ] **Step 0: impact**

```
impact({target: "main", direction: "upstream", repo: "ClaudeCodeWorkSpace", file_path: "projects/knowledge-base/src/cli.py"})
```

- [ ] **Step 1: 写失败测试**

`tests/test_migrator.py`:

```python
"""MigrationService 测试(spec §10)。"""
from pathlib import Path

import pytest
import yaml

from src.services.db import Database
from src.services.migrator import MigrationService
from src.utils.config import Config


def _insert_file_knowledge(kid, source_path, content="doc body"):
    Database.insert_knowledge({
        "id": kid, "title": "T", "content": content,
        "source_type": "file", "source_path": source_path, "file_type": "md",
        "file_size": len(content), "content_hash": "h", "file_created_at": "",
        "file_modified_at": "", "tags": "[]", "version": 1,
        "created_at": "2026-07-01T00:00:00", "updated_at": "2026-07-01T00:00:00",
    })


def test_plan_scans_knowledge_without_writing(tmp_path, monkeypatch):
    """plan() 只扫描,不写盘。"""
    # 准备:一个真实源文件 + 对应 knowledge
    raw_file = tmp_path / "original.md"
    raw_file.write_text("# Doc\ndoc body", encoding="utf-8")
    _insert_file_knowledge("k1", str(raw_file))

    monkeypatch.setattr("src.services.migrator.Config.get",
                        lambda key, default=None: {
                            "knowledge_workflow.raw_dir": str(tmp_path / "raw"),
                            "storage.data_dir": str(tmp_path / "data"),
                        }.get(key, default))
    svc = MigrationService(project_dir=tmp_path)
    plan = svc.plan()
    assert plan["knowledge_count"] >= 1
    assert "actions" in plan
    # plan 不写盘
    assert not (tmp_path / "raw").exists() or not list((tmp_path / "raw").glob("*"))


def test_apply_exports_sources_and_backs_up_data(tmp_path, monkeypatch):
    """apply() 备份 data/ + 导出源到 raw/。"""
    raw_file = tmp_path / "original.md"
    raw_file.write_text("# Doc\ndoc body", encoding="utf-8")
    _insert_file_knowledge("k1", str(raw_file))

    monkeypatch.setattr("src.services.migrator.Config.get",
                        lambda key, default=None: {
                            "knowledge_workflow.raw_dir": str(tmp_path / "raw"),
                            "knowledge_workflow.mode": "legacy",
                        }.get(key, default))
    svc = MigrationService(project_dir=tmp_path)
    result = svc.apply()
    assert result["exported"] >= 1
    assert (tmp_path / "raw" / "original.md").exists()  # 源已导出
    assert result["backup_created"] is True


def test_apply_skips_missing_source_files(tmp_path, monkeypatch):
    """source_path 指向不存在的文件 → 跳过(不计入 exported)。"""
    _insert_file_knowledge("k1", str(tmp_path / "ghost.md"))  # 不存在
    monkeypatch.setattr("src.services.migrator.Config.get",
                        lambda key, default=None: {
                            "knowledge_workflow.raw_dir": str(tmp_path / "raw"),
                            "knowledge_workflow.mode": "legacy",
                        }.get(key, default))
    svc = MigrationService(project_dir=tmp_path)
    result = svc.apply()
    assert result["exported"] == 0
    assert result["skipped_missing"] >= 1
```

- [ ] **Step 2: 验证失败**

Run: `python -m pytest tests/test_migrator.py -v`
Expected: FAIL — `ModuleNotFoundError: ... migrator`

- [ ] **Step 3: 实现 `src/services/migrator.py`**

```python
"""MigrationService — 把 legacy 项目迁移到 wiki-first。

流程(spec §10):
1. plan()/--dry-run:扫描 data/ knowledge,输出计划(导出哪些源、重编译哪些),不写盘
2. apply()/--apply:备份 data/ → 按 source_path 导出源到 raw/ → 触发 wiki 重编译 → 切 mode

不删除 data/,双轨过渡;失败可从备份回滚。
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

from src.services.db import Database
from src.utils.config import Config

logger = logging.getLogger(__name__)


class MigrationService:
    def __init__(self, project_dir: Path | None = None):
        self.project_dir = Path(project_dir) if project_dir else Path.cwd()

    def plan(self) -> dict:
        """扫描 knowledge,输出迁移计划(不写盘)。"""
        items = Database.list_knowledge(limit=10000)
        actions = []
        for it in items:
            if it.get("source_type") != "file":
                continue
            sp = it.get("source_path", "")
            exists = bool(sp) and Path(sp).exists()
            actions.append({
                "knowledge_id": it["id"],
                "title": it.get("title", ""),
                "source_path": sp,
                "source_exists": exists,
                "action": "export" if exists else "skip_missing",
            })
        return {
            "knowledge_count": len(items),
            "exportable": sum(1 for a in actions if a["action"] == "export"),
            "actions": actions,
        }

    def apply(self, backup: bool = True) -> dict:
        """备份 data/ + 导出源到 raw/ + 触发重编译 + 切 mode。"""
        raw_dir = Path(Config.get("knowledge_workflow.raw_dir", "raw"))
        raw_dir.mkdir(parents=True, exist_ok=True)

        data_dir = self.project_dir / Config.get("storage.data_dir", "data")
        backup_created = False
        if backup and data_dir.exists():
            backup_path = data_dir.parent / f"{data_dir.name}.backup"
            if backup_path.exists():
                shutil.rmtree(backup_path)
            shutil.copytree(data_dir, backup_path)
            backup_created = True
            logger.info("data/ backed up to %s", backup_path)

        exported = 0
        skipped_missing = 0
        items = Database.list_knowledge(limit=10000)
        for it in items:
            if it.get("source_type") != "file":
                continue
            sp = it.get("source_path", "")
            if not sp or not Path(sp).exists():
                skipped_missing += 1
                continue
            src = Path(sp)
            dest = raw_dir / src.name
            # 同名冲突:加 knowledge_id 短缀
            if dest.exists() and dest.read_bytes() != src.read_bytes():
                dest = raw_dir / f"{src.stem}-{it['id'][:8]}{src.suffix}"
            shutil.copy2(src, dest)
            exported += 1

        # 触发 wiki 重编译(每个 knowledge)
        recompiled = 0
        try:
            from src.services.knowledge_workflow import try_knowledge_workflow_compile
            for it in items:
                if it.get("source_type") == "file":
                    try_knowledge_workflow_compile(
                        it["id"], ingested_at=it.get("created_at", "")
                    )
                    recompiled += 1
        except Exception as e:
            logger.warning("recompile during migrate failed: %s", e)

        return {
            "exported": exported,
            "skipped_missing": skipped_missing,
            "recompiled": recompiled,
            "backup_created": backup_created,
        }
```

- [ ] **Step 4: cli.py 加 `migrate` 子命令**

在 `_handle_wiki` 之后加 `_handle_migrate`:

```python
def _handle_migrate(args: argparse.Namespace) -> int:
    """处理 migrate 子命令:legacy → wiki_first。"""
    from src.services.migrator import MigrationService
    svc = MigrationService()
    if not args.apply:
        plan = svc.plan()
        print(f"[PLAN] knowledge: {plan['knowledge_count']}, 可导出: {plan['exportable']}")
        for a in plan["actions"][:20]:
            mark = "✓" if a["action"] == "export" else "✗"
            print(f"  {mark} {a['title'][:40]} → {a['source_path']}")
        if len(plan["actions"]) > 20:
            print(f"  ...(另有 {len(plan['actions']) - 20} 条)")
        print("\n使用 --apply 执行迁移(将备份 data/、导出源到 raw/、重编译 wiki)")
        return 0
    result = svc.apply(backup=not args.no_backup)
    print(f"[OK] 导出 {result['exported']} 源,跳过 {result['skipped_missing']},"
          f"重编译 {result['recompiled']},备份={'是' if result['backup_created'] else '否'}")
    return 0
```

在 subparsers 区(wiki_parser 之后)加:

```python
    # --- migrate ---
    migrate_parser = subparsers.add_parser(
        "migrate", help="迁移 legacy 项目到 wiki-first",
        description="扫描 data/ 知识,导出源到 raw/,触发 wiki 重编译。默认 dry-run。",
    )
    migrate_parser.add_argument("--apply", action="store_true", help="执行迁移(默认仅计划)")
    migrate_parser.add_argument("--no-backup", action="store_true", help="apply 时跳过 data/ 备份")
```

handlers dict 加 `"migrate": _handle_migrate`。

- [ ] **Step 5: 验证通过 + commit**

Run: `python -m pytest tests/test_migrator.py tests/test_cli.py -v` → PASS

```bash
git add src/services/migrator.py src/cli.py tests/test_migrator.py
git commit -m "feat(knowledge-base): add shinehe migrate (legacy → wiki-first)"
```

---

## Task 3: wiki-compilation eval

对应 spec §6.4 任务 4.3、§3 S5。参考 `evals/run_retrieval_eval.py` 模式。

**Files:**
- Create: `evals/run_wiki_eval.py`
- Create: `evals/fixtures/wiki_sample.md`(若复用现有 fixture 则跳过)
- Test: `tests/test_wiki_eval.py`(验证指标计算)

**指标(spec §6.4 4.3)**:
- Source Coverage = 有 `wiki/sources/*.md` 的 knowledge 比例
- Cross-page Update Rate = 有 backlinks 的 wiki 页比例
- Orphan Page Rate = orphan wiki 页比例(来自 lint)
- Query Save Rate = `wiki/syntheses|comparisons/*.md` 数量(相对 knowledge 的比例)
- Stale Claim Ratio = outdated_claim findings 占 wiki 页比例

- [ ] **Step 1: 写失败测试(指标计算)**

`tests/test_wiki_eval.py`:

```python
"""wiki-compilation eval 指标计算测试(spec S5)。"""
from pathlib import Path

from evals.run_wiki_eval import compute_metrics


def test_source_coverage(tmp_path):
    wiki = tmp_path / "wiki"
    (wiki / "sources").mkdir(parents=True)
    (wiki / "sources" / "a.md").write_text("---\ntitle: A\n---\n", encoding="utf-8")
    metrics = compute_metrics(wiki_dir=wiki, knowledge_count=4, orphan_pages=0,
                              total_wiki_pages=1, outdated_claims=0,
                              query_save_pages=0, backlinked_pages=1)
    # 1 个 source / 4 个 knowledge → 0.25
    assert metrics["source_coverage"] == 0.25


def test_orphan_page_rate():
    metrics = compute_metrics(wiki_dir=Path("/nonexist"), knowledge_count=0,
                              orphan_pages=2, total_wiki_pages=5,
                              outdated_claims=0, query_save_pages=0,
                              backlinked_pages=3)
    assert metrics["orphan_page_rate"] == 0.4
    assert metrics["cross_page_update_rate"] == 0.6


def test_stale_claim_ratio():
    metrics = compute_metrics(wiki_dir=Path("/nonexist"), knowledge_count=0,
                              orphan_pages=0, total_wiki_pages=10,
                              outdated_claims=3, query_save_pages=0,
                              backlinked_pages=0)
    assert metrics["stale_claim_ratio"] == 0.3
```

- [ ] **Step 2: 验证失败**

Run: `python -m pytest tests/test_wiki_eval.py -v`
Expected: FAIL — `ModuleNotFoundError: evals.run_wiki_eval`

- [ ] **Step 3: 实现 `evals/run_wiki_eval.py`**

```python
"""wiki-compilation eval — 在 fixture 上产出 5 项指标(spec §6.4 4.3, S5)。

指标:
  - source_coverage:        wiki/sources/ 页数 / knowledge 总数
  - cross_page_update_rate: 有 backlinks 的 wiki 页 / wiki 页总数
  - orphan_page_rate:       orphan wiki 页 / wiki 页总数
  - query_save_rate:        wiki/(syntheses|comparisons)/ 页数 / knowledge 总数
  - stale_claim_ratio:      outdated_claim findings / wiki 页总数

Usage:
    python evals/run_wiki_eval.py --project /path/to/project
    python evals/run_wiki_eval.py --project . --output report.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def compute_metrics(
    wiki_dir: Path,
    knowledge_count: int,
    orphan_pages: int,
    total_wiki_pages: int,
    outdated_claims: int,
    query_save_pages: int,
    backlinked_pages: int,
) -> dict:
    """计算 5 项 wiki-compilation 指标(纯函数,可测试)。"""
    kc = max(knowledge_count, 1)
    twp = max(total_wiki_pages, 1)
    sources = 0
    if wiki_dir.exists():
        sources = len(list((wiki_dir / "sources").glob("*.md"))) if (wiki_dir / "sources").exists() else 0
    return {
        "source_coverage": round(sources / kc, 4),
        "cross_page_update_rate": round(backlinked_pages / twp, 4),
        "orphan_page_rate": round(orphan_pages / twp, 4),
        "query_save_rate": round(query_save_pages / kc, 4),
        "stale_claim_ratio": round(outdated_claims / twp, 4),
    }


def run_on_project(project_dir: Path) -> dict:
    """对一个已编译的 wiki-first 项目,从文件系统 + lint 提取指标。"""
    from src.utils.config import Config
    from src.services.wiki_lint import WikiLint

    Config.load()
    wiki_dir = project_dir / Config.get("knowledge_workflow.wiki_dir", "wiki")

    # knowledge 总数
    from src.services.db import Database
    knowledge_count = len(Database.list_knowledge(limit=10000))

    # lint 报告(orphan / outdated_claim / backlinks)
    report = WikiLint().run()
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="wiki-compilation eval")
    parser.add_argument("--project", default=".", help="项目根目录")
    parser.add_argument("--output", default=None, help="输出 JSON 报告路径")
    args = parser.parse_args(argv)

    metrics = run_on_project(Path(args.project))
    print("wiki-compilation metrics:")
    for k, v in metrics.items():
        print(f"  {k}: {v}")
    if args.output:
        Path(args.output).write_text(
            json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"\n报告已写入: {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: 验证通过 + 本地 smoke**

Run: `python -m pytest tests/test_wiki_eval.py -v` → PASS

本地 smoke(确认脚本可跑,不要求真实数据):
```bash
python evals/run_wiki_eval.py --project . --output /tmp/wiki_eval_report.json
```

- [ ] **Step 5: commit**

```bash
git add evals/run_wiki_eval.py tests/test_wiki_eval.py
git commit -m "feat(knowledge-base): add wiki-compilation eval (5 metrics)"
```

> CI nightly:spec 说不进 PR 门禁。`.github/workflows/ci.yml` 的 nightly 配置留作后续运维任务(本 plan 不强制改 CI,避免影响 PR 门禁稳定性)。

---

## W4 阶段验收 + 最终全量回归

**W4 DoD**(spec §3 S5、S7):

- [ ] 3 个 Task 测试全绿
- [ ] S5:run_wiki_eval 产出 5 项指标
- [ ] S7:README/config 一致性测试通过
- [ ] **最终全量回归**(基线 1092 passed, 1 skipped):`python -m pytest tests/ -q`
- [ ] 全部通过后 → `detect_changes()` 最终确认 → commit(若 W4 Task 间已逐个 commit 则此步汇总)→ push 主分支
