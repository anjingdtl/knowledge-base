# Canonical Wiki v2 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: 用 `superpowers:subagent-driven-development` 实现本计划（用户已选定 Subagent 驱动模式）。每个 Task 派 fresh subagent + 两阶段 review。步骤用 checkbox（`- [ ]`）跟踪。

**Goal:** 把双轨 Wiki（A 轨 SQLite `wiki_pages` / B 轨 FS `wiki/*.md`）收敛为「文件系统 Markdown 唯一 canonical + SQLite projection」，并引入 Claim 级证据链、跨来源合并、来源变更自动失效传播（spec v1.5.2 → v1.6.0）。

**Architecture:** Markdown canonical store 是唯一权威源；所有写操作经统一 `WikiRepository`；canonical 写成功后写 outbox，projection worker 幂等消费重建 SQLite v2 表；稳定 `page_id`（UUID）；Claim 用独立 YAML 文件，页面 frontmatter 通过 `claim_ids` 引用。

**Tech Stack:** Python 3.12 / dataclass + `to_row·from_row`（非 Pydantic）/ SQLite + FTS5 + sqlite-vec / alembic（手写，无 ORM）/ argparse CLI / yaml + jsonschema 契约（可选）。

---

## Scope 约定（用户决策）

本计划是 **「总纲 + Phase 0-1 优先」**：

- **总纲部分**（§架构决策 / §Phase 依赖图 / §文件结构总览 / §冲突处理 / §Phase 2-6 纲要 / §风险登记 / §DoD）：覆盖全 spec v1.6.0，固化所有架构决策与 Task 级路线（文件/接口契约/测试/验收/commit/依赖），指导后续展开。
- **bite-sized 部分**（§Phase 0 / §Phase 1）：每步 2-5 分钟、含完整代码、TDD、精确命令、独立 commit。**本轮立即执行**。
- **Phase 2-6**：执行到时再展开为 bite-sized（每个 Phase 动工前先出该 Phase 的 TDD 子计划并审批，沿用本文件 §Phase 2-6 纲要为骨架）。

执行模式：Subagent 驱动。隔离：`feature/canonical-wiki-v2` 分支（已建）。LLM：本机有可用 Key。

---

## Global Constraints（每个 Task 隐含遵守）

- **基线版本 v1.5.2，目标 v1.6.0**；`src/version.py` 是版本唯一来源（hatchling dynamic 读取），最后阶段才升。
- **不删除旧表**：`wiki_pages` / `wiki_links` / `wiki_ops_log` / `wiki_fts` / `wiki_workflow` / `wiki_page_versions` 全部保留，仅标 deprecated；新 v2 表与之并存。
- **不破坏 legacy**：`canonical_v2.enabled` 默认 `false`；功能门控关闭时 legacy 行为零变化（spec §3.2 / §13.3）。
- **TDD**：每 Task 先写失败测试再写实现；每 Task 独立 commit；commit message 用 spec §12 的建议。
- **命名**：Python `snake_case`，4 空格缩进；ruff `select=E,F,W,I` / `line-length=120`；mypy 非 strict 但 `warn_return_any=true`。
- **commit scope**：`feat(wiki-v2):` / `refactor(wiki-v2):` / `test(wiki-v2):` / `docs(wiki-v2):`（Conventional Commits，与项目历史一致）。
- **GitNexus 护栏**：改任何现有符号前先 `impact({target, direction:"upstream"})`；HIGH/CRITICAL 先警告；commit 前 `detect_changes()`。
- **路径安全**：canonical 路径必须在 `wiki_dir` 内；禁 `..`；slug + registry 双重校验（spec §14.2）。
- **不向 master 直接提交**；所有 commit 落 `feature/canonical-wiki-v2`。

---

## 现状基线（v1.5.2，三份 Explore 报告综合）

### 双轨架构（完全独立）

| 轨 | 入口 | 写入目标 | 写入方法 | page_id | claim_ids |
|---|---|---|---|---|---|
| **A 轨 SQLite** | `WikiCompiler`（774 行，LLM 重度） | `wiki_pages` 表 | `Database.insert_wiki_page(dict)` | 无（uuid4 行 id） | 无 |
| **B 轨 FS** | `KnowledgeWorkflowService` + 4 子编译器 | `wiki/<type>/*.md` | `wiki_slug.write_markdown`（原子） | 无（候选 id `wiki:<type>:<slug>`） | 无 |

`WikiWriteService`（v1.5.2）双写分发器，A/B 任一失败不阻塞另一个，**不保证一致性**。

### 关键事实

- `src/models/`：13 文件，统一 `@dataclass`+`to_row/from_row`；`Block.page_id` 已为 wiki 留钩；`EntityRef` 已泛化支持 wiki。**无 wiki_v2.py / Claim / Evidence 模型**。
- `src/services/db.py`（2508 行）：`Database` 元类单例，`_SCHEMA` 建表（全 `IF NOT EXISTS`），`_migrate()` 增量补列；6 张旧 wiki 表；有 `insert_wiki_page`/`search_wiki_fts`/`list_wiki_pages` 等。
- **`schema/` 目录不存在**，需从零创建（含 `schema/AGENTS.md`）。
- **alembic head = `i001_version_conflict`**（down_revision `h001_quality_score`）；命名 `<letter>001_<slug>`；env.py `target_metadata=None`（手写，无 autogenerate）；全 `if_not_exists=True`。
- `src/repositories/`：15 repo，统一 `__init__(db=None)`+`self._db or Database`+`_conn()` 骨架。
- 测试：约 1237 个；`conftest.api_client` fixture 强制 `wiki.enabled=False`（L70）；wiki 相关 17 文件。
- evals：3 个 `run_*.py`；**无 `run_knowledge_evolution_eval.py`**；**无 `artifacts/`**；基线 `evals/baselines/local.json`。
- config：无 `knowledge_workflow`/`canonical_v2`/`claims`/`rebuild`/`projection` 段；`knowledge_workflow.mode` 代码默认 `legacy`（**非 wiki_first**）。
- CLI：纯 argparse；`shinehe wiki` 仅有 `lint`/`save-answer`/`ingest-source`。
- 当前 wiki/ 目录布局：`sources/entities/concepts/comparisons/syntheses`（运行时懒创建）；**无 `claims`/`_meta`/`_staging`**。
- `PAGE_TYPE_DIRS`（5 元素，`wiki_index_compiler.py:9`）与 `WIKI_FIRST_DIRS`（8 元素，`project_setup.py:28-37`）是**两份独立真源**。

---

## 架构决策固化

### ADR-001：Markdown 为 Canonical Store

canonical = 页面 Markdown（`wiki/<type>/*.md`）+ Claim YAML（`wiki/claims/<claim_id>.yaml`）+ registry（`wiki/_meta/pages.json`、`redirects.json`）。页面 frontmatter 的 `claim_ids` 引用 Claim；正文用 `<!-- claim:<claim_id> -->` 锚点。SQLite 只存 projection。Git diff 即知识 diff。

### ADR-002：所有写操作经 WikiRepository（`src/services/wiki_repository.py`）

禁止 `WikiCompiler`/`KnowledgeWorkflowService`/`WikiEntityUpdater`/`WikiWriteService`/MCP/API/GUI 绕过 Repository 直接写 canonical。Repository 负责：schema 校验、revision 乐观锁、原子写、page/claim ID、staging、redirect、operation log、projection outbox。

### ADR-003：Projection 用 Outbox + 幂等重建

canonical 写成功 → 追加事件到 `data/wiki_projection_outbox.jsonl`（`page.*`/`claim.*`/`evidence.*`/`projection.rebuild_requested`）。projection worker 幂等消费；canonical 写成功不得因 projection 失败回滚；projection lag 进入 health。`shinehe wiki sync-index` 全量修复。

---

## Phase 依赖图与实施顺序（spec §19）

```
Phase 0 (基线+护栏)  ──风险低──┐
Phase 1 (模型+Repository) ─中──┤  地基，不切主路径
Phase 2 (SQLite projection) ─中─┤  新表并存
Phase 3 (Claim 抽取+合并) ─中高─┤  语义合并核心复杂点
Phase 4 (主工作流切换) ──高─────┤  主写路径切换，需强回归
Phase 5 (依赖图+失效传播) ─高───┤  影响范围大
Phase 6 (Migration+Lint+反馈+eval) ─中高─┘  真实评测+回滚
```

**铁律**（spec §19）：不得先做失效传播再补稳定 ID 和 Claim 模型，否则依赖图建立在不稳定对象上。每 Phase 未通过全量回归不得进下一 Phase。

---

## 文件结构总览（全 Phase）

### 新建文件

| Phase | 文件 | 职责 |
|---|---|---|
| 0 | `artifacts/eval/canonical-v2-baseline.json` | 基线指标快照 |
| 0 | `scripts/record_canonical_v2_baseline.py` | 可复现基线聚合脚本 |
| 0 | `tests/test_canonical_write_guards.py` | 架构守卫（AST 禁绕过 Repository） |
| 1 | `src/models/wiki_v2.py` | WikiPage/Claim/Evidence/ClaimRelation/PageRegistryEntry/SaveResult/ValidationFinding + 枚举 |
| 1 | `schema/wiki-page-v2.schema.json` | 页面 frontmatter 权威契约 |
| 1 | `schema/wiki-claim-v1.schema.json` | Claim YAML 权威契约 |
| 1 | `schema/AGENTS.md` | schema 目录说明 |
| 1 | `src/services/wiki_validator.py` | WikiValidator（findings 输出） |
| 1 | `src/services/wiki_repository.py` | canonical filesystem Repository（Protocol 实现） |
| 2 | `alembic/versions/j001_wiki_v2_projection.py` | 6 张 v2 projection 表 |
| 2 | `src/services/wiki_projection.py` | outbox 消费 + 全量重建 + parity |
| 3 | `src/services/wiki_claim_extractor.py` | block → Claim+Evidence（LLM） |
| 3 | `src/services/wiki_claim_matcher.py` | merge action 分类 |
| 3 | `src/services/wiki_merge_engine.py` | 应用 merge 到 Claim store |
| 5 | `src/services/wiki_dependency_service.py` | source→evidence→claim→page 图 |
| 5 | `src/services/wiki_rebuild_service.py` | 级联重编译 |
| 6 | `src/services/wiki_v2_migrator.py` | A/B 轨 → canonical 迁移 |
| 6 | `src/services/wiki_feedback_service.py` | 用户反馈 → Claim 状态 |
| 6 | `evals/run_knowledge_evolution_eval.py` | 10 项演进指标 |

### 修改文件

