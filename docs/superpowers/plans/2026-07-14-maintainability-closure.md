# Maintainability Closure（v1.9.x → v1.10.0）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans。Steps 使用 checkbox（`- [ ]`）追踪。  
> **Spec:** `docs/superpowers/specs/04-maintainability-closure-spec.md`  
> **基线版本:** v1.9.0 / `master`  
> **目标版本:** v1.9.1（WP0+WP1）→ v1.9.2（WP2–WP4）→ v1.10.0（WP5）  
> **分支建议:** `feat/maintainability-closure-wp0-wp1`（首批）

**Goal:** 把 v1.9.0 的“安全过渡架构”收束为可长期维护的正式架构：Unified Retrieval 成为默认权威路径，Answer 归位，MCP 工具实拆，Container 拥有构造/生命周期，Alembic 门禁真实可信，最终删除 Legacy 双路径。

**Architecture:** 按 WP0→WP5 严格串行。检索正式路径为 `RetrievalOrchestrator + Policy + RawRetriever + VerifiedProvider + Fusion`；`SearchService` 退化为 Facade；Answer 业务在 `src/answering/`；MCP `server.py` 只做协议与注册；Container 能力组 Provider 拥有构造；写模式启动校验 Alembic head。

**Tech Stack:** Python 3.10+、pytest、ruff、mypy、Alembic、FastMCP、现有 HybridSearcher / VerifiedHybridFusion / AppContainer

**首批强制范围（Spec §17）：** 仅执行 WP0-T1、WP0-T2、WP1-T1。完成后提交阶段报告并停止；未确认 Raw 等价前不得做 Fusion 迁移与 Unified 默认切换。

---

## 0. 现状快照（2026-07-14 勘察）

| 项 | 现状 | 收尾目标 |
|---|---|---|
| `retrieval.orchestrator` | example 默认 `legacy`；本地 Config 可能为 `None`→代码默认 | WP1 后默认 `unified` |
| `RawRetriever` | 适配器：调 `SearchService.run_raw_retrieval_adapter` | 算法权威，显式依赖构造 |
| Policy | 回调 `execute_evidence_only` / `execute_verified` | 直接组合 Raw/Verified/Fusion |
| `SearchService` | ~835 行，持有 `_search_legacy_pipeline` / `_search_verified_hybrid` | Facade + compatibility |
| Answer | `src/answering/` 有 service；assemble 仍在 `verified_answer.py` | assemble 全迁 answering |
| MCP `server.py` | ~3628 行，工具主体仍在此 | ≤500 行注册层 |
| `src/mcp/tools/*` | 仅名称常量 | 真实工具实现 |
| Container groups | 只读视图 | Provider 拥有构造/生命周期 |
| Alembic env | 只读 Config db path，忽略 `SHINEHE_TEST_ALEMBIC_URL` | 显式 URL 优先级 |
| `test_alembic_baseline` | 失败可 soft-skip | 普通失败必须 fail |

**非目标（禁止顺带）：** 新 MCP 工具、改 RRF/Gate/算法、换 SQLite/FastMCP、一次性清空 `db.py`、无 Shadow 证据删 Legacy。

---

## 1. 文件地图（全生命周期）

### WP0 基线

| 动作 | 路径 | 职责 |
|---|---|---|
| Create | `docs/superpowers/reviews/maintainability-closure-baseline.md` | 可执行验收基线 |
| Create | `tools/report_closure_debt.py` | 架构欠账扫描 CLI |
| Create | `tests/architecture/test_closure_debt_baseline.py` | 欠账快照/趋势测试 |

### WP1 Retrieval 收束

