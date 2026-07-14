"""Retrieval-domain MCP tools (protocol adapters only).

Implementations live in ``src.mcp.server`` during the Phase-3 transition;
this module documents the domain boundary for architecture tests.
"""
from src.mcp.tools import RETRIEVAL_TOOLS

DOMAIN = "retrieval"
TOOL_NAMES = RETRIEVAL_TOOLS