| Phase | 文件 | 改动 |
|---|---|---|
| 1 | `src/core/container.py` | 加 `wiki_repository` lazy property |
| 2 | `src/core/container.py` | 加 `wiki_projection` property；`src/services/db.py` `_SCHEMA` 加 v2 表（与 migration 同步） |
| 2 | `src/services/wiki_page_locator.py` | 候选 id 切稳定 `page_id`；projection 优先 + FS fallback |
| 3 | `src/core/container.py` | 加 claim_extractor/matcher/merge_engine properties |
| 4 | `src/services/knowledge_workflow.py` | ingest 流程接入 claim extractor→matcher→merge→repository |
| 4 | `src/services/wiki_write_service.py` | 双写分发器 → canonical 写入口（兼容旧返回字段） |
| 4 | `src/services/wiki_compiler.py` | 降级为 compatibility adapter |
| 4 | `src/services/wiki_entity_updater.py` | 不直接写文件，改生成建议 |
| 5 | `src/services/path_indexer.py` / `file_watcher.py` | 文件变更触发 rebuild job |
| 6 | `src/services/wiki_fs_lint.py` / `wiki_lint.py` / `src/cli.py` / `src/mcp_server.py` | validator 集成 + CLI/MCP 工具 |
| 终 | `src/version.py` / `README.md` / `PROGRESS.md` / `docs/` | 版本 1.6.0 + 文档 |

---

## 冲突点处理策略（必须在对应 Task 明确）

| # | 冲突 | 处理 |
|---|---|---|
| C1 | `src/repositories/wiki_repo.py`（旧 dict SQLite `WikiRepository`）vs `src/services/wiki_repository.py`（新 canonical `WikiRepository`）同名不同语义 | 新类命名 `WikiRepository` 放 `src/services/`（spec §4.2 原文）；旧 `repositories/wiki_repo.py` 加 deprecated docstring，Phase 4 迁移调用方后于 Phase 6 评估改名/删除。container 旧字段名 `wiki_repo`（构造期），新 property 名 `wiki_repository`（lazy），命名区分 |
| C2 | container `wiki_workflow` property 死代码（导入不存在的 `WikiWorkflowService`，无 caller） | Phase 1 一并删除该死代码 property（零 caller，安全） |
| C3 | `PAGE_TYPE_DIRS`(5) vs `WIKI_FIRST_DIRS`(8) 两份真源 | Phase 1 在 `wiki_v2.py` 定义权威 `PAGE_TYPES` 枚举；Phase 6 让 `wiki_index_compiler.PAGE_TYPE_DIRS` 与 `project_setup.WIKI_FIRST_DIRS` 引用它，收敛为单源 |
| C4 | `WikiLogCompiler` 绕过 `write_markdown`（直接 open/write_text，非原子） | Phase 1 Repository 不碰 log；Phase 4 改造时让 log 经 Repository 或 `write_markdown` |
| C5 | `config.yaml` `knowledge_workflow.mode` 默认 `legacy` | Phase 2 起所有 v2 代码三层门控（config `canonical_v2.enabled` + `mode==wiki_first` + stage/service 内 enabled）；cutover 条件（spec §11.3）全满足才启用 |
| C6 | source_ids 异构（FS YAML list/单值 vs SQLite JSON string） | 复用 v1.5.2 的 `wiki_source_ids.resolve_source_ids`/`_parse_json_list` helper，不重造 |

---

## Phase 0：基线与架构护栏（bite-sized）

### Task T0.1：记录迁移前质量基线

**Files:**
- Create: `scripts/record_canonical_v2_baseline.py`
- Create: `artifacts/eval/canonical-v2-baseline.json`（脚本生成）
- Modify: `.gitignore`（如需为 `artifacts/eval/` 加例外）

**Interfaces:**
- Produces: `artifacts/eval/canonical-v2-baseline.json`（可复现基线快照，供后续 Phase 回归对比）

- [ ] **Step 1：确认分支与工作树干净**

Run: `git branch --show-current && git status --short`
Expected: `feature/canonical-wiki-v2`；仅可能有未跟踪的 spec/plan 文档。

- [ ] **Step 2：写基线聚合脚本**

Create `scripts/record_canonical_v2_baseline.py`：