| 动作 | 路径 | 职责 |
|---|---|---|
| Rewrite | `src/retrieval/raw_retriever.py` | Raw 算法权威实现 |
| Create | `src/retrieval/fusion.py` | Claim/Raw fusion 编排 |
| Create | `src/retrieval/packaging.py` | candidate packaging / SearchExecution 组装辅助 |
| Create | `src/retrieval/deadlines.py` | stage timeout 工具（可从 SearchService 抽出） |
| Modify | `src/retrieval/policies/evidence_only.py` | 直接 `RawRetriever.retrieve` |
| Modify | `src/retrieval/policies/verified.py` | Raw + VerifiedProvider + Fusion |
| Modify | `src/retrieval/orchestrator.py` | 注入真实组件而非整包 SearchService |
| Create | `src/compatibility/legacy_retrieval.py` | Legacy 主管线迁出 |
| Modify | `src/services/search_service.py` | Facade + 委托 |
| Create | `tools/run_retrieval_shadow_eval.py` | 聚合 Shadow 报告 |
| Modify | `config.example.yaml` / Config 默认 | `retrieval.orchestrator: unified` |

### WP2 Answer + MCP

| 动作 | 路径 | 职责 |
|---|---|---|
| Create | `src/answering/assembler.py` 等 | Answer 领域逻辑 |
| Rewrite | `src/services/verified_answer.py` | 兼容 re-export |
| Create | `src/application/*_commands.py` | MCP 业务命令服务 |
| Rewrite | `src/mcp/tools/*.py` | 真实工具实现 |
| Shrink | `src/mcp/server.py` | 仅注册/生命周期 |

### WP3 Container

| 动作 | 路径 | 职责 |
|---|---|---|
| Rewrite | `src/core/service_groups.py` | Provider 构造 |
| Modify | `src/core/container.py` | 扁平属性代理 + 全局访问限制 |
| Create | `src/compatibility/container_access.py` | 白名单全局访问 |

### WP4 Alembic + Repository

| 动作 | 路径 | 职责 |
|---|---|---|
| Modify | `alembic/env.py` | 显式 URL 优先级 |
| Create | `tests/migrations/*` | empty/v1.9/idempotent/recovery |
| Create | `src/storage/migration_status.py` / `startup_gate.py` | 写模式 head gate |
| Create | 最小 Repository | 消除 MCP/Application 直接 SQL |

### WP5 Legacy 删除

| 动作 | 路径 | 职责 |
|---|---|---|
| Delete | compatibility legacy 主管线 / 伪双路径 | 清零架构债 |
| Modify | 文档 / VERSION 1.10.0 | 最终发布 |

---

## 2. 发布列车与停止规则

| 版本 | 内容 | 删除 Legacy？ |
|---|---|---|
| **v1.9.1** | WP0 + WP1（Unified 默认，Legacy 可回滚） | **否** |
| **v1.9.2** | WP2 + WP3 + WP4 | **否**（除非已满观察周期） |
| **v1.10.0** | WP5 删除与最终验收 | **是**（前置条件全满足） |

**停止条件（任一命中立即停）：** 契约变化、Hybrid Eval 下降、Claim/Evidence 关系变、单 Task 改 >15 个无直接关系生产文件、Unified/Legacy 差异无法解释、同版本既切默认又删回滚。

**Agent 单 Task 流程：** 搜调用方 → 先补测试 → 基线 → 最小改动 → targeted tests → 全量 → ruff/mypy → eval（若触及检索）→ 报告 → 单一职责提交。

---

# WP0：可执行验收基线

### Task 0.1：运行全量门禁并写 baseline 报告

**Files:**
- Create: `docs/superpowers/reviews/maintainability-closure-baseline.md`

- [ ] **Step 1: 记录环境元数据**

```bash
git rev-parse HEAD
python --version
python -c "import platform; print(platform.platform())"
python -c "from src.utils.config import Config; Config.load(); print(Config.get('retrieval.orchestrator')); print(Config.get('answer.orchestrator'))"
```

- [ ] **Step 2: 跑全量 pytest**

```bash
python -m pytest tests/ -q --tb=no 2>&1 | Tee-Object -FilePath docs/superpowers/reviews/_baseline_pytest.txt
```

