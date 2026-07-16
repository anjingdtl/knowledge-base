from __future__ import annotations

import inspect

from src.mcp.tools import retrieval


def test_ask_outer_deadline_declares_cooperative_thread_mode() -> None:
    source = inspect.getsource(retrieval._do_ask)
    assert 'isolate="thread"' in source


def test_ask_generation_routes_through_provider_operation() -> None:
    from src.services import llm

    source = inspect.getsource(llm.LLMService.chat_with_usage)
    assert "run_provider_operation" in source or "_run_generate" in source