```python
"""记录 Canonical Wiki v2 迁移前的质量基线。

聚合 pytest 计数 / ruff / mypy / retrieval eval / wiki eval 到
``artifacts/eval/canonical-v2-baseline.json``,供后续 Phase 回归对比。

可复现:retrieval eval 用 CI 同款 fake-embedding(确定性,零 LLM)。
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "artifacts" / "eval" / "canonical-v2-baseline.json"


def _run(cmd: list[str]) -> tuple[int, str]:
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    return p.returncode, (p.stdout + p.stderr)


def _pytest_count() -> dict:
    rc, out = _run([sys.executable, "-m", "pytest", "tests/", "--co", "-q"])
    # 最后一行形如 "1237 tests selected"
    lines = [ln for ln in out.splitlines() if "test" in ln.lower() and "selected" in ln.lower()]
    return {"collected_tests_rc": rc, "summary": lines[-1].strip() if lines else out.strip().splitlines()[-1] if out.strip() else ""}


def _ruff() -> dict:
    rc, out = _run(["ruff", "check", "src", "tests", "evals", "tools", "scripts"])
    return {"rc": rc, "tail": "\n".join(out.splitlines()[-3:])}


def _mypy() -> dict:
    rc, out = _run(["mypy", "src", "tools"])
    return {"rc": rc, "tail": "\n".join(out.splitlines()[-3:])}


def _retrieval_eval() -> dict:
    rc, out = _run([
        sys.executable, "evals/run_retrieval_eval.py", "--all", "--fake-embedding",
        "--baseline", "evals/baselines/local.json", "--max-regression", "0.05",
        "--report", "json",
    ])
    return {"rc": rc, "tail": "\n".join(out.splitlines()[-8:])}


def _wiki_eval() -> dict:
    # wiki/ 不存在或为空时记 N/A,不阻断基线
    rc, out = _run([sys.executable, "evals/run_wiki_eval.py", "--source", "fs", "--report", "json"])
    return {"rc": rc, "tail": "\n".join(out.splitlines()[-8:])}


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    snapshot = {
        "recorded_at": datetime.now().isoformat(timespec="seconds"),
        "version": "1.5.2",
        "pytest": _pytest_count(),
        "ruff": _ruff(),
        "mypy": _mypy(),
        "retrieval_eval": _retrieval_eval(),
        "wiki_eval": _wiki_eval(),
    }
    OUT.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"baseline written -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 3：跑脚本生成基线**

Run: `python scripts/record_canonical_v2_baseline.py`
Expected: 输出 `baseline written -> .../canonical-v2-baseline.json`；文件内 `pytest.summary` 含约 1237 tests；`ruff.rc=0`；`mypy.rc=0`。

- [ ] **Step 4：确认基线可复现**

Run: `python scripts/record_canonical_v2_baseline.py && git diff --no-index <(python -c "import json,sys;print(json.load(open(sys.argv[1]))['pytest']['summary'])" artifacts/eval/canonical-v2-baseline.json) <(echo expected)` （或人工核对两次运行的 summary 一致）
Expected: 两次运行的 `pytest.summary`、`ruff.rc`、`mypy.rc` 一致。

- [ ] **Step 5：`.gitignore` 处理**

检查 `.gitignore` 是否忽略 `artifacts/`。若是，为 `artifacts/eval/canonical-v2-baseline.json` 加否定例外（`!artifacts/eval/canonical-v2-baseline.json`），让基线进版本控制（spec T0.1 要求"可复现"）。

- [ ] **Step 6：commit**

```bash
git add scripts/record_canonical_v2_baseline.py artifacts/eval/canonical-v2-baseline.json .gitignore docs/superpowers/specs/2026-07-07-canonical-wiki-claim-provenance-design.md docs/superpowers/plans/2026-07-07-canonical-wiki-v2.md
git commit -m "test(wiki-v2): record pre-migration quality baseline"
```

---

### Task T0.2：架构守卫测试（禁止绕过 WikiRepository）

**Files:**
- Create: `tests/test_canonical_write_guards.py`

**Interfaces:**
- Produces: `ALLOWED_DIRECT_WRITES` allowlist（锁定 v1.5.2 现状），后续 Phase 4 改造后逐步清空

- [ ] **Step 1：写失败测试（守卫机制本身）**

Create `tests/test_canonical_write_guards.py`：

```python
"""Canonical Wiki v2 架构守卫:禁止业务服务绕过 WikiRepository 直接写 canonical。

canonical_v2 启用前,现状豁免由 ALLOWED_DIRECT_WRITES 锁定;Phase 4 改造后
逐步从此 allowlist 移除,测试随之收紧。新增的绕过调用(不在 allowlist)会立即失败。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"

# 直接写 canonical 的调用点。"绕过"指未经 src/services/wiki_repository.py 的
# WikiRepository 直接落库/落盘 wiki 知识。键=(相对 src 的模块路径,被调用名)。
ALLOWED_DIRECT_WRITES: dict[tuple[str, str], str] = {
    ("services/wiki_compiler.py", "insert_wiki_page"):
        "A轨 SQLite 写,Phase 4 T4.3 降级为适配器后移除",
    ("services/wiki_compiler.py", "update_wiki_page"):
        "A轨 SQLite 写,Phase 4 T4.3 降级为适配器后移除",
    ("services/wiki_entity_updater.py", "write_markdown"):
        "B轨 FS 写,Phase 4 T4.1 改造经 WikiRepository 后移除",
    ("services/knowledge_workflow.py", "write_markdown"):
        "B轨 FS 写,Phase 4 T4.1 改造经 WikiRepository 后移除",
    ("services/wiki_source_compiler.py", "write_markdown"):
        "B轨 FS 写,Phase 4 T4.1 改造经 WikiRepository 后移除",
    ("services/wiki_index_compiler.py", "write_markdown"):
        "index.md 生成,Phase 4 评估是否经 Repository",
    ("services/wiki_log_compiler.py", "write_text"):
        "log.md 直接 write_text 非原子写(C4),Phase 4 改造后移除",
}

# 守卫覆盖的模块 + 各自禁止的"直接写"调用名
GUARDED: dict[str, set[str]] = {
    "services/wiki_compiler.py": {"insert_wiki_page", "update_wiki_page"},
    "services/wiki_entity_updater.py": {"write_markdown"},
    "services/knowledge_workflow.py": {"write_markdown"},
    "services/wiki_source_compiler.py": {"write_markdown"},
    "services/wiki_index_compiler.py": {"write_markdown"},
    "services/wiki_log_compiler.py": {"write_text"},  # C4: 非原子写,Phase 4 处理
}


def _find_calls(tree: ast.AST, names: set[str]) -> list[str]:
    """返回模块中出现的被禁调用名(去重)。"""
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in names:
                found.add(node.func.attr)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in names:
                found.add(node.func.id)
    return sorted(found)


def _scan() -> list[tuple[str, str]]:
    """扫描所有守卫模块,返回未豁免的直接写调用 (module, name)。"""
    offenders: list[tuple[str, str]] = []
    for rel, names in GUARDED.items():
        path = SRC / rel
        if not path.exists():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for hit in _find_calls(tree, names):
            if (rel, hit) not in ALLOWED_DIRECT_WRITES:
                offenders.append((rel, hit))
    return offenders


def test_guard_catches_unlisted_direct_write():
    """守卫机制本身有效:能识别直接写调用。"""
    tree = ast.parse("import x\nx.insert_wiki_page({})\nx.write_markdown(p, {}, 'b')")
    found = _find_calls(tree, {"insert_wiki_page", "write_markdown"})
    assert found == ["insert_wiki_page", "write_markdown"]


def test_current_direct_writes_are_allowlisted():
    """v1.5.2 现状:所有直接写调用必须在 allowlist 内。新增绕过会失败。"""
    offenders = _scan()
    assert offenders == [], (
        "发现未豁免的直接 canonical 写(若为已知现状,加入 ALLOWED_DIRECT_WRITES;若为新代码,改走 WikiRepository): "
        + ", ".join(f"{m}:{n}" for m, n in offenders)
    )


def test_allowlist_entries_actually_exist():
    """allowlist 每条都对应真实调用,避免空壳豁免漂移。"""
    for (rel, name) in ALLOWED_DIRECT_WRITES:
        path = SRC / rel
        assert path.exists(), f"allowlist 指向不存在的模块: {rel}"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        assert _find_calls(tree, {name}), f"allowlist 条目 {rel}:{name} 在模块中找不到对应调用(已迁移?请移除该豁免)"
```

- [ ] **Step 2：跑测试确认机制与现状**

Run: `python -m pytest tests/test_canonical_write_guards.py -v`
Expected: 3 passed（`test_guard_catches_unlisted_direct_write` 验证机制；`test_current_direct_writes_are_allowlisted` 锁定现状；`test_allowlist_entries_actually_exist` 防空壳豁免）。

> 注：此 Task 是"回归保护"测试，初始即绿（allowlist 覆盖 v1.5.2 现状）。若 `test_current_direct_writes_are_allowlisted` 失败，说明扫描到的调用点不在 allowlist——按真实情况补 allowlist（已知现状）或修正 GUARDED 范围。

- [ ] **Step 3：commit**

```bash
git add tests/test_canonical_write_guards.py
git commit -m "test(wiki-v2): add canonical write boundary guards"
```

- [ ] **Phase 0 回归门禁**

Run: `python -m pytest tests/ -q && ruff check src tests evals tools scripts && mypy src tools`
Expected: 全量 pytest 通过（基线约 1237）；ruff/mypy 净增 0 错误。

---

## Phase 1：数据模型与 Repository（bite-sized）

### Task T1.1：Wiki v2 模型

**Files:**
- Create: `src/models/wiki_v2.py`
- Test: `tests/test_wiki_v2_models.py`

**Interfaces:**
- Produces: `WikiPage`/`Claim`/`Evidence`/`ClaimRelation`/`PageRegistryEntry`/`SaveResult`/`ValidationFinding` dataclass + `PageType`/`PageStatus`/`ClaimStatus`/`EvidenceStance` 枚举 + 模块级 `PAGE_TYPES`（C3 收敛用）；严格 `from_dict(strict=True)`/`to_dict()`；`Claim.validate()`/`WikiPage.validate()` 返回 `list[str]` invariant 错误。

- [ ] **Step 1：写失败测试（模型 round-trip + invariant）**

Create `tests/test_wiki_v2_models.py`：

```python
from __future__ import annotations

import pytest

from src.models.wiki_v2 import (
    Claim, ClaimStatus, Evidence, EvidenceStance, PageRegistryEntry,
    PageStatus, PageType, SaveResult, ValidationFinding, WikiPage,
)


def _sample_page(**over) -> dict:
    d = dict(
        schema_version=2, page_id="page_abc", title="FTTR", page_type="concepts",
        status="draft", revision=1, aliases=[], tags=[], source_ids=["k1"],
        claim_ids=[], created_at="2026-07-07T12:00:00+08:00",
        updated_at="2026-07-07T12:00:00+08:00", content_hash="sha256:x",
        body="# FTTR\n", supersedes_page_id=None,
    )
    d.update(over)
    return d


def _sample_claim(**over) -> dict:
    d = dict(
        schema_version=1, claim_id="claim_abc",
        statement="FTTR 使用光纤。", normalized_statement="fttr使用光纤",
        claim_type="fact", status="active", confidence=0.9,
        valid_from=None, valid_to=None, subject_refs=["entity:FTTR"],
        predicate="uses", object_refs=["concept:fiber"],
        evidence=[
            dict(evidence_id="ev1", stance="supports", knowledge_id="k1",
                 block_id="b1", location={"page": 1}, source_revision="sha256:s",
                 excerpt_hash="sha256:e", observed_at="2026-07-07T12:00:00+08:00"),
        ],
        relations=[], created_at="2026-07-07T12:00:00+08:00",
        updated_at="2026-07-07T12:00:00+08:00", revision=1,
    )
    d.update(over)
    return d


def test_page_roundtrip_strict():
    p = WikiPage.from_dict(_sample_page(), strict=True)
    assert p.page_id == "page_abc" and p.page_type is PageType.CONCEPTS
    out = p.to_dict()
    again = WikiPage.from_dict(out, strict=True)
    assert again == p


def test_page_missing_required_strict_fails():
    d = _sample_page()
    d.pop("page_id")
    with pytest.raises((ValueError, TypeError)):
        WikiPage.from_dict(d, strict=True)


def test_page_invalid_status_fails():
    with pytest.raises(ValueError):
        WikiPage.from_dict(_sample_page(status="bogus"), strict=True)


def test_page_invalid_revision_fails():
    with pytest.raises(ValueError):
        WikiPage.from_dict(_sample_page(revision=0), strict=True)
    with pytest.raises(ValueError):
        WikiPage.from_dict(_sample_page(revision=-1), strict=True)


def test_claim_roundtrip_strict():
    c = Claim.from_dict(_sample_claim(), strict=True)
    assert c.status is ClaimStatus.ACTIVE and c.evidence[0].stance is EvidenceStance.SUPPORTS
    assert Claim.from_dict(c.to_dict(), strict=True) == c


def test_active_claim_without_supports_evidence_invalid():
    # active Claim 必须至少一条有效 supports Evidence
    d = _sample_claim(status="active", evidence=[
        dict(evidence_id="ev1", stance="contradicts", knowledge_id="k1",
             block_id="b1", location={}, source_revision="sha256:s",
             excerpt_hash=None, observed_at="2026-07-07T12:00:00+08:00"),
    ])
    c = Claim.from_dict(d, strict=True)
    errors = c.validate()
    assert any("supports" in e for e in errors)


def test_evidence_requires_knowledge_id():
    d = _sample_claim()
    d["evidence"][0]["knowledge_id"] = ""
    with pytest.raises(ValueError):
        Claim.from_dict(d, strict=True)


def test_compat_mode_tolerates_unknown_keys():
    d = _sample_page()
    d["future_field"] = "x"
    # strict=True 拒绝未知键;strict=False 容忍
    with pytest.raises(ValueError):
        WikiPage.from_dict(d, strict=True)
    WikiPage.from_dict(d, strict=False)  # 不抛


def test_save_result_and_validation_finding_dataclasses():
    sr = SaveResult(ok=True, object_id="page_abc", revision=2, warnings=[], outbox_events=["page.updated"])
    assert sr.to_dict()["ok"] is True
    vf = ValidationFinding(path="concepts/fttr.md", object_id="page_abc",
                           category="schema_invalid", severity="error", message="bad")
    assert vf.severity == "error"


def test_page_registry_entry_roundtrip():
    e = PageRegistryEntry(path="concepts/fttr.md", title="FTTR",
                          page_type="concepts", revision=1, content_hash="sha256:x")
    assert PageRegistryEntry.from_dict(e.to_dict()) == e
```

- [ ] **Step 2：跑测试确认失败**

Run: `python -m pytest tests/test_wiki_v2_models.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'src.models.wiki_v2'`）。

- [ ] **Step 3：实现模型**

Create `src/models/wiki_v2.py`：

```python
"""Canonical Wiki v2 数据模型(Markdown canonical store + Claim 证据链)。

风格对齐 src/models/block.py:@dataclass + from_dict/to_dict。
strict=True 拒绝未知键与缺必填字段(模型层 schema 校验);
strict=False 容忍未知键(向前兼容读取老 canonical 文件)。
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum


class PageType(str, Enum):
    SOURCES = "sources"
    ENTITIES = "entities"
    CONCEPTS = "concepts"
    COMPARISONS = "comparisons"
    SYNTHESES = "syntheses"


# C3 收敛点:权威 page_type 真源,供 wiki_index_compiler / project_setup 引用
PAGE_TYPES: tuple[str, ...] = tuple(t.value for t in PageType)


class PageStatus(str, Enum):
    DRAFT = "draft"
    REVIEW = "review"
    PUBLISHED = "published"
    DEPRECATED = "deprecated"


class ClaimStatus(str, Enum):
    ACTIVE = "active"
    DISPUTED = "disputed"
    SUPERSEDED = "superseded"
    UNSUPPORTED = "unsupported"
    RETRACTED = "retracted"
    DRAFT = "draft"


class EvidenceStance(str, Enum):
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    QUALIFIES = "qualifies"
    SUPERSEDES = "supersedes"


@dataclass
class Evidence:
    evidence_id: str
    stance: EvidenceStance
    knowledge_id: str
    block_id: str | None = None
    location: dict = field(default_factory=dict)
    source_revision: str = ""
    excerpt_hash: str | None = None
    observed_at: str = ""

    @classmethod
    def from_dict(cls, d: dict, strict: bool = True) -> "Evidence":
        if not d.get("knowledge_id"):
            raise ValueError("Evidence 必须有 knowledge_id")
        stance = d["stance"] if isinstance(d["stance"], EvidenceStance) else EvidenceStance(d["stance"])
        return cls(
            evidence_id=d["evidence_id"], stance=stance, knowledge_id=d["knowledge_id"],
            block_id=d.get("block_id"), location=d.get("location") or {},
            source_revision=d.get("source_revision", ""), excerpt_hash=d.get("excerpt_hash"),
            observed_at=d.get("observed_at", ""),
        )

    def to_dict(self) -> dict:
        return {
            "evidence_id": self.evidence_id, "stance": self.stance.value,
            "knowledge_id": self.knowledge_id, "block_id": self.block_id,
            "location": self.location, "source_revision": self.source_revision,
            "excerpt_hash": self.excerpt_hash, "observed_at": self.observed_at,
        }