Expected: 记录 passed/failed/skipped；若 failed>0 则 **停止改造**，先修基线。

- [ ] **Step 3: 静态检查**

```bash
ruff check src tests evals tools scripts
python -m mypy src tools --ignore-missing-imports
```

- [ ] **Step 4: Eval**

```bash
python evals/run_retrieval_eval.py --all --fake-embedding --baseline evals/baselines/local.json --max-regression 0.05
python evals/run_hybrid_eval.py --strict
```

- [ ] **Step 5: 写报告**

报告模板必须含：Git SHA、Python 版本、OS、pytest 统计、每个 skip 原因、Retrieval/Hybrid 指标、orchestrator 有效值、结论（PASS/FAIL）。

- [ ] **Step 6: Commit（可与 Task 0.2 合并一次 commit）**

```bash
git add docs/superpowers/reviews/maintainability-closure-baseline.md
git commit -m "test(closure): capture executable maintainability baseline"
```

---

### Task 0.2：架构欠账快照工具与测试

**Files:**
- Create: `tools/report_closure_debt.py`
- Create: `tests/architecture/test_closure_debt_baseline.py`

- [ ] **Step 1: 写失败测试（工具尚不存在时 import 失败或 metrics 键缺失）**

```python
# tests/architecture/test_closure_debt_baseline.py
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.report_closure_debt import collect_debt_metrics


REQUIRED_KEYS = {
    "mcp_server_lines",
    "mcp_server_tool_functions",
    "mcp_tools_real_impl_count",
    "database_instance_refs_src",
    "get_active_container_refs_src",
    "search_service_has_legacy_pipeline",
    "search_service_has_verified_hybrid",
    "raw_retriever_calls_search_service",
    "answering_depends_on_verified_answer",
    "alembic_env_reads_test_url",
    "migration_tests_have_skip_paths",
}


def test_collect_debt_metrics_has_required_keys():
    metrics = collect_debt_metrics(ROOT)
    missing = REQUIRED_KEYS - set(metrics)
    assert not missing, f"missing keys: {missing}"


def test_debt_metrics_are_non_negative_counts():
    metrics = collect_debt_metrics(ROOT)
    for key in (
        "mcp_server_lines",
        "mcp_server_tool_functions",
        "mcp_tools_real_impl_count",
        "database_instance_refs_src",
        "get_active_container_refs_src",
    ):
        assert isinstance(metrics[key], int)
        assert metrics[key] >= 0


def test_baseline_reflects_current_debt_shape():
    """初始阶段：报告债务存在，不要求为零。"""
    m = collect_debt_metrics(ROOT)
    # v1.9.0 已知形状：server 巨大；tools 几乎无实现；Raw 仍适配 SearchService
    assert m["mcp_server_lines"] > 500
    assert m["mcp_tools_real_impl_count"] == 0
    assert m["search_service_has_legacy_pipeline"] is True
    assert m["raw_retriever_calls_search_service"] is True
    assert m["alembic_env_reads_test_url"] is False
```

- [ ] **Step 2: 运行确认失败**

```bash
python -m pytest tests/architecture/test_closure_debt_baseline.py -v
```

Expected: FAIL（`tools.report_closure_debt` 不存在或函数缺失）

- [ ] **Step 3: 实现 `tools/report_closure_debt.py`**

