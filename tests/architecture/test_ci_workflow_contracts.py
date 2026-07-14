"""CI workflow static contracts — FIX-2/FIX-3 (v1.10.2 spec §7).

Asserts ``.github/workflows/ci.yml`` enforces the four public contracts
(Search/Ask/Wiki/MCP) in a dedicated gate, lints the whole repository
with ``ruff check .`` (covering Alembic), and runs the closure-debt
strict gate — without bypass patterns (``continue-on-error: true`` or
``|| true``).
"""
from __future__ import annotations

from pathlib import Path

CI_YML = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"


def _ci_text() -> str:
    assert CI_YML.exists(), f"missing CI workflow: {CI_YML}"
    return CI_YML.read_text(encoding="utf-8")


class TestCiWorkflowContracts:
    def test_contract_gate_runs_wiki_contract(self):
        """Wiki serving contract must be in the dedicated Contract Gate."""
        text = _ci_text()
        assert "tests/test_wiki_serving_contract.py" in text

    def test_contract_gate_runs_all_four_contracts(self):
        """Search/Ask/Wiki/MCP contracts must all be explicit in the gate."""
        text = _ci_text()
        for marker in (
            "tests/test_public_search_contract.py",
            "tests/test_public_ask_contract.py",
            "tests/test_wiki_serving_contract.py",
            "tests/test_mcp_contract.py",
        ):
            assert marker in text, f"contract gate missing {marker}"

    def test_lint_covers_whole_repository(self):
        """Ruff must lint the whole repo (incl. Alembic), not a subdir list."""
        text = _ci_text()
        assert "ruff check ." in text
        assert "ruff check src tests evals tools scripts" not in text

    def test_closure_debt_strict_gate_present(self):
        text = _ci_text()
        assert "python tools/report_closure_debt.py --strict" in text

    def test_no_continue_on_error_bypass(self):
        text = _ci_text()
        assert "continue-on-error: true" not in text

    def test_no_shell_or_true_bypass(self):
        text = _ci_text()
        assert "|| true" not in text