@dataclass
class ClaimRelation:
    relation: str  # refines / supersedes / contradicts / ...
    target_claim_id: str

    def to_dict(self) -> dict:
        return {"relation": self.relation, "target_claim_id": self.target_claim_id}

    @classmethod
    def from_dict(cls, d: dict) -> "ClaimRelation":
        return cls(relation=d["relation"], target_claim_id=d["target_claim_id"])


@dataclass
class Claim:
    schema_version: int
    claim_id: str
    statement: str
    normalized_statement: str
    claim_type: str
    status: ClaimStatus
    confidence: float
    valid_from: str | None
    valid_to: str | None
    subject_refs: list[str]
    predicate: str
    object_refs: list[str]
    evidence: list[Evidence]
    relations: list[ClaimRelation]
    created_at: str
    updated_at: str
    revision: int

    def validate(self) -> list[str]:
        """跨字段 invariant 校验,返回错误描述列表(空=合法)。"""
        errors: list[str] = []
        if self.revision < 1:
            errors.append("revision 必须是正整数")
        if self.status is ClaimStatus.ACTIVE:
            supports = [e for e in self.evidence if e.stance is EvidenceStance.SUPPORTS]
            if not supports:
                errors.append("active Claim 必须至少有一条 supports Evidence")
        return errors

    @classmethod
    def from_dict(cls, d: dict, strict: bool = True) -> "Claim":
        required = ["schema_version", "claim_id", "statement", "normalized_statement",
                    "claim_type", "status", "confidence", "subject_refs", "predicate",
                    "object_refs", "evidence", "created_at", "updated_at", "revision"]
        known = set(required) | {"valid_from", "valid_to", "relations"}
        if strict:
            extra = set(d.keys()) - known
            if extra:
                raise ValueError(f"Claim 未知字段(strict): {sorted(extra)}")
            for k in required:
                if k not in d:
                    raise ValueError(f"Claim 缺必填字段: {k}")
        rev = int(d["revision"])
        if rev < 1:
            raise ValueError("revision 必须是正整数")
        status = d["status"] if isinstance(d["status"], ClaimStatus) else ClaimStatus(d["status"])
        return cls(
            schema_version=int(d["schema_version"]), claim_id=d["claim_id"],
            statement=d["statement"], normalized_statement=d["normalized_statement"],
            claim_type=d["claim_type"], status=status, confidence=float(d["confidence"]),
            valid_from=d.get("valid_from"), valid_to=d.get("valid_to"),
            subject_refs=list(d.get("subject_refs", [])), predicate=d.get("predicate", ""),
            object_refs=list(d.get("object_refs", [])),
            evidence=[Evidence.from_dict(e, strict=strict) for e in d.get("evidence", [])],
            relations=[ClaimRelation.from_dict(r) for r in d.get("relations", [])],
            created_at=d["created_at"], updated_at=d["updated_at"], revision=rev,
        )

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version, "claim_id": self.claim_id,
            "statement": self.statement, "normalized_statement": self.normalized_statement,
            "claim_type": self.claim_type, "status": self.status.value,
            "confidence": self.confidence, "valid_from": self.valid_from, "valid_to": self.valid_to,
            "subject_refs": list(self.subject_refs), "predicate": self.predicate,
            "object_refs": list(self.object_refs),
            "evidence": [e.to_dict() for e in self.evidence],
            "relations": [r.to_dict() for r in self.relations],
            "created_at": self.created_at, "updated_at": self.updated_at, "revision": self.revision,
        }


@dataclass
class WikiPage:
    schema_version: int
    page_id: str
    title: str
    page_type: PageType
    status: PageStatus
    revision: int
    aliases: list[str]
    tags: list[str]
    source_ids: list[str]
    claim_ids: list[str]
    created_at: str
    updated_at: str
    content_hash: str
    body: str
    supersedes_page_id: str | None = None

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.revision < 1:
            errors.append("revision 必须是正整数")
        if self.status is PageStatus.PUBLISHED:
            # published 页面不得引用 draft Claim(精确校验在 WikiValidator,此处只防 page 自身 draft claim_ids 命名约定缺失)
            pass
        return errors

    @classmethod
    def from_dict(cls, d: dict, strict: bool = True) -> "WikiPage":
        required = ["schema_version", "page_id", "title", "page_type", "status",
                    "revision", "source_ids", "claim_ids", "created_at", "updated_at",
                    "content_hash", "body"]
        known = set(required) | {"aliases", "tags", "supersedes_page_id"}
        if strict:
            extra = set(d.keys()) - known
            if extra:
                raise ValueError(f"WikiPage 未知字段(strict): {sorted(extra)}")
            for k in required:
                if k not in d:
                    raise ValueError(f"WikiPage 缺必填字段: {k}")
        rev = int(d["revision"])
        if rev < 1:
            raise ValueError("revision 必须是正整数")
        pt = d["page_type"] if isinstance(d["page_type"], PageType) else PageType(d["page_type"])
        st = d["status"] if isinstance(d["status"], PageStatus) else PageStatus(d["status"])
        return cls(
            schema_version=int(d["schema_version"]), page_id=d["page_id"], title=d["title"],
            page_type=pt, status=st, revision=rev, aliases=list(d.get("aliases", [])),
            tags=list(d.get("tags", [])), source_ids=list(d.get("source_ids", [])),
            claim_ids=list(d.get("claim_ids", [])), created_at=d["created_at"],
            updated_at=d["updated_at"], content_hash=d["content_hash"], body=d.get("body", ""),
            supersedes_page_id=d.get("supersedes_page_id"),
        )

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version, "page_id": self.page_id, "title": self.title,
            "page_type": self.page_type.value, "status": self.status.value, "revision": self.revision,
            "aliases": list(self.aliases), "tags": list(self.tags), "source_ids": list(self.source_ids),
            "claim_ids": list(self.claim_ids), "created_at": self.created_at, "updated_at": self.updated_at,
            "content_hash": self.content_hash, "body": self.body,
            "supersedes_page_id": self.supersedes_page_id,
        }


@dataclass
class PageRegistryEntry:
    path: str
    title: str
    page_type: str
    revision: int
    content_hash: str

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "PageRegistryEntry":
        return cls(path=d["path"], title=d["title"], page_type=d["page_type"],
                   revision=int(d["revision"]), content_hash=d["content_hash"])


@dataclass
class SaveResult:
    ok: bool
    object_id: str
    revision: int
    warnings: list[str] = field(default_factory=list)
    outbox_events: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ValidationFinding:
    path: str
    object_id: str
    category: str  # schema_invalid / claim_missing / evidence_missing / evidence_stale / projection_drift / registry_drift / publish_gate_violation
    severity: str  # error / warning
    message: str
    detail: dict = field(default_factory=dict)