```python
#!/usr/bin/env python3
"""Report maintainability-closure architecture debt metrics.

Usage:
  python tools/report_closure_debt.py
  python tools/report_closure_debt.py --json
  python tools/report_closure_debt.py --strict   # WP5: exit 1 if any debt remains
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path
from typing import Any


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _count_regex(text: str, pattern: str) -> int:
    return len(re.findall(pattern, text))


def _count_tool_functions_in_server(text: str) -> int:
    """Heuristic: FastMCP tool handlers defined in server.py."""
    # Count functions that look like registered tools (mcp.tool or def kb_/search/ask...)
    tree = ast.parse(text)
    count = 0
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            name = node.name
            if name.startswith("_"):
                continue
            # skip obvious non-tools
            if name in {"main", "create_app", "lifespan"}:
                continue
            # count decorated or public handlers living in monolith
            has_tool_decorator = any(
                (isinstance(d, ast.Attribute) and d.attr == "tool")
                or (isinstance(d, ast.Call) and (
                    (isinstance(d.func, ast.Attribute) and d.func.attr == "tool")
                    or (isinstance(d.func, ast.Name) and d.func.id in {"tool", "mcp_tool"})
                ))
                for d in node.decorator_list
            )
            if has_tool_decorator or name in {
                "ping", "search", "ask", "read", "list_knowledge",
                "index_path", "get_job", "list_jobs", "reindex_all",
            }:
                count += 1
    return count


def _count_real_tool_impls(tools_dir: Path) -> int:
    """Count non-trivial function defs in tools/*.py excluding name-only modules."""
    total = 0
    if not tools_dir.is_dir():
        return 0
    for path in tools_dir.glob("*.py"):
        if path.name == "__init__.py":
            continue
        text = _read(path)
        # Name-only modules are tiny (<400 chars) or only assign TOOL_NAMES / lists
        if len(text.strip()) < 400:
            continue
        tree = ast.parse(text)
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not node.name.startswith("_"):
                    total += 1
    return total


def _src_py_files(root: Path) -> list[Path]:
    return [p for p in (root / "src").rglob("*.py") if "__pycache__" not in p.parts]


def collect_debt_metrics(root: Path) -> dict[str, Any]:
    root = root.resolve()
    server = root / "src" / "mcp" / "server.py"
    server_text = _read(server) if server.exists() else ""
    tools_dir = root / "src" / "mcp" / "tools"
    search_svc = root / "src" / "services" / "search_service.py"
    search_text = _read(search_svc) if search_svc.exists() else ""
    raw_ret = root / "src" / "retrieval" / "raw_retriever.py"
    raw_text = _read(raw_ret) if raw_ret.exists() else ""
    answering_dir = root / "src" / "answering"
    alembic_env = root / "alembic" / "env.py"
    alembic_text = _read(alembic_env) if alembic_env.exists() else ""
    alembic_test = root / "tests" / "test_alembic_baseline.py"
    alembic_test_text = _read(alembic_test) if alembic_test.exists() else ""

    db_instance_refs = 0
    gac_refs = 0
    for path in _src_py_files(root):
        text = _read(path)
        db_instance_refs += _count_regex(text, r"Database\._instance")
        gac_refs += _count_regex(text, r"get_active_container\s*\(")

    answering_dep = False
    if answering_dir.is_dir():
        for path in answering_dir.rglob("*.py"):
            if "verified_answer" in _read(path):
                answering_dep = True
                break

    metrics: dict[str, Any] = {
        "mcp_server_lines": server_text.count("\n") + (1 if server_text and not server_text.endswith("\n") else 0),
        "mcp_server_tool_functions": _count_tool_functions_in_server(server_text) if server_text else 0,
        "mcp_tools_real_impl_count": _count_real_tool_impls(tools_dir),
        "database_instance_refs_src": db_instance_refs,
        "get_active_container_refs_src": gac_refs,
        "search_service_has_legacy_pipeline": "def _search_legacy_pipeline" in search_text,
        "search_service_has_verified_hybrid": "def _search_verified_hybrid" in search_text,
        "raw_retriever_calls_search_service": (
            "SearchService" in raw_text
            or "run_raw_retrieval_adapter" in raw_text
            or "self._svc" in raw_text
        ),
        "answering_depends_on_verified_answer": answering_dep,
        "alembic_env_reads_test_url": "SHINEHE_TEST_ALEMBIC_URL" in alembic_text,
        "migration_tests_have_skip_paths": "pytest.skip" in alembic_test_text,
    }
    return metrics


def _strict_failures(metrics: dict[str, Any]) -> list[str]:
    fails = []
    if metrics["mcp_server_lines"] > 500:
        fails.append(f"mcp_server_lines={metrics['mcp_server_lines']} > 500")
    if metrics["mcp_server_tool_functions"] > 0:
        fails.append("mcp_server still defines tool functions")
    if metrics["mcp_tools_real_impl_count"] <= 0:
        fails.append("mcp tools have no real implementations")
    if metrics["database_instance_refs_src"] > 0:
        fails.append(f"Database._instance refs={metrics['database_instance_refs_src']}")
    # get_active_container: WP5 allows whitelist; strict full zero is aspirational
    if metrics["search_service_has_legacy_pipeline"]:
        fails.append("SearchService still has _search_legacy_pipeline")
    if metrics["search_service_has_verified_hybrid"]:
        fails.append("SearchService still has _search_verified_hybrid")
    if metrics["raw_retriever_calls_search_service"]:
        fails.append("RawRetriever still depends on SearchService")
    if metrics["answering_depends_on_verified_answer"]:
        fails.append("answering still depends on verified_answer")
    if not metrics["alembic_env_reads_test_url"]:
        fails.append("alembic/env.py does not honor SHINEHE_TEST_ALEMBIC_URL")
    if metrics["migration_tests_have_skip_paths"]:
        fails.append("migration tests still soft-skip failures")
    return fails


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Report maintainability closure debt")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--strict", action="store_true", help="exit 1 if residual debt")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)

    metrics = collect_debt_metrics(args.root)
    if args.json:
        print(json.dumps(metrics, indent=2, ensure_ascii=False))
    else:
        print("Maintainability Closure Debt Report")
        print("=" * 40)
        for k, v in metrics.items():
            print(f"  {k}: {v}")
        fails = _strict_failures(metrics)
        print("-" * 40)
        if fails:
            print(f"Residual debt items: {len(fails)}")
            for f in fails:
                print(f"  - {f}")
        else:
            print("No residual debt (strict clean).")

    if args.strict:
        fails = _strict_failures(metrics)
        return 1 if fails else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 跑通测试与 CLI**

```bash
python -m pytest tests/architecture/test_closure_debt_baseline.py -v
python tools/report_closure_debt.py
```

Expected: tests PASS；CLI 打印已知债务（server 行数大、raw 适配、alembic 未读 test URL 等）。

- [ ] **Step 5: Commit**

```bash
git add tools/report_closure_debt.py tests/architecture/test_closure_debt_baseline.py docs/superpowers/reviews/maintainability-closure-baseline.md
git commit -m "test(closure): capture executable maintainability baseline"
```

**WP0 验收:** 基线报告可重复；一条命令输出欠账；基线失败则停。

---

# WP1：Retrieval 真正收束（首批仅 T1）

### Task 1.1：把 Raw 算法迁入 RawRetriever（**本批必做**）

**Files:**
- Rewrite: `src/retrieval/raw_retriever.py`
- Modify: `src/services/search_service.py`（`run_raw_retrieval_adapter` / `_search_legacy_pipeline` 委托新 RawRetriever，保持行为）
- Modify: `tests/retrieval/test_raw_retriever.py`
- Create: `tests/retrieval/test_raw_retriever_parity.py`（Legacy 适配路径 vs 独立 Raw 等价）

**原则:** 算法逐行等价；不改评分；`RawRetriever` **不**接收整个 `SearchService`；通过显式依赖构造。

- [ ] **Step 1: 写等价性测试（先失败或先锁现有适配行为）**

```python
# tests/retrieval/test_raw_retriever_parity.py
"""RawRetriever independent path must match SearchService adapter path."""
from unittest.mock import Mock, patch

