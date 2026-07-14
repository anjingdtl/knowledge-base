"""ShineHeKnowledge MCP Server — compatibility entrypoint (Phase-3).

Implementation lives in ``src.mcp.server``. This module is a **module alias**
so that tests and launchers that import ``src.mcp_server`` share the same
object graph (including ``_container`` patches).
"""
from __future__ import annotations

import sys

import src.mcp.server as _server

# Ensure server exposes main for Spec compatibility
if not hasattr(_server, "main"):
    def main(argv=None):
        from src.mcp_cli import main as _cli_main

        return _cli_main(argv)

    _server.main = main  # type: ignore[attr-defined]

# Alias: any attribute get/set on mcp_server hits the same module as mcp.server
sys.modules[__name__] = _server
