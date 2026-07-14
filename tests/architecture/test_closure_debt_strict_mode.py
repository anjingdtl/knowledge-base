"""Negative tests: strict debt gate must fail when residual debt reappears (WP1-T3).

Builds a minimal pseudo-repo under tmp_path; never touches the real project tree
as --root for negative injection.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_DEBT_PATH = ROOT / "tools" / "report_closure_debt.py"


def _load_debt_mod():
    spec = importlib.util.spec_from_file_location("report_closure_debt", _DEBT_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _seed_clean_skeleton(base: Path) -> None:
    """Minimal tree that passes most metrics except intentionally injected debt."""
    (base / "src" / "mcp" / "tools").mkdir(parents=True)
    (base / "src" / "answering").mkdir(parents=True)
    (base / "src" / "retrieval").mkdir(parents=True)
    (base / "src" / "services").mkdir(parents=True)
    (base / "src" / "core").mkdir(parents=True)
    (base / "src" / "compatibility").mkdir(parents=True)
    (base / "alembic").mkdir(parents=True)
    (base / "tests").mkdir(parents=True)

    # Thin MCP server shell (no tool functions)
    (base / "src" / "mcp" / "server.py").write_text(
        '"""shell"""\n\ndef create_app():\n    return None\n',
        encoding="utf-8",
    )
    # Real tool impls so mcp_tools_real_impl_count > 0
    (base / "src" / "mcp" / "tools" / "retrieval.py").write_text(
        textwrap.dedent(
            '''
            """real tool module long enough for debt scanner."""

            def search(query: str) -> dict:
                return {"query": query, "hits": []}

            def ask(question: str) -> dict:
                return {"answer": question}

            def read(knowledge_id: str) -> dict:
                return {"id": knowledge_id}

            def list_knowledge() -> list:
                return []

            def index_path(path: str) -> dict:
                return {"path": path}

            def get_job(job_id: str) -> dict:
                return {"id": job_id}

            def list_jobs() -> list:
                return []

            def reindex_all() -> dict:
                return {"ok": True}

            def kb_capabilities() -> dict:
                return {}

            def ping() -> str:
                return "pong"
            '''
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    (base / "src" / "services" / "search_service.py").write_text(
        "class SearchService:\n    def execute(self):\n        pass\n",
        encoding="utf-8",
    )
    (base / "src" / "retrieval" / "raw_retriever.py").write_text(
        "class RawRetriever:\n    pass\n",
        encoding="utf-8",
    )
    (base / "src" / "answering" / "service.py").write_text(
        "class AnswerService:\n    pass\n",
        encoding="utf-8",
    )
    (base / "alembic" / "env.py").write_text(
        'url = "sqlite:///"\n# SHINEHE_TEST_ALEMBIC_URL honored\n',
        encoding="utf-8",
    )
    (base / "tests" / "test_alembic_baseline.py").write_text(
        "def test_alembic_ok():\n    assert True\n",
        encoding="utf-8",
    )
    # Whitelist-compatible infra files (empty-ish)
    (base / "src" / "services" / "db.py").write_text(
        "class Database:\n    _instance = None\n",
        encoding="utf-8",
    )
    (base / "src" / "core" / "container.py").write_text(
        "def get_active_container():\n    return None\n",
        encoding="utf-8",
    )
    (base / "src" / "compatibility" / "container_access.py").write_text(
        "def get_active_container():\n    return None\n",
        encoding="utf-8",
    )
    (base / "src" / "mcp" / "runtime.py").write_text(
        "def lifespan():\n    pass\n",
        encoding="utf-8",
    )


def _run_strict(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_DEBT_PATH), "--strict", "--root", str(root)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_strict_fails_on_database_instance_outside_whitelist(tmp_path: Path):
    _seed_clean_skeleton(tmp_path)
    bad = tmp_path / "src" / "services" / "evil_db_user.py"
    bad.write_text(
        "from somewhere import X\n"
        "def f():\n"
        "    return Database._instance\n",
        encoding="utf-8",
    )
    mod = _load_debt_mod()
    m = mod.collect_debt_metrics(tmp_path)
    assert m["database_instance_refs_outside_infra"] > 0
    fails = mod.validate_strict_metrics(m)
    assert any("Database._instance outside infra" in f for f in fails)
    proc = _run_strict(tmp_path)
    assert proc.returncode != 0


def test_strict_fails_on_get_active_container_outside_whitelist(tmp_path: Path):
    _seed_clean_skeleton(tmp_path)
    bad = tmp_path / "src" / "services" / "evil_gac_user.py"
    bad.write_text(
        "def f():\n"
        "    from src.core.container import get_active_container\n"
        "    return get_active_container()\n",
        encoding="utf-8",
    )
    mod = _load_debt_mod()
    m = mod.collect_debt_metrics(tmp_path)
    assert m["get_active_container_refs_outside_whitelist"] > 0
    fails = mod.validate_strict_metrics(m)
    assert any("get_active_container outside whitelist" in f for f in fails)
    proc = _run_strict(tmp_path)
    assert proc.returncode != 0


def test_strict_fails_on_legacy_search_pipeline(tmp_path: Path):
    _seed_clean_skeleton(tmp_path)
    (tmp_path / "src" / "services" / "search_service.py").write_text(
        "class SearchService:\n"
        "    def _search_legacy_pipeline(self):\n"
        "        pass\n",
        encoding="utf-8",
    )
    mod = _load_debt_mod()
    m = mod.collect_debt_metrics(tmp_path)
    assert m["search_service_has_legacy_pipeline"] is True
    fails = mod.validate_strict_metrics(m)
    assert any("_search_legacy_pipeline" in f for f in fails)
    proc = _run_strict(tmp_path)
    assert proc.returncode != 0


def test_strict_fails_on_mcp_server_tool_functions(tmp_path: Path):
    _seed_clean_skeleton(tmp_path)
    (tmp_path / "src" / "mcp" / "server.py").write_text(
        textwrap.dedent(
            '''
            class _M:
                def tool(self, f=None):
                    def deco(fn):
                        return fn
                    return deco if f is None else deco(f)

            mcp = _M()

            @mcp.tool
            def search(query: str) -> dict:
                return {}
            '''
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    mod = _load_debt_mod()
    m = mod.collect_debt_metrics(tmp_path)
    assert m["mcp_server_tool_functions"] > 0
    fails = mod.validate_strict_metrics(m)
    assert any("tool functions" in f for f in fails)
    proc = _run_strict(tmp_path)
    assert proc.returncode != 0


def test_strict_fails_on_alembic_test_pytest_skip(tmp_path: Path):
    _seed_clean_skeleton(tmp_path)
    (tmp_path / "tests" / "test_alembic_baseline.py").write_text(
        "import pytest\n\n"
        "def test_skip_path():\n"
        "    pytest.skip('soft skip not allowed')\n",
        encoding="utf-8",
    )
    mod = _load_debt_mod()
    m = mod.collect_debt_metrics(tmp_path)
    assert m["migration_tests_have_skip_paths"] is True
    fails = mod.validate_strict_metrics(m)
    assert any("soft-skip" in f for f in fails)
    proc = _run_strict(tmp_path)
    assert proc.returncode != 0


def test_strict_clean_skeleton_passes(tmp_path: Path):
    """Control: seeded skeleton without injected debt is strict-clean."""
    _seed_clean_skeleton(tmp_path)
    mod = _load_debt_mod()
    m = mod.collect_debt_metrics(tmp_path)
    assert mod.validate_strict_metrics(m) == []
    proc = _run_strict(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