from src.retrieval.raw_retriever import RawRetriever
from src.services.search_service import SearchService


def _make_service():
    config = Mock()
    config.get.side_effect = lambda key, default=None: {
        "rag.pipeline.stage_timeouts.query_rewrite": 5.0,
        "rag.pipeline.stage_timeouts.wiki_search": 5.0,
        "rag.pipeline.stage_timeouts.rerank": 5.0,
    }.get(key, default)
    db = Mock()
    db.search_wiki_fts.return_value = []
    db.get_knowledge.return_value = {"title": "Title"}
    hybrid = Mock()
    block_store = Mock()
    embedding = Mock()
    return SearchService(config, db, hybrid, block_store, embedding)


def test_independent_raw_retriever_matches_adapter_path():
    service = _make_service()
    hit = {
        "id": "b1",
        "text": "hello world",
        "metadata": {"page_id": "k1"},
        "rrf_score": 0.9,
    }
    with patch.object(service, "_rewrite_query", return_value=["q"]), \
         patch.object(service, "_hybrid_search", return_value=[hit]), \
         patch.object(service, "_rerank", return_value=[{**hit, "rerank_score": 0.95}]):
        via_adapter = service.run_raw_retrieval_adapter("q", top_k=3)

    # Independent constructor (post-migration)
    raw = RawRetriever(
        config=service._config,
        db=service._db,
        block_store=service._block_store,
        hybrid_searcher=service._hybrid,
        query_rewriter=service._rewrite_query,  # or dedicated callable
        reranker=lambda q, cands, top_k: service._rerank(q, cands, top_k),
        citation_builder_factory=None,
        stage_timeout_fn=service._stage_timeout,
        knowledge_fts_fn=service._knowledge_fts_search,
        wiki_search_fn=service._safe_wiki_search,
        package_raw_fn=service._package_raw_candidates,
        diversity_fn=service._diversity_filter,
    )
    with patch.object(service, "_rewrite_query", return_value=["q"]), \
         patch.object(service, "_hybrid_search", return_value=[hit]), \
         patch.object(service, "_rerank", return_value=[{**hit, "rerank_score": 0.95}]):
        via_raw = raw.retrieve("q", top_k=3)

    assert [c.get("block_id") or c.get("id") for c in via_raw.candidates] == [
        c.get("block_id") or c.get("id") for c in via_adapter.candidates
    ]
    assert via_raw.trace.get("stages", {}).keys() >= {"query_rewrite", "raw_retrieval"}
