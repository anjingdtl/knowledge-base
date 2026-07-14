"""Architecture debt baseline for maintainability closure.

WP1-T2: strict zero assertions for residual debt gates.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_DEBT_PATH = ROOT / "tools" / "report_closure_debt.py"
_spec = importlib.util.spec_from_file_location("report_closure_debt", _DEBT_PATH)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
collect_debt_metrics = _mod.collect_debt_metrics
validate_strict_metrics = _mod.validate_strict_metrics
_strict_failures = _mod._strict_failures

REQUIRED_KEYS = {
    "mcp_server_lines",
    "mcp_server_tool_functions",
    "mcp_tools_real_impl_count",
    "database_instance_refs_src",
    "database_instance_refs_outside_infra",
    "get_active_container_refs_src",
    "get_active_container_refs_outside_whitelist",
    "search_service_has_legacy_pipeline",
    "search_service_has_verified_hybrid",
    "raw_retriever_calls_search_service",
    "answering_depends_on_verified_answer",
    "alembic_env_reads_test_url",
    "migration_tests_have_skip_paths",
    "database_runtime_executes_schema",
    "database_runtime_calls_migrate",
    "container_gate_after_database_open",
    "allow_unstamped_default_true",
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
        "database_instance_refs_outside_infra",
        "get_active_container_refs_src",
        "get_active_container_refs_outside_whitelist",
    ):
        assert isinstance(metrics[key], int)
        assert metrics[key] >= 0


def test_baseline_reflects_current_debt_shape():
    """WP5/WP1 closed shape: no legacy pipelines; server shell only; zeros enforced."""
    m = collect_debt_metrics(ROOT)
    # Spec budget: server.py <= 500 lines (registration shell only)
    assert m["mcp_server_lines"] <= 500
    assert m["mcp_server_tool_functions"] == 0
    assert m["mcp_tools_real_impl_count"] >= 40
    assert m["search_service_has_legacy_pipeline"] is False
    assert m["search_service_has_verified_hybrid"] is False
    assert m["raw_retriever_calls_search_service"] is False
    assert m["answering_depends_on_verified_answer"] is False
    # WP4-T1: alembic env honors SHINEHE_TEST_ALEMBIC_URL; tests are strict
    assert m["alembic_env_reads_test_url"] is True
    assert m["migration_tests_have_skip_paths"] is False
    # WP1-T2: force zero residual for singleton/container access outside whitelist
    assert m["database_instance_refs_outside_infra"] == 0
    assert m["get_active_container_refs_outside_whitelist"] == 0
    # WP5: runtime schema authority removed
    assert m["database_runtime_executes_schema"] is False
    assert m["database_runtime_calls_migrate"] is False
    assert m["container_gate_after_database_open"] is False
    assert m["allow_unstamped_default_true"] is False
    assert _strict_failures(m) == []
    assert validate_strict_metrics(m) == []
