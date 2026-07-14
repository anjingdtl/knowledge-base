#!/usr/bin/env python3
"""Report maintainability-closure architecture debt metrics.

Usage:
  python tools/report_closure_debt.py
  python tools/report_closure_debt.py --json
  python tools/report_closure_debt.py --strict   # WP5: exit 1 if residual debt
"""
from __future__ import annotations

import argparse
import ast
import json
import re
from pathlib import Path
from typing import Any


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _count_regex(text: str, pattern: str) -> int:
    return len(re.findall(pattern, text))


def _count_tool_functions_in_server(text: str) -> int:
    """Heuristic: FastMCP tool handlers defined in server.py."""
    if not text.strip():
        return 0
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return 0
    count = 0
    known_tools = {
        "ping",
        "search",
        "ask",
        "read",
        "list_knowledge",
        "index_path",
        "get_job",
        "list_jobs",
        "reindex_all",
        "kb_capabilities",
    }
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        name = node.name
        if name.startswith("_"):
            continue
        if name in {"main", "create_app", "lifespan"}:
            continue
        has_tool_decorator = False
        for d in node.decorator_list:
            if isinstance(d, ast.Attribute) and d.attr == "tool":
                has_tool_decorator = True
            elif isinstance(d, ast.Call):
                func = d.func
                if isinstance(func, ast.Attribute) and func.attr == "tool":
                    has_tool_decorator = True
                elif isinstance(func, ast.Name) and func.id in {"tool", "mcp_tool"}:
                    has_tool_decorator = True
        if has_tool_decorator or name in known_tools:
            count += 1
    return count


def _count_real_tool_impls(tools_dir: Path) -> int:
    """Count non-trivial public function defs in tools/*.py (skip name-only modules)."""
    total = 0
    if not tools_dir.is_dir():
        return 0
    for path in tools_dir.glob("*.py"):
        if path.name == "__init__.py":
            continue
        text = _read(path)
        # Name-only modules are tiny or only assign name lists
        if len(text.strip()) < 400:
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
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

    line_count = 0
    if server_text:
        line_count = server_text.count("\n")
        if not server_text.endswith("\n"):
            line_count += 1

    metrics: dict[str, Any] = {
        "mcp_server_lines": line_count,
        "mcp_server_tool_functions": (
            _count_tool_functions_in_server(server_text) if server_text else 0
        ),
        "mcp_tools_real_impl_count": _count_real_tool_impls(tools_dir),
        "database_instance_refs_src": db_instance_refs,
        "get_active_container_refs_src": gac_refs,
        "search_service_has_legacy_pipeline": "def _search_legacy_pipeline" in search_text,
        "search_service_has_verified_hybrid": "def _search_verified_hybrid" in search_text,
        "raw_retriever_calls_search_service": (
            "from src.services.search_service" in raw_text
            or "run_raw_retrieval_adapter" in raw_text
            or "self._svc" in raw_text
            or "search_service:" in raw_text
            or "search_service =" in raw_text
        ),
        "answering_depends_on_verified_answer": answering_dep,
        "alembic_env_reads_test_url": "SHINEHE_TEST_ALEMBIC_URL" in alembic_text,
        "migration_tests_have_skip_paths": "pytest.skip" in alembic_test_text,
    }
    return metrics


def _strict_failures(metrics: dict[str, Any]) -> list[str]:
    fails: list[str] = []
    if metrics["mcp_server_lines"] > 500:
        fails.append(f"mcp_server_lines={metrics['mcp_server_lines']} > 500")
    if metrics["mcp_server_tool_functions"] > 0:
        fails.append("mcp_server still defines tool functions")
    if metrics["mcp_tools_real_impl_count"] <= 0:
        fails.append("mcp tools have no real implementations")
    if metrics["database_instance_refs_src"] > 0:
        fails.append(
            f"Database._instance refs={metrics['database_instance_refs_src']}"
        )
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
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
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