```

> 实现时以最终 `RawRetriever.__init__` 签名为准调整测试；核心断言是 **candidates 结构与 stage keys 与 adapter 一致**。

- [ ] **Step 2: 实现独立 `RawRetriever`**

目标构造（允许内部 helper，但禁止持有 SearchService）：

```python
class RawRetriever:
    def __init__(
        self,
        *,
        config,
        db,
        block_store,
        hybrid_searcher,
        query_rewriter=None,
        reranker=None,
        stage_timeout_fn=None,
        knowledge_fts_fn=None,
        wiki_search_fn=None,
        package_raw_fn=None,
        diversity_fn=None,
        citation_builder_factory=None,
    ):
        ...

    def retrieve(self, query: str, *, top_k: int = 5, include_legacy_wiki_fts: bool = True) -> RawRetrievalResult:
        # 迁移 _search_legacy_pipeline + packaging + stage timeouts 的等价逻辑
        ...
```

迁移职责清单：

1. query rewrite（超时回落原 query）
2. legacy wiki FTS（`include_legacy_wiki_fts` 可控）
3. hybrid search + BlockStore/Knowledge FTS fallback（现 `_raw_retrieve`）
4. timed rerank
5. diversity filter
6. package raw candidates
7. stage trace / warnings / fallbacks

- [ ] **Step 3: SearchService 委托**

```python
def run_raw_retrieval_adapter(...):
    return self._get_raw_retriever().retrieve(...)

def _search_legacy_pipeline(...):
    raw = self._get_raw_retriever().retrieve(query, top_k=top_k, include_legacy_wiki_fts=True)
    # 把 raw.candidates 写回 list，merge warnings/fallbacks/trace 到 state
    ...