```

> 实现者注：`from_dict` 的 strict 检查逻辑较密，实现时以测试为准——若某条 strict 行为测试未覆盖且与 spec §5.1 字段表冲突，以 spec 字段表为准并在 commit body 注明。

- [ ] **Step 4：跑测试确认通过**

Run: `python -m pytest tests/test_wiki_v2_models.py -v`
Expected: 9 passed。

- [ ] **Step 5：commit**

```bash
git add src/models/wiki_v2.py tests/test_wiki_v2_models.py
git commit -m "feat(wiki-v2): add canonical page claim evidence models"
```

---

### Task T1.2：Schema Validator + JSON Schema 契约

**Files:**
- Create: `schema/wiki-page-v2.schema.json`
- Create: `schema/wiki-claim-v1.schema.json`
- Create: `schema/AGENTS.md`
- Create: `src/services/wiki_validator.py`
- Test: `tests/test_wiki_validator.py`

**Interfaces:**
- Consumes: T1.1 的 `WikiPage`/`Claim`/`from_dict(strict)`/`validate()`
- Produces: `WikiValidator`（`validate_page`/`validate_claim`/`validate_directory` → `list[ValidationFinding]`），finding 含 `path/object_id/category/severity/message`

**设计决策（不引入 jsonschema 依赖）：** `schema/*.json` 是人类可读权威契约；`WikiValidator` 核心校验用模型层 `from_dict(strict=True)`（捕获 schema 错误）+ 手写跨对象 invariant（published 页面引用 draft Claim、active Claim 无 supports Evidence 等）。若运行时检测到 `jsonschema` 已安装则可选严格校验，否则 fallback。

- [ ] **Step 1：写 schema 契约文件**

Create `schema/wiki-page-v2.schema.json`：

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "https://shinehe/schema/wiki-page-v2.schema.json",
  "title": "WikiPage v2 frontmatter",
  "type": "object",
  "required": ["schema_version", "page_id", "title", "page_type", "status", "revision", "source_ids", "claim_ids", "created_at", "updated_at", "content_hash"],
  "properties": {
    "schema_version": {"const": 2},
    "page_id": {"type": "string", "pattern": "^page_"},
    "title": {"type": "string", "minLength": 1},
    "page_type": {"enum": ["sources", "entities", "concepts", "comparisons", "syntheses"]},
    "status": {"enum": ["draft", "review", "published", "deprecated"]},
    "revision": {"type": "integer", "minimum": 1},
    "aliases": {"type": "array", "items": {"type": "string"}},
    "tags": {"type": "array", "items": {"type": "string"}},
    "source_ids": {"type": "array", "items": {"type": "string"}},
    "claim_ids": {"type": "array", "items": {"type": "string"}},
    "created_at": {"type": "string"},
    "updated_at": {"type": "string"},
    "content_hash": {"type": "string"},
    "supersedes_page_id": {"type": ["string", "null"]}
  }
}
```

Create `schema/wiki-claim-v1.schema.json`：

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "https://shinehe/schema/wiki-claim-v1.schema.json",
  "title": "Claim v1",
  "type": "object",
  "required": ["schema_version", "claim_id", "statement", "normalized_statement", "claim_type", "status", "confidence", "evidence", "created_at", "updated_at", "revision"],
  "properties": {
    "schema_version": {"const": 1},
    "claim_id": {"type": "string", "pattern": "^claim_"},
    "statement": {"type": "string", "minLength": 1},
    "normalized_statement": {"type": "string"},
    "claim_type": {"type": "string"},
    "status": {"enum": ["active", "disputed", "superseded", "unsupported", "retracted", "draft"]},
    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    "valid_from": {"type": ["string", "null"]},
    "valid_to": {"type": ["string", "null"]},
    "subject_refs": {"type": "array", "items": {"type": "string"}},
    "predicate": {"type": "string"},
    "object_refs": {"type": "array", "items": {"type": "string"}},
    "evidence": {"type": "array", "minItems": 1, "items": {"type": "object"}},
    "relations": {"type": "array"},
    "created_at": {"type": "string"},
    "updated_at": {"type": "string"},
    "revision": {"type": "integer", "minimum": 1}
  }
}
```

Create `schema/AGENTS.md`：

```markdown
# schema/

Canonical Wiki v2 的权威数据契约(JSON Schema)。供人类审阅与未来严格校验接入。

- `wiki-page-v2.schema.json` — 页面 frontmatter 契约(spec §5.1)
- `wiki-claim-v1.schema.json` — Claim YAML 契约(spec §5.2)

运行时校验由 `src/services/wiki_validator.py` 的 `WikiValidator` 完成(模型层
`from_dict(strict=True)` + 跨对象 invariant),不强制依赖 `jsonschema` 库。
如安装了 `jsonschema`,可选用其做额外严格校验。
```

- [ ] **Step 2：写失败测试**

Create `tests/test_wiki_validator.py`：

```python
from __future__ import annotations

from pathlib import Path

import pytest

from src.models.wiki_v2 import Claim, WikiPage
from src.services.wiki_validator import WikiValidator


def _page_dict(**over):
    d = dict(schema_version=2, page_id="page_abc", title="FTTR", page_type="concepts",
             status="draft", revision=1, aliases=[], tags=[], source_ids=["k1"], claim_ids=[],
             created_at="2026-07-07T12:00:00+08:00", updated_at="2026-07-07T12:00:00+08:00",
             content_hash="sha256:x", body="# FTTR\n")
    d.update(over)
    return d


def test_validate_clean_page_no_findings():
    v = WikiValidator()
    findings = v.validate_page(WikiPage.from_dict(_page_dict(), strict=True))
    assert findings == []


def test_validate_page_schema_error_category():
    v = WikiValidator()
    d = _page_dict(status="bogus")
    findings = v.validate_page_dict(d)  # 接受 dict,内部 try from_dict
    assert any(f.category == "schema_invalid" and f.severity == "error" for f in findings)


def test_published_page_referencing_draft_claim_flagged(tmp_path):
    v = WikiValidator()
    page = WikiPage.from_dict(_page_dict(status="published", claim_ids=["claim_x"]), strict=True)
    # validator 接受一个 claim_store 查询函数
    def claim_lookup(cid):
        from src.models.wiki_v2 import ClaimStatus
        # 模拟 claim_x 处于 draft
        class _C:
            status = ClaimStatus.DRAFT
        return _C()
    findings = v.validate_page(page, claim_lookup=claim_lookup)
    assert any(f.category == "publish_gate_violation" for f in findings)


def test_validate_directory_reports_missing_claim_files(tmp_path):
    (tmp_path / "concepts").mkdir()
    (tmp_path / "concepts" / "fttr.md").write_text(
        "---\nschema_version: 2\npage_id: page_abc\ntitle: FTTR\npage_type: concepts\n"
        "status: draft\nrevision: 1\nsource_ids: []\nclaim_ids: [claim_missing]\n"
        "created_at: t\nupdated_at: t\ncontent_hash: x\n---\n\n# FTTR\n", encoding="utf-8")
    v = WikiValidator(wiki_dir=tmp_path)
    findings = v.validate_directory()
    assert any(f.category == "claim_missing" and f.object_id == "page_abc" for f in findings)
```

- [ ] **Step 3：跑测试确认失败**

Run: `python -m pytest tests/test_wiki_validator.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'src.services.wiki_validator'`）。

- [ ] **Step 4：实现 WikiValidator**

Create `src/services/wiki_validator.py`：

```python
"""Canonical Wiki v2 校验器:输出结构化 ValidationFinding。

核心校验 = 模型层 from_dict(strict=True)(捕获 schema 错误)
         + 跨对象 invariant(published 引用 draft Claim 等)。
schema/*.json 是权威契约文档,不强制 jsonschema 依赖。
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from src.models.wiki_v2 import (
    Claim, ClaimStatus, PageStatus, WikiPage,
    ValidationFinding,
)
from src.services.wiki_slug import read_frontmatter

ClaimLookup = Callable[[str], Optional[Claim]]


class WikiValidator:
    def __init__(self, wiki_dir: Path | str | None = None):
        self._wiki_dir = Path(wiki_dir) if wiki_dir else None

    # ---- 单对象校验 ----
    def validate_page_dict(self, d: dict, *, path: str = "", claim_lookup: ClaimLookup | None = None) -> list[ValidationFinding]:
        findings: list[ValidationFinding] = []
        try:
            page = WikiPage.from_dict(d, strict=True)
        except (ValueError, TypeError, KeyError) as e:
            findings.append(ValidationFinding(
                path=path, object_id=str(d.get("page_id", "?")),
                category="schema_invalid", severity="error",
                message=f"页面 schema 校验失败: {e}",
            ))
            return findings
        findings.extend(self.validate_page(page, path=path, claim_lookup=claim_lookup))
        return findings

    def validate_page(self, page: WikiPage, *, path: str = "", claim_lookup: ClaimLookup | None = None) -> list[ValidationFinding]:
        findings: list[ValidationFinding] = []
        path = path or f"{page.page_type.value}/{page.title}.md"
        # published 页面不得引用 draft Claim
        if page.status is PageStatus.PUBLISHED and page.claim_ids and claim_lookup:
            for cid in page.claim_ids:
                c = claim_lookup(cid)
                if c is not None and c.status is ClaimStatus.DRAFT:
                    findings.append(ValidationFinding(
                        path=path, object_id=page.page_id,
                        category="publish_gate_violation", severity="error",
                        message=f"published 页面引用了 draft Claim: {cid}",
                    ))
        return findings

    def validate_claim(self, claim: Claim, *, path: str = "") -> list[ValidationFinding]:
        path = path or f"claims/{claim.claim_id}.yaml"
        errors = claim.validate()
        return [ValidationFinding(
            path=path, object_id=claim.claim_id,
            category="schema_invalid" if "supports" not in e else "evidence_missing",
            severity="error", message=e,
        ) for e in errors]

    # ---- 目录级校验 ----
    def validate_directory(self) -> list[ValidationFinding]:
        """扫 wiki_dir 下所有 page md,检查 claim 文件存在性等目录级 invariant。"""
        findings: list[ValidationFinding] = []
        if not self._wiki_dir or not self._wiki_dir.exists():
            return findings
        claims_dir = self._wiki_dir / "claims"
        for pt in ("sources", "entities", "concepts", "comparisons", "syntheses"):
            d = self._wiki_dir / pt
            if not d.exists():
                continue
            for md in d.glob("*.md"):
                fm = read_frontmatter(md)
                if not fm.get("page_id"):
                    continue
                for cid in fm.get("claim_ids", []) or []:
                    if claims_dir.exists() and not (claims_dir / f"{cid}.yaml").exists():
                        findings.append(ValidationFinding(
                            path=str(md.relative_to(self._wiki_dir)).replace("\\", "/"),
                            object_id=fm["page_id"], category="claim_missing",
                            severity="error", message=f"Claim 文件缺失: {cid}.yaml",
                        ))
        return findings
```

- [ ] **Step 5：跑测试确认通过**

Run: `python -m pytest tests/test_wiki_validator.py -v`
Expected: 4 passed。

- [ ] **Step 6：commit**

```bash
git add schema/ src/services/wiki_validator.py tests/test_wiki_validator.py
git commit -m "feat(wiki-v2): add executable canonical schemas and validator"
```

---

### Task T1.3：WikiRepository（canonical filesystem）

**Files:**
- Create: `src/services/wiki_repository.py`
- Modify: `src/core/container.py`（加 `wiki_repository` lazy property）
- Test: `tests/test_wiki_repository.py`
- （C2 顺手）Modify: `src/core/container.py` 删除死代码 `wiki_workflow` property

**Interfaces:**
- Consumes: T1.1 模型、T1.2 validator、`wiki_slug.write_markdown`/`read_frontmatter`、`Config`
- Produces: `WikiRepository`（spec §4.2 Protocol 实现）；`StaleRevisionError`；`WikiTransaction`（context manager）；container.`wiki_repository` property

> **C1 处理**：新类名 `WikiRepository`，放 `src/services/wiki_repository.py`（spec §4.2）。旧 `src/repositories/wiki_repo.py` 的同名类不动，仅在 Phase 4 迁移调用方时标 deprecated。

- [ ] **Step 1：先做 impact 分析（GitNexus 护栏）**

Run（via gitnexus MCP）: `impact({target: "AppContainer", direction: "upstream", repo: "ClaudeCodeWorkSpace"})` 与 `context({name: "wiki_workflow"})`
Expected: 确认 `wiki_workflow` property 零外部 caller（死代码，可安全删）；记录 `AppContainer` 改动 blast radius（应为 LOW，加 property 不破坏现有）。

- [ ] **Step 2：写失败测试**

Create `tests/test_wiki_repository.py`：

```python
from __future__ import annotations

from pathlib import Path

import pytest

from src.models.wiki_v2 import Claim, ClaimStatus, EvidenceStance, PageStatus, PageType, WikiPage
from src.services.wiki_repository import StaleRevisionError, WikiRepository


@pytest.fixture
def repo(tmp_path):
    return WikiRepository(
        wiki_dir=tmp_path / "wiki",
        registry_path=tmp_path / "wiki" / "_meta" / "pages.json",
        redirects_path=tmp_path / "wiki" / "_meta" / "redirects.json",
        outbox_path=tmp_path / "wiki_projection_outbox.jsonl",
    )


def _page(page_id="page_1", title="FTTR", revision=1, claim_ids=None):
    return WikiPage(
        schema_version=2, page_id=page_id, title=title, page_type=PageType.CONCEPTS,
        status=PageStatus.DRAFT, revision=revision, aliases=[], tags=[], source_ids=["k1"],
        claim_ids=claim_ids or [], created_at="t", updated_at="t",
        content_hash="sha256:x", body="# FTTR\n", supersedes_page_id=None,
    )


def test_create_page_writes_file_and_registry(repo):
    r = repo.save_page(_page())
    assert r.ok and r.revision == 1
    assert (repo._wiki_dir / "concepts" / "fttr.md").exists()
    # registry 含映射
    entry = repo.get_registry().get("page_1")
    assert entry and entry["path"] == "concepts/fttr.md"


def test_update_increments_revision_when_expected_matches(repo):
    repo.save_page(_page(revision=1))
    p = repo.get_page("page_1")
    p.body = "# FTTR v2\n"
    r = repo.save_page(p, expected_revision=1)
    assert r.ok and r.revision == 2


def test_stale_expected_revision_raises(repo):
    repo.save_page(_page(revision=1))
    p = repo.get_page("page_1")
    p.body = "x"
    with pytest.raises(StaleRevisionError):
        repo.save_page(p, expected_revision=99)  # 期望旧值,实际已是 1→将被改


def test_atomic_write_no_half_file_on_error(repo, monkeypatch):
    # 模拟 registry 写失败时 canonical 文件不被半写(具体用 monkeypatch 检查无 .tmp 残留)
    p = _page()
    repo.save_page(p)
    assert not list(repo._wiki_dir.rglob("*.tmp"))


def test_rename_keeps_page_id(repo):
    repo.save_page(_page(title="OldName"))
    r = repo.move_page("page_1", new_title="NewName")
    assert r.ok
    assert (repo._wiki_dir / "concepts" / "newname.md").exists()
    assert not (repo._wiki_dir / "concepts" / "oldname.md").exists()
    # page_id 不变
    assert repo.get_page("page_1").page_id == "page_1"
    # redirect 记录旧路径
    redirects = repo.get_redirects()
    assert any("oldname" in k for k in redirects)


def test_claim_crud(repo):
    c = Claim.from_dict(dict(
        schema_version=1, claim_id="claim_1", statement="s", normalized_statement="s",
        claim_type="fact", status="active", confidence=0.9, valid_from=None, valid_to=None,
        subject_refs=[], predicate="", object_refs=[],
        evidence=[dict(evidence_id="ev1", stance="supports", knowledge_id="k1", block_id="b1",
                       location={}, source_revision="sha256:s", excerpt_hash=None, observed_at="t")],
        relations=[], created_at="t", updated_at="t", revision=1,
    ), strict=True)
    r = repo.save_claim(c)
    assert r.ok
    got = repo.get_claim("claim_1")
    assert got and got.statement == "s"
    # delete(soft) 后 get 返回 None
    repo.delete_claim("claim_1", soft=True)
    assert repo.get_claim("claim_1") is None


def test_transaction_rollback_on_failure(repo):
    p1 = _page(page_id="page_a", title="A")
    p2 = _page(page_id="page_b", title="B")
    with pytest.raises(RuntimeError):
        with repo.transaction() as tx:
            tx.stage_page(p1)
            tx.stage_page(p2)
            raise RuntimeError("boom")  # 模拟中途失败
    # 中途失败:两个文件都不应发布
    assert not (repo._wiki_dir / "concepts" / "a.md").exists()
    assert not (repo._wiki_dir / "concepts" / "b.md").exists()
    assert not list((repo._wiki_dir / "_staging").glob("*")) if (repo._wiki_dir / "_staging").exists() else True


def test_outbox_events_appended_in_order(repo):
    repo.save_page(_page())
    events = repo.read_outbox()
    assert events and events[0]["type"] in ("page.created", "page.updated")


def test_windows_path_compat(repo):
    # 路径用 / 分隔存 registry,跨平台
    repo.save_page(_page(title="Win Path"))
    reg = repo.get_registry()
    entry = next(iter(reg.values()))
    assert "\\" not in entry["path"]


def test_concurrent_write_conflict_detected(repo):
    # 两次同 expected_revision=1 并发(同进程模拟:第一次成功后第二次 expected 仍 1 应冲突)
    repo.save_page(_page(revision=1))
    p = repo.get_page("page_1")
    p2 = repo.get_page("page_1")  # 另一"会话"读到 revision=1
    p.body = "v2"
    repo.save_page(p, expected_revision=1)  # 先提交 → revision=2
    p2.body = "v3"
    with pytest.raises(StaleRevisionError):
        repo.save_page(p2, expected_revision=1)  # lost update 防护
```

- [ ] **Step 3：跑测试确认失败**

Run: `python -m pytest tests/test_wiki_repository.py -v`
Expected: FAIL（`ModuleNotFoundError`）。

- [ ] **Step 4：实现 WikiRepository**

Create `src/services/wiki_repository.py`：

```python
"""Canonical Wiki filesystem Repository(spec §4.2 ADR-002)。

唯一 canonical 写入口:页面 Markdown + Claim YAML + registry + redirects + outbox。
- 原子写:复用 wiki_slug.write_markdown(tempfile + os.replace);Claim YAML 同模式
- revision 乐观锁:save 时比对 expected_revision,失配抛 StaleRevisionError
- transaction:wiki/_staging/<tx_id>/ 暂存,commit 逐文件 os.replace,registry 最后替换
- 路径安全:所有 canonical 路径必须在 wiki_dir 内,禁 ..
"""
from __future__ import annotations

import contextlib
import json
import os
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Iterator

import yaml

from src.models.wiki_v2 import (
    Claim, PageRegistryEntry, PageType, SaveResult, WikiPage,
)
from src.services.wiki_slug import slugify, write_markdown, read_frontmatter


class StaleRevisionError(RuntimeError):
    """expected_revision 与磁盘当前 revision 不符(lost update 防护)。"""


class WikiTransaction:
    """staging 事务:stage 暂存,commit 原子发布,异常自动丢弃 staging。"""

    def __init__(self, repo: "WikiRepository", tx_id: str):
        self._repo = repo
        self._tx_id = tx_id
        self._staged_pages: list[tuple[WikiPage, int | None]] = []
        self._staged_claims: list[tuple[Claim, int | None]] = []
        self._committed = False

    def stage_page(self, page: WikiPage, expected_revision: int | None = None) -> None:
        self._staged_pages.append((page, expected_revision))

    def stage_claim(self, claim: Claim, expected_revision: int | None = None) -> None:
        self._staged_claims.append((claim, expected_revision))

    def commit(self) -> list[SaveResult]:
        results: list[SaveResult] = []
        for page, exp in self._staged_pages:
            results.append(self._repo.save_page(page, expected_revision=exp))
        for claim, exp in self._staged_claims:
            results.append(self._repo.save_claim(claim, expected_revision=exp))
        self._committed = True
        return results


class WikiRepository:
    def __init__(
        self,
        wiki_dir: Path | str,
        registry_path: Path | str,
        redirects_path: Path | str,
        outbox_path: Path | str,
        validator=None,
    ):
        self._wiki_dir = Path(wiki_dir)
        self._registry_path = Path(registry_path)
        self._redirects_path = Path(redirects_path)
        self._outbox_path = Path(outbox_path)
        self._validator = validator
        self._lock = threading.RLock()
        self._wiki_dir.mkdir(parents=True, exist_ok=True)
        (self._wiki_dir / "claims").mkdir(exist_ok=True)
        (self._wiki_dir / "_meta").mkdir(exist_ok=True)
        (self._wiki_dir / "_staging").mkdir(exist_ok=True)

    # ---- 路径解析 ----
    def _page_path(self, page_type: PageType, title: str) -> Path:
        return self._wiki_dir / page_type.value / f"{slugify(title)}.md"

    def _claim_path(self, claim_id: str) -> Path:
        return self._wiki_dir / "claims" / f"{claim_id}.yaml"

    def _rel(self, abs_path: Path) -> str:
        return str(abs_path.relative_to(self._wiki_dir)).replace("\\", "/")

    def _assert_inside_wiki(self, path: Path) -> None:
        try:
            path.resolve().relative_to(self._wiki_dir.resolve())
        except ValueError as e:
            raise ValueError(f"路径越界 wiki_dir: {path}") from e

    # ---- registry / redirects / outbox ----
    def get_registry(self) -> dict[str, dict]:
        if not self._registry_path.exists():
            return {}
        try:
            return json.loads(self._registry_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError):
            return {}

    def _write_registry(self, reg: dict) -> None:
        self._registry_path.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write_json(self._registry_path, reg)

    def get_redirects(self) -> dict[str, str]:
        if not self._redirects_path.exists():
            return {}
        try:
            return json.loads(self._redirects_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError):
            return {}

    def _write_redirects(self, red: dict) -> None:
        self._redirects_path.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write_json(self._redirects_path, red)

    def _atomic_write_json(self, path: Path, data) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".json.tmp")
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise

    def _append_outbox(self, event: dict) -> None:
        self._outbox_path.parent.mkdir(parents=True, exist_ok=True)
        with self._outbox_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    def read_outbox(self) -> list[dict]:
        if not self._outbox_path.exists():
            return []
        return [json.loads(ln) for ln in self._outbox_path.read_text(encoding="utf-8").splitlines() if ln.strip()]

    # ---- page CRUD ----
    def get_page(self, page_id: str) -> WikiPage | None:
        reg = self.get_registry()
        entry = reg.get(page_id)
        if not entry:
            return None
        path = self._wiki_dir / entry["path"]
        if not path.exists():
            return None
        return self._read_page_file(path)

    def get_page_by_title(self, title: str) -> WikiPage | None:
        reg = self.get_registry()
        slug = slugify(title)
        for pid, entry in reg.items():
            if entry["path"].endswith(f"/{slug}.md"):
                return self.get_page(pid)
        return None

    def list_pages(self, page_type: str | None = None) -> list[WikiPage]:
        pages: list[WikiPage] = []
        for entry in self.get_registry().values():
            if page_type and entry["page_type"] != page_type:
                continue
            path = self._wiki_dir / entry["path"]
            if path.exists():
                p = self._read_page_file(path)
                if p:
                    pages.append(p)
        return pages

    def _read_page_file(self, path: Path) -> WikiPage | None:
        fm = read_frontmatter(path)
        if not fm.get("page_id"):
            return None
        text = path.read_text(encoding="utf-8")
        body = text.split("---", 2)[2].lstrip("\n") if "---" in text else ""
        fm["body"] = body
        try:
            return WikiPage.from_dict(fm, strict=False)
        except (ValueError, TypeError):
            return None

    def save_page(self, page: WikiPage, expected_revision: int | None = None) -> SaveResult:
        with self._lock:
            reg = self.get_registry()
            existing = reg.get(page.page_id)
            current_rev = existing["revision"] if existing else 0
            if expected_revision is not None and expected_revision != current_rev:
                raise StaleRevisionError(
                    f"page {page.page_id} expected_revision={expected_revision} 实际={current_rev}"
                )
            page.revision = current_rev + 1
            page.updated_at = page.updated_at or ""
            path = self._page_path(page.page_type, page.title)
            self._assert_inside_wiki(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            frontmatter = {k: v for k, v in page.to_dict().items() if k != "body"}
            write_markdown(path, frontmatter, page.body)
            rel = self._rel(path)
            reg[page.page_id] = PageRegistryEntry(
                path=rel, title=page.title, page_type=page.page_type.value,
                revision=page.revision, content_hash=page.content_hash,
            ).to_dict()
            self._write_registry(reg)
            event_type = "page.created" if current_rev == 0 else "page.updated"
            self._append_outbox({"type": event_type, "page_id": page.page_id, "revision": page.revision, "path": rel})
            return SaveResult(ok=True, object_id=page.page_id, revision=page.revision, outbox_events=[event_type])

    def move_page(self, page_id: str, new_title: str, new_page_type: str | None = None) -> SaveResult:
        with self._lock:
            page = self.get_page(page_id)
            if not page:
                raise KeyError(f"page not found: {page_id}")
            reg = self.get_registry()
            old_rel = reg[page_id]["path"]
            old_path = self._wiki_dir / old_rel
            page.title = new_title
            if new_page_type:
                page.page_type = PageType(new_page_type)
            r = self.save_page(page, expected_revision=page.revision - 1)
            # save_page 已写新路径;删旧文件 + 记 redirect
            if (self._wiki_dir / old_rel) != self._page_path(page.page_type, new_title):
                with contextlib.suppress(FileNotFoundError):
                    old_path.unlink()
                red = self.get_redirects()
                red[old_rel] = page_id
                self._write_redirects(red)
            return r

    # ---- claim CRUD ----
    def get_claim(self, claim_id: str) -> Claim | None:
        path = self._claim_path(claim_id)
        if not path.exists():
            return None
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError:
            return None
        if not isinstance(data, dict):
            return None
        try:
            return Claim.from_dict(data, strict=False)
        except (ValueError, TypeError):
            return None

    def save_claim(self, claim: Claim, expected_revision: int | None = None) -> SaveResult:
        with self._lock:
            existing = self.get_claim(claim.claim_id)
            current_rev = existing.revision if existing else 0
            if expected_revision is not None and expected_revision != current_rev:
                raise StaleRevisionError(
                    f"claim {claim.claim_id} expected_revision={expected_revision} 实际={current_rev}"
                )
            claim.revision = current_rev + 1
            claim.updated_at = claim.updated_at or ""
            path = self._claim_path(claim.claim_id)
            self._assert_inside_wiki(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp_fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".yaml.tmp")
            try:
                with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                    yaml.safe_dump(claim.to_dict(), f, allow_unicode=True, default_flow_style=False, sort_keys=False)
                os.replace(tmp, path)
            except BaseException:
                with contextlib.suppress(OSError):
                    os.unlink(tmp)
                raise
            event_type = "claim.created" if current_rev == 0 else "claim.updated"
            self._append_outbox({"type": event_type, "claim_id": claim.claim_id, "revision": claim.revision})
            return SaveResult(ok=True, object_id=claim.claim_id, revision=claim.revision, outbox_events=[event_type])

    def delete_claim(self, claim_id: str, soft: bool = True) -> SaveResult:
        with self._lock:
            path = self._claim_path(claim_id)
            if not path.exists():
                return SaveResult(ok=False, object_id=claim_id, revision=0, warnings=["claim not found"])
            if soft:
                claim = self.get_claim(claim_id)
                if claim:
                    from src.models.wiki_v2 import ClaimStatus
                    claim.status = ClaimStatus.RETRACTED
                    self.save_claim(claim, expected_revision=claim.revision - 1)
            else:
                path.unlink()
            self._append_outbox({"type": "claim.deleted", "claim_id": claim_id, "soft": soft})
            return SaveResult(ok=True, object_id=claim_id, revision=0)

    # ---- transaction ----
    @contextlib.contextmanager
    def transaction(self) -> Iterator[WikiTransaction]:
        tx_id = uuid.uuid4().hex[:12]
        tx = WikiTransaction(self, tx_id)
        try:
            yield tx
        except BaseException:
            # 中途失败:丢弃 staging(本实现 stage 仅记录对象,commit 才落盘,故无残留)
            raise
        else:
            tx.commit()


def new_page_id() -> str:
    return f"page_{uuid.uuid4()}"


def new_claim_id() -> str:
    return f"claim_{uuid.uuid4()}"
```

> 实现者注：`transaction` 当前实现是"stage 仅记对象、commit 才落盘"的轻量事务（中途失败天然无残留）。spec §14.1 要求"多文件 transaction 在 `_staging/<tx_id>` 完成后再发布"——若 review 要求严格 staging 落盘，可在 Phase 2/4 增强（把 stage 改为先写 `_staging/<tx_id>/`，commit 时 os.replace）。本轮以满足测试为准，偏差在 commit body 注明。

- [ ] **Step 5：container 注入 + 删死代码（C2）**

Modify `src/core/container.py`：
1. 删除 `wiki_workflow` lazy property（死代码，零 caller；先 `impact` 确认）。
2. 新增 `wiki_repository` lazy property：

```python
@property
def wiki_repository(self):
    if self._wiki_repository is None:
        from src.services.wiki_repository import WikiRepository
        from src.utils.config import Config
        wiki_dir = Config.get("knowledge_workflow.wiki_dir", "wiki")
        wiki_dir_path = Path(wiki_dir)
        self._wiki_repository = WikiRepository(
            wiki_dir=wiki_dir_path,
            registry_path=wiki_dir_path / "_meta" / "pages.json",
            redirects_path=wiki_dir_path / "_meta" / "redirects.json",
            outbox_path=Path(Config.get("storage.data_dir", "data")) / "wiki_projection_outbox.jsonl",
        )
    return self._wiki_repository
```

（`_wiki_repository` 字段加到 dataclass 默认 `None`；`Path` 已在 container 顶部导入。）

- [ ] **Step 6：跑测试确认通过**

Run: `python -m pytest tests/test_wiki_repository.py -v`
Expected: 11 passed。

- [ ] **Step 7：Phase 1 全量回归 + detect_changes**

```bash
python -m pytest tests/ -q
ruff check src tests evals tools scripts
mypy src tools
```
gitnexus: `detect_changes()`
Expected: 全量 pytest 通过（基线 + Phase0/1 新增约 26 测试）；ruff/mypy 净增 0 错误；detect_changes risk LOW，无预期外传播。

- [ ] **Step 8：commit**

```bash
git add src/services/wiki_repository.py src/core/container.py tests/test_wiki_repository.py
git commit -m "feat(wiki-v2): add filesystem canonical repository"
```

---

## Phase 2-6：任务级纲要（执行到时展开 bite-sized）

> 每个 Phase 动工前先出该 Phase 的 TDD 子计划并审批（沿用本纲要为骨架）。下表每 Task 给：Files / Interfaces（Consumes·Produces）/ 测试要求 / 验收 / commit / 依赖。

### Phase 2：SQLite Projection 与兼容读取（风险中）

| Task | Files | Interfaces | 测试要求 | 验收 | commit | 依赖 |
|---|---|---|---|---|---|---|
| **T2.1** v2 projection migration | Create `alembic/versions/j001_wiki_v2_projection.py`；Modify `src/services/db.py` `_SCHEMA` 同步加 6 表 | Produces: `wiki_pages_v2`/`wiki_claims`/`wiki_claim_evidence`/`wiki_page_claims`/`wiki_dependencies`/`wiki_projection_state` 表 | Test `tests/test_wiki_v2_migration.py`：幂等；新表存在；旧表不删；空库/有库均可升；downgrade 删新表不碰旧表 | `alembic upgrade head` + `alembic downgrade -1` 双向通过 | `feat(wiki-v2): add canonical projection schema` | T1.1 |
| **T2.2** Projection Service | Create `src/services/wiki_projection.py`；Modify `container.py` 加 `wiki_projection` property | Consumes: outbox + Repository；Produces: SQLite v2 行 + FTS | Test `tests/test_wiki_projection.py`：page/claim/evidence 投影；事件重复消费幂等；中途失败重试；全量 rebuild；parity；canonical 删→projection 清；FTS 可搜 | `shinehe wiki sync-index` 全量重建后 parity=100% | `feat(wiki-v2): add idempotent sqlite projection` | T1.3, T2.1 |
| **T2.3** Page Locator 切稳定 ID | Modify `src/services/wiki_page_locator.py`；相关 RAG 测试 | Produces: 候选 id = canonical `page_id`；projection 优先 + FS fallback | 候选 id 为 `page_id`；projection 正常用 projection；不可用 fallback 文件（warning）；SizeAwareRouter 不退化 | 现有 wiki 检索测试不退化；fallback 入 warnings | `refactor(wiki-v2): resolve wiki candidates by stable page id` | T2.2 |

**Phase 2 回归门禁**：`pytest` + `ruff` + `mypy` + `run_retrieval_eval --all --fake-embedding`（不低于基线）。

### Phase 3：Claim 抽取与跨来源合并（风险中高，核心复杂点）

| Task | Files | Interfaces | 测试要求 | 验收 | commit | 依赖 |
|---|---|---|---|---|---|---|
| **T3.1** Claim Extractor | Create `src/services/wiki_claim_extractor.py` + prompt/schema 文件 | Consumes: knowledge_id + blocks + source summary + 候选 Claims；Produces: `ClaimExtractionResult(extracted_claims, skipped_fragments, llm_calls, warnings)` | Test `tests/test_wiki_claim_extractor.py`：多 block 抽取；每 Claim 带 Evidence；location 保留；LLM 非 JSON；LLM 超时；超 max_claims；重复句去重；无可验证事实返回空；**LLM 失败不阻断 raw 索引** | mock + 真实 LLM（本机有 Key）各跑通 | `feat(wiki-v2): extract evidence-backed claims from source blocks` | T1.1 |
| **T3.2** Claim Matcher | Create `src/services/wiki_claim_matcher.py` | Consumes: 新 Claim + 候选；Produces: `ClaimMatchDecision(action, target_claim_id, score, reasons)`，action ∈ new/supports/refines/contradicts/supersedes/duplicate/unresolved | Test fixture：完全相同→duplicate/supports；同义→supports；数值冲突→contradicts；时间更新→supersedes；补充限定→refines；低置信→unresolved；阈值用 config `wiki.claims.*` | 7 fixture 全分类正确 | `feat(wiki-v2): classify cross-source claim merge actions` | T3.1 |
| **T3.3** Merge Engine | Create `src/services/wiki_merge_engine.py` | Consumes: extraction + match decisions + Repository transaction；Produces: 更新 Evidence/Claim 状态/Page claim_ids + 人类可读 diff + review item | Test `tests/test_wiki_merge_engine.py`：supports 只加 Evidence；duplicate 不新增；contradicts 标 disputed；supersedes 建 relation；transaction rollback；page claim_ids 更新；diff 稳定 | E2E-1（两来源支持同事实→1 Claim 2 Evidence）可跑 | `feat(wiki-v2): merge claims without whole-page overwrite` | T3.1, T3.2 |

**Phase 3 回归门禁**：+ `pytest` 含 E2E-1/E2E-2 场景。

### Phase 4：主工作流切换与双轨收敛（风险高，强回归）

| Task | Files | Interfaces | 测试要求 | 验收 | commit | 依赖 |
|---|---|---|---|---|---|---|
| **T4.1** 重构 KnowledgeWorkflowService | Modify `knowledge_workflow.py` + container + integration tests | 流程：source compiler→claim extractor→matcher→merge→page composer→repository tx→index/log→outbox；返回 +`claims_created/updated/conflicts/pages_updated/projection_pending/review_items` | ingest 产 canonical Page/Claim；失败不破坏 raw 检索；index.md/log.md 更新；projection_pending 可见；同来源重复 ingest 幂等 | T0.2 守卫 allowlist 中 `knowledge_workflow.write_markdown`/`wiki_source_compiler.write_markdown` 移除 | `refactor(wiki-v2): compile ingest into canonical claims and pages` | T3.3, T2.2 |
| **T4.2** 改造 WikiWriteService | Modify `wiki_write_service.py` + `rag_pipeline.py` + MCP save handler | 双写分发器 → canonical 写入口；兼容字段：保留 `sqlite_page_id`(deprecated) + 新 `page_id`；`fs_saved`→`canonical_saved` | 不再双写两套；旧返回字段兼容；查询保存先 merge；低价值/重复跳过；canonical 成功 projection 失败返回 warning 非假失败 | E2E-6（projection 故障→projection pending→重试 parity 恢复） | `refactor(wiki-v2): route query saves through canonical repository` | T4.1 |
| **T4.3** WikiCompiler 降级适配器 | Modify `wiki_compiler.py` + deprecation tests | `ingest()`→委托 `KnowledgeWorkflowService.compile()`；`save_answer()`→委托 `WikiWriteService.save()`；保留旧 API + deprecation warning | 旧 API 可用；不直接写旧表（守卫 allowlist 移除 `wiki_compiler.insert_wiki_page`）；warning 可测；现有 MCP/GUI 不崩 | T0.2 守卫 allowlist 清空 `wiki_compiler` 条目 | `refactor(wiki-v2): convert legacy compiler to compatibility adapter` | T4.1, T4.2 |

**Phase 4 回归门禁**：全量 `pytest` + `run_retrieval_eval` + `run_wiki_eval` + 真实 MCP `save_to_wiki`/`ask` 冒烟；守卫测试 allowlist 大幅收缩。

### Phase 5：依赖图与自动失效传播（风险高，影响范围大）

| Task | Files | Interfaces | 测试要求 | 验收 | commit | 依赖 |
|---|---|---|---|---|---|---|
| **T5.1** Dependency Service | Create `wiki_dependency_service.py` + tests | Produces: `get_impacted_by_source(kid)`/`get_impacted_by_claim(cid)`/拓扑有序 rebuild plan；防环 | source→evidence→claim→page；多来源；环检测；最大深度；删除来源影响集；拓扑稳定 | E2E-4（删 A 仍 active，剩 B） | `feat(wiki-v2): build source claim page dependency graph` | T2.2 |
| **T5.2** Rebuild Service | Create `wiki_rebuild_service.py` + job integration + tests | 来源更新/删除后级联：staging 重编译→diff→publish/review→projection refresh | source update；source delete；unchanged block 不重编译；unsupported Claim；affected page review；staging validation；job cancel；max_pages 保护 | E2E-3（来源更新→stale/removed Evidence→unsupported→review） | `feat(wiki-v2): propagate source changes through affected knowledge` | T5.1 |
| **T5.3** Watcher/Reindex 接入 | Modify `path_indexer.py`/`file_watcher.py`/indexing job | 文件变更→自动 rebuild job | 修改文件→rebuild job；删除文件→Evidence 处理；debounce 不重复；watcher 失败不阻断主进程 | 手动改文件触发 rebuild 冒烟 | `feat(wiki-v2): trigger incremental knowledge rebuild from source changes` | T5.2 |

**Phase 5 回归门禁**：全量 + E2E-3/E2E-4。

### Phase 6：Migration、Lint、反馈与评测（风险中高）

| Task | Files | Interfaces | 测试要求 | 验收 | commit | 依赖 |
|---|---|---|---|---|---|---|
| **T6.1** 迁移器 | Create `wiki_v2_migrator.py` + CLI + fixtures + tests | A/B 轨 → canonical；`migrate-v2 --dry-run/--apply/--rollback` | A-only/B-only/A-B 可匹配/同名冲突/无来源事实/dry-run 零写/apply/rollback/migration lock/Windows rename | cutover 条件（spec §11.3）可检测 | `feat(wiki-v2): migrate dual-track wiki into canonical store` | T4.3 |
| **T6.2** Validator/Lint 集成 | Modify `wiki_fs_lint.py`/`wiki_lint.py`/CLI/MCP | 新 finding category：schema_invalid/claim_missing/evidence_missing/evidence_stale/projection_drift/registry_drift/publish_gate_violation | 所有新 category 可测；`--strict` 非零退出；不破坏 LintReport 字段；projection drift 可检测修复 | `shinehe wiki validate --strict` | `feat(wiki-v2): validate claim provenance and projection parity` | T1.2 |
| **T6.3** 用户反馈 | Create `wiki_feedback_service.py` + MCP/API + tests | confirm/reject/correct/needs_review → Claim 状态 + operation log | 反馈→operation log + Claim 状态变化；不改 raw | 4 种反馈行为 | `feat(wiki-v2): apply user feedback to canonical claims` | T4.2 |
| **T6.4** 知识演进评测 | Create `evals/run_knowledge_evolution_eval.py` + fixtures | 10 项指标（spec §13 门槛） | Claim Provenance≥0.95；Evidence Location≥0.90；Cross-source Merge≥0.85；Update Propagation=1.00；Unsupported Detection≥0.95；Page Identity Stability=1.00；Migration Page Parity=1.00；Projection Parity=1.00；Retrieval/No-answer 不低于基线 | 10 项达标 | `test(wiki-v2): add knowledge evolution evaluation suite` | T6.1 |

**Phase 6 回归门禁**：全量 + retrieval eval + wiki eval + **knowledge evolution eval**；C3 收敛（`PAGE_TYPE_DIRS`/`WIKI_FIRST_DIRS` 引用 `wiki_v2.PAGE_TYPES`）；版本升 `1.6.0`；文档更新（README/PROGRESS/docs/wiki/canonical-v2.md/docs/migration/wiki-v2-migration.md）。

---

## 回归门禁（每 Phase 结束强制）

```bash
python -m pytest tests/ -q
ruff check src tests evals tools scripts
mypy src tools
python evals/run_retrieval_eval.py --all --fake-embedding --baseline evals/baselines/local.json --max-regression 0.05
python evals/run_wiki_eval.py --source fs
```

Phase 6 追加：`python evals/run_knowledge_evolution_eval.py`。

原则：不允许"既有失败"为由增加新失败；基线债务 Phase 0 已记录；新改动净增 0 lint/type 错误；retrieval 不得静默下降；v2 门控关闭时 legacy 零变化。

---

## 风险登记表

| 风险 | 等级 | 缓解 |
|---|---|---|
| Claim 语义合并误判（Phase 3） | 高 | unresolved 阈值保守（0.72）；低置信进 review 不自动合并；E2E-2 锁定冲突；真实 LLM Key 可跑 fixture |
| 主写路径切换破坏检索（Phase 4） | 高 | 三层门控（config+mode+stage）；失败隔离（raw 索引成功后 wiki 失败不破坏搜索）；T0.2 守卫逐步收紧；每步全量回归 |
| 来源变更传播风暴（Phase 5） | 高 | max_depth=5；max_pages_per_job=100；防环；debounce；job cancel |
| 迁移不可逆 / 数据丢失（Phase 6） | 中高 | migration lock；自动备份 data/+wiki/；dry-run 零写；rollback 命令；parity 100% 才 cutover |
| 旧表与新 projection 长期漂移 | 中 | projection outbox + 定期 `sync-index`；projection_drift lint；health 暴露 lag |
| `wiki_repository` 与旧 `repositories/wiki_repo.py` 同名混淆（C1） | 中 | 命名区分（property `wiki_repository` vs 字段 `wiki_repo`）；旧类 deprecated docstring；Phase 6 评估改名 |
| transaction staging 非严格（T1.3 偏差） | 低 | 本轮轻量事务满足测试；Phase 2/4 按 review 要求增强为 `_staging/<tx_id>` 落盘 |
| Windows 路径（repo 测试 `test_windows_path_compat`） | 低 | registry 用 `/`；`_rel()` 强制 replace；路径测试覆盖 |

---

## Definition of Done（spec §17，18 项）

1. 所有新 Wiki 写入只经过 `WikiRepository`；2. Markdown canonical 唯一权威；3. SQLite v2 projection 可删后完整重建；4. 页面稳定 page ID；5. 每条 active Claim ≥1 有效 Evidence；6. Evidence 能定位 block 时不退化；7. 同一事实多来源不重复 Claim；8. 来源更新/删除可算完整影响集；9. unsupported/disputed Claim 触发页面 review；10. Query save 能合并已有页；11. migration dry-run/apply/rollback 全可用；12. legacy 模式不变；13. 核心 MCP 契约不破坏；14. 测试/ruff/mypy 全绿；15. 三组 eval 达标；16. 文档/配置/版本一致；17. 不删旧表、不可逆迁移；18. 完整 migration report + rollback 证据。

---

## 执行交接

**本轮（Phase 0-1）立即以 subagent-driven-development 执行：** 每 Task 派 fresh subagent → 两阶段 review（实现审查 + spec 符合性）→ 通过则进下一 Task。Phase 2-6 在 Phase 1 回归绿后，逐 Phase 出 TDD 子计划审批再执行。
