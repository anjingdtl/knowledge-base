"""kb_capabilities hidden_by_policy capability state — FIX-1 (v1.10.2 spec §6).

Asserts ``kb_capabilities()`` exposes the real
``RegistrationState.hidden_by_policy`` instead of a fixed empty list.

Covered cases (spec minimum = 3):
  * nothing hidden by policy        -> []
  * policy hides tools              -> sorted(real hidden names)
  * bootstrap not run (STATE None)  -> []
plus type and sort-order invariants.
"""
from __future__ import annotations

import pytest

from src.mcp import registration
from src.mcp.registration import RegistrationState
from src.mcp.tool_registry import get_definitions, list_hidden_by_policy
from src.mcp_server import kb_capabilities


def _ensure_defs() -> None:
    """Import mcp_server so tool definitions register; idempotent."""
    if not get_definitions():
        import src.mcp_server  # noqa: F401


@pytest.fixture
def isolated_state():
    """Save/restore registration.STATE so tests cannot leak global state.

    conftest.setup_db (autouse) resets Database/singletons but not
    registration.STATE, so we manage it locally.
    """
    saved = registration.STATE
    registration.STATE = None
    try:
        yield
    finally:
        registration.STATE = saved


def _payload() -> dict:
    result = kb_capabilities()
    assert result.get("ok") is True, result
    return result["data"]


def _make_state(*, hidden: list[str], profile: str = "extended") -> RegistrationState:
    return RegistrationState(
        profile=profile,
        experimental_enabled=False,
        aliases_enabled=False,
        visible_tool_names=set(get_definitions().keys()),
        registered_aliases={},
        hidden_by_policy=hidden,
        effective_settings=None,
    )


class TestKbCapabilitiesHiddenByPolicy:
    def test_field_is_always_list(self, isolated_state):
        _ensure_defs()
        payload = _payload()
        assert isinstance(payload["hidden_by_policy"], list)

    def test_empty_when_nothing_hidden_by_policy(self, isolated_state):
        """Bootstrap ran but policy hides nothing -> empty list."""
        _ensure_defs()
        registration.STATE = _make_state(hidden=[])
        payload = _payload()
        assert payload["hidden_by_policy"] == []

    def test_reflects_real_hidden_tools(self, isolated_state):
        """core + write_policy=disabled hides index_path / reindex_all."""
        _ensure_defs()
        expected = list_hidden_by_policy("core", write_policy="disabled")
        assert expected, "precondition: core+disabled must hide tools"
        registration.STATE = _make_state(hidden=expected, profile="core")
        payload = _payload()
        assert payload["hidden_by_policy"] == sorted(expected)
        assert isinstance(payload["hidden_by_policy"], list)

    def test_unsorted_state_returned_sorted(self, isolated_state):
        """Spec target: ``sorted(state.hidden_by_policy)``."""
        _ensure_defs()
        registration.STATE = _make_state(
            hidden=["reindex_all", "index_path"], profile="core"
        )
        payload = _payload()
        assert payload["hidden_by_policy"] == ["index_path", "reindex_all"]

    def test_empty_when_not_bootstrapped(self, isolated_state):
        """STATE is None (bootstrap not run) -> empty list, never error."""
        _ensure_defs()
        # isolated_state already set STATE = None
        payload = _payload()
        assert payload["hidden_by_policy"] == []
        assert isinstance(payload["hidden_by_policy"], list)