```

Legacy 主路径行为保持：`execute_primary_legacy` / `execute_evidence_only` 结果不变。

- [ ] **Step 4: 更新旧 unit tests**

`tests/retrieval/test_raw_retriever.py` 改为构造独立 RawRetriever 或通过 SearchService 工厂获取；不再 `RawRetriever(service)`。

- [ ] **Step 5: 验证**

```bash
python -m pytest tests/retrieval/ tests/test_public_search_contract.py tests/test_public_ask_contract.py tests/test_wiki_serving_contract.py -q
python tools/report_closure_debt.py
```

Expected: 相关测试 PASS；debt 中 `raw_retriever_calls_search_service` 变为 **False**（若实现彻底）；Legacy 方法仍可存在于 SearchService。

- [ ] **Step 6: Commit**

```bash
git add src/retrieval/raw_retriever.py src/services/search_service.py tests/retrieval/
git commit -m "refactor(retrieval): move raw pipeline into RawRetriever"
```

**本批停止点:** Task 1.1 完成后写阶段报告，**不得**继续 Task 1.2+。

---

### Task 1.2：抽取 VerifiedFusion 与 Packaging（**下一批**）

**Files:** `src/retrieval/fusion.py`, `src/retrieval/packaging.py`  
从 `_search_verified_hybrid` 迁出 normalize/fuse/package/conflict/stale/fallback；**禁止改评分公式**。

提交: `refactor(retrieval): extract verified fusion and packaging`

### Task 1.3：Policy 直接组合组件（**下一批**）

```python
# EvidenceOnlyPolicy
raw = self.raw_retriever.retrieve(...)
return packaging.build_evidence_only_execution(raw)

# VerifiedPolicy
raw = self.raw_retriever.retrieve(...)
verified = self.verified_provider.serve(...)
return self.fusion.fuse(raw, verified, ...)
```

禁止再调 `execute_evidence_only` / `execute_verified`。

提交: `refactor(retrieval): make policies compose retrieval components directly`

### Task 1.4：SearchService Facade + compatibility（**下一批**）

创建 `src/compatibility/legacy_retrieval.py`；SearchService 仅 Facade + Orchestrator + 兼容 search()。

### Task 1.5：聚合 Shadow Eval（**下一批**）

`tools/run_retrieval_shadow_eval.py` → `evals/reports/retrieval-shadow-<date>.json`  
门槛：Top-5 overlap≥95%；Eligible Claim/Conflict/Fallback/Citation key 一致率 100%；Unsupported/Stale serving=0；P95≤+10%。

### Task 1.6：默认切 Unified（**Shadow 通过后**）

- `config.example.yaml` / Config 默认 / README / migration / release notes / tests  
- Legacy 仍可配置回滚  
- **不得**与 Legacy 删除同版本

提交: `feat(config): make unified retrieval the default`

**WP1 验收:** Policy 不依赖 SearchService 私有管线；RawRetriever 为 Raw 权威；Unified 默认；契约+Eval 不退化。

---

# WP2：Answer 归位 + MCP 实拆（v1.9.2）

### Task 2.1：Answer Assembler 迁移

- Create: `src/answering/assembler.py`, `citations.py`, `fallbacks.py`
- `verified_answer.py` 仅 re-export
- 清理伪 `answer.orchestrator` 双路径（无差异则只留 unified）

### Task 2.2：MCP 工具按域迁移

目标 `src/mcp/tools/{retrieval,ingest,administration,wiki,graph,memory,operations}.py` 含真实实现。  
`server.py` ≤500 行。

### Task 2.3：Application Services

`src/application/{knowledge,ingest,operation,wiki}_commands.py` + `tagging_service.py`  
MCP 只做：校验 → Write Policy → Service → Envelope。

### Task 2.4：清零 MCP 直接 DB 访问

`Database._instance` / SQL / `get_conn()` / 工具内 Wiki 文件操作 = 0。

### Task 2.5：MCP Contract 快照全绿

```bash
pytest tests/test_mcp_contract.py -q
```

---

# WP3：Container / 全局状态（v1.9.2）

### Task 3.1–3.4

- ServiceGroups → 真实 Provider（lazy 构造 + close）
- 扁平属性兼容代理；新代码强制 `container.core.*`
- `get_active_container` 仅 `src/mcp/runtime.py` + `src/compatibility/container_access.py`
- 生命周期与多实例隔离测试

---

# WP4：Alembic 严格化 + 最小 Repository（v1.9.2）

### Task 4.1：`alembic/env.py` URL 优先级

```text
-x url=... / SHINEHE_TEST_ALEMBIC_URL → alembic.ini → Config db path
```

### Task 4.2：严格 migration 测试

- 改 `tests/test_alembic_baseline.py`：普通失败 fail
- 新增 `tests/migrations/test_{empty_to_head,v1_9_to_head,upgrade_idempotent,interrupted_upgrade_recovery}.py`

### Task 4.3：写模式 Migration Head Gate

`src/storage/migration_status.py` + `startup_gate.py`：落后 head 拒绝写服务；只读诊断可启动。

### Task 4.4：最小 Repository

仅针对 WP2 触及的直接 SQL（如 Tag/Maintenance）。

### Task 4.5：CI Jobs

`architecture-closure` / `migration-gate` / `contract-gate`（见 Spec §12）。

---

# WP5：Legacy 删除与 v1.10.0（前置全满足后）

前置：Unified 默认已发布 ≥1 个正式版本；Shadow 门槛通过；无必须回滚 Legacy 的生产问题；WP2–WP4 完成。

删除项见 Spec §11.1；最终命令见 Spec §11.3：

```bash
python -m pytest tests/ -q
ruff check src tests evals tools scripts
python -m mypy src tools --ignore-missing-imports
python evals/run_retrieval_eval.py --all --fake-embedding --baseline evals/baselines/local.json --max-regression 0.05
python evals/run_hybrid_eval.py --strict
python evals/run_ask_e2e_eval.py --engine real-llm
python tools/report_closure_debt.py --strict
```

文档更新：`README*`、`PROGRESS.md`、`Claude.md`、architecture/*、`docs/migration/v1.9-to-v1.10-maintainability-closure.md`、`docs/release/v1.10.0-release-notes.md`。

---

## 3. Spec 覆盖自检

| Spec 要求 | Plan Task |
|---|---|
| WP0 基线 + debt 工具 | 0.1, 0.2 |
| Raw 算法入 RawRetriever | 1.1 |
| Fusion/Packaging | 1.2 |
| Policy 直接组合 | 1.3 |
| SearchService Facade + compatibility | 1.4 |
| Shadow 聚合门槛 | 1.5 |
| Unified 默认 | 1.6 |
| Answer 归位 | 2.1 |
| MCP 实拆 + Application + 无 SQL | 2.2–2.4 |
| Container Provider + 全局收束 | 3.x |
| Alembic 严格 + head gate + 最小 repo | 4.x |
| Legacy 删除 + strict debt | 5 |
| CI jobs | 4.5 |
| 首批仅 0.1/0.2/1.1 | 明确标注 |

**Placeholder scan:** 无 TBD；下一批任务给了明确文件/提交/验收，细节可在进入该批时按 1.1 粒度展开。

---

## 4. 首批完成后交付物

1. `docs/superpowers/reviews/maintainability-closure-baseline.md`
2. `tools/report_closure_debt.py` + 架构测试
3. 独立 `RawRetriever` + 相关测试绿
4. 阶段报告（是否允许进入 WP1-T2）：YES/NO + 原因
5. Commit(s)：`test(closure): ...` + `refactor(retrieval): move raw pipeline into RawRetriever`
